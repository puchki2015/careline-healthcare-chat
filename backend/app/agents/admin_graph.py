"""LangGraph administrator workflow for confirmed patient email invitations."""

import logging
from collections.abc import Sequence
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agents.admin_classifier import AdminChatClassifier
from app.agents.admin_pending_actions import (
    consume_invitation_action,
    create_invitation_action,
)
from app.repositories.admin_chat_repository import AdminChatRepository
from app.schemas.admin import (
    AdminChatAction,
    AdminChatDecision,
    AdminChatIntent,
    AdminChatRequest,
    AdminChatResponse,
    AdminPendingActionDraft,
    CurrentAdmin,
    PatientInvitationDetails,
)
from app.schemas.chat import ChatToolTrace
from app.schemas.chat_history import ChatContextMessage, ChatSessionMemoryRecord
from app.services.admin_service import invite_patient

logger = logging.getLogger(__name__)


class AdminChatUnderstandingError(ValueError):
    """Raised when extracted invitation data fails deterministic validation."""


class AdminGraphState(TypedDict, total=False):
    request: AdminChatRequest
    decision: AdminChatDecision
    response: AdminChatResponse
    traces: list[ChatToolTrace]
    pending_action: AdminPendingActionDraft


FIELD_LABELS = {
    "full_name": "full name",
    "date_of_birth": "date of birth (YYYY-MM-DD)",
    "phone": "phone number with country code",
    "email": "email address",
}


def build_admin_chat_graph(
    *,
    classifier: AdminChatClassifier,
    current_admin: CurrentAdmin,
    repository: AdminChatRepository,
    invitation_redirect_url: str,
    pending_action_ttl_seconds: int,
    context: Sequence[ChatContextMessage] = (),
    memory: Sequence[ChatSessionMemoryRecord] = (),
):
    """Compile an admin-only graph; authorization occurs before construction."""

    def classify_message(state: AdminGraphState) -> dict[str, object]:
        message = state["request"].message
        if message is None:
            raise AdminChatUnderstandingError("Admin chat message is missing")
        started_at = perf_counter()
        decision = classifier.classify(message, context, memory)
        return {
            "decision": decision,
            "traces": [
                ChatToolTrace(
                    node_name="classify_message",
                    tool_name="admin_chat_model.classify",
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                    result={
                        "intent": decision.intent.value,
                        "missing_field_count": len(decision.missing_fields),
                    },
                )
            ],
        }

    def route_input(state: AdminGraphState) -> str:
        return "confirmation" if state["request"].confirmation is not None else "message"

    def route_decision(state: AdminGraphState) -> str:
        return state["decision"].intent.value

    def create_patient(state: AdminGraphState) -> dict[str, object]:
        """Collect fields or create a confirmation proposal; never invite directly."""

        decision = state["decision"]
        values = {
            "full_name": decision.full_name,
            "date_of_birth": decision.date_of_birth,
            "phone": decision.phone,
            "email": decision.email,
        }
        actually_missing = [key for key, value in values.items() if value is None]
        missing = list(dict.fromkeys([*decision.missing_fields, *actually_missing]))
        if missing:
            labels = [FIELD_LABELS[key] for key in missing]
            return {
                "response": AdminChatResponse(
                    action=AdminChatAction.INFORMATION_REQUIRED,
                    message="Please provide the following patient information: "
                    + ", ".join(labels)
                    + ".",
                    data={"missing_fields": missing},
                ),
                "traces": [
                    *state.get("traces", []),
                    ChatToolTrace(
                        node_name="createPatient",
                        tool_name="patient_invitation.collect_fields",
                        duration_ms=0,
                        arguments={"required_field_count": 4},
                        result={"missing_field_count": len(missing)},
                    ),
                ],
            }

        try:
            details = PatientInvitationDetails.model_validate(values)
        except ValidationError as exc:
            logger.warning(
                "admin_patient_invitation_validation_failed",
                extra={"validation_error_count": exc.error_count()},
            )
            raise AdminChatUnderstandingError("Patient invitation details are invalid") from exc

        started_at = perf_counter()
        token, pending = create_invitation_action(
            details,
            ttl_seconds=pending_action_ttl_seconds,
        )
        return {
            "response": AdminChatResponse(
                action=AdminChatAction.CONFIRMATION_REQUIRED,
                message="Please confirm the patient information before the invitation is sent.",
                requires_confirmation=True,
                pending_action_id=token,
                data={"proposed_patient": details.model_dump(mode="json")},
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="createPatient",
                    tool_name="patient_invitation.propose",
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                    arguments={"required_field_count": 4},
                    result={"confirmation_required": True},
                ),
            ],
            "pending_action": pending,
        }

    def handle_confirmation(state: AdminGraphState) -> dict[str, object]:
        confirmation = state["request"].confirmation
        if confirmation is None:
            raise AdminChatUnderstandingError("Invitation confirmation is missing")
        started_at = perf_counter()
        details = consume_invitation_action(
            repository,
            confirmation.action_id,
            current_admin.id,
            state["request"].session_id,
            decision="approved" if confirmation.approved else "rejected",
        )
        if not confirmation.approved:
            return {
                "response": AdminChatResponse(
                    action=AdminChatAction.CANCELLED,
                    message="The patient invitation was cancelled. No account was created.",
                ),
                "traces": [
                    ChatToolTrace(
                        node_name="handle_confirmation",
                        tool_name="admin_pending_action.consume",
                        duration_ms=round((perf_counter() - started_at) * 1000, 2),
                        arguments={"approved": False},
                        result={"status": "cancelled"},
                    )
                ],
            }

        invited_patient_id = invite_patient(
            repository, details, redirect_to=invitation_redirect_url
        )
        # The invitation itself is the authoritative external side effect. Link
        # the conversation to the new patient for administrative audit/search;
        # a link failure is logged but must not falsely report that email failed.
        try:
            repository.update_session(
                str(current_admin.id),
                str(state["request"].session_id),
                {"subject_patient_id": str(invited_patient_id)},
                expected_status="active",
            )
        except Exception as exc:
            logger.error(
                "admin_chat_subject_link_failed",
                extra={"error_type": type(exc).__name__},
            )
        return {
            "response": AdminChatResponse(
                action=AdminChatAction.PATIENT_INVITED,
                message="The patient invitation was sent successfully.",
                data={"status": "invitation_sent"},
            ),
            "traces": [
                ChatToolTrace(
                    node_name="handle_confirmation",
                    tool_name="supabase_auth.invite_patient",
                    duration_ms=round((perf_counter() - started_at) * 1000, 2),
                    arguments={"approved": True, "field_count": 4},
                    result={"status": "invitation_sent"},
                )
            ],
        }

    def unsupported(state: AdminGraphState) -> dict[str, object]:
        return {
            "response": AdminChatResponse(
                action=AdminChatAction.UNSUPPORTED,
                message="I can currently help an administrator invite a new patient.",
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="unsupported",
                    tool_name="admin_policy.boundary_response",
                    duration_ms=0,
                    result={"status": "unsupported"},
                ),
            ],
        }

    graph = StateGraph(AdminGraphState)
    graph.add_node("classify_message", classify_message)
    graph.add_node("createPatient", create_patient)
    graph.add_node("handle_confirmation", handle_confirmation)
    graph.add_node("unsupported", unsupported)
    graph.add_conditional_edges(
        START,
        route_input,
        {"message": "classify_message", "confirmation": "handle_confirmation"},
    )
    graph.add_conditional_edges(
        "classify_message",
        route_decision,
        {
            AdminChatIntent.CREATE_PATIENT.value: "createPatient",
            AdminChatIntent.UNSUPPORTED.value: "unsupported",
        },
    )
    graph.add_edge("createPatient", END)
    graph.add_edge("handle_confirmation", END)
    graph.add_edge("unsupported", END)
    return graph.compile()
