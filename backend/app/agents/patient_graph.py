"""LangGraph workflow for safe patient-profile questions and updates."""

import json
import logging
from collections.abc import Sequence
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from supabase import Client

from app.agents.appointment_classifier import AppointmentClassifier
from app.agents.appointment_graph import build_appointment_graph
from app.agents.classifier import ChatClassifier
from app.agents.pending_actions import (
    consume_patient_action,
    create_pending_action_draft,
)
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.chat_history_repository import ChatHistoryRepository
from app.schemas.appointment import AppointmentActionPayload
from app.schemas.auth import CurrentUser
from app.schemas.chat import (
    ChatAction,
    ChatDecision,
    ChatIntent,
    ChatPendingActionDraft,
    ChatRequest,
    ChatResponse,
    ChatToolTrace,
)
from app.schemas.chat_history import ChatContextMessage, ChatSessionMemoryRecord
from app.schemas.patient import READABLE_FIELD_ORDER, READABLE_FIELDS, validate_attribute_value
from app.services.appointment_service import execute_confirmed_action
from app.services.patient_service import (
    PatientAttributeValidationError,
    get_patient,
    update_patient_attribute,
)

logger = logging.getLogger(__name__)


class ChatUnderstandingError(ValueError):
    """Raised when a model suggestion fails deterministic safety checks."""


class ChatGraphState(TypedDict, total=False):
    """Only request, routing decision and final response move between nodes."""

    request: ChatRequest
    decision: ChatDecision
    response: ChatResponse
    traces: list[ChatToolTrace]
    pending_action: ChatPendingActionDraft


def build_patient_chat_graph(
    *,
    classifier: ChatClassifier,
    current_user: CurrentUser,
    patient_client: Client,
    history_repository: ChatHistoryRepository,
    pending_action_ttl_seconds: int,
    context: Sequence[ChatContextMessage] = (),
    memory: Sequence[ChatSessionMemoryRecord] = (),
    appointment_classifier: AppointmentClassifier | None = None,
    appointment_repository: AppointmentRepository | None = None,
):
    """Compile a request-scoped graph with trusted dependencies in closures.

    The JWT-derived identity and Supabase client are deliberately not part of
    graph state or model input. The model can suggest an intent, but only these
    deterministic nodes can invoke patient services.
    """

    def classify_message(state: ChatGraphState) -> dict[str, object]:
        message = state["request"].message
        if message is None:  # Protected by ChatRequest; retained as defense in depth.
            raise ChatUnderstandingError("Message is missing")
        started_at = perf_counter()
        decision = classifier.classify(message, context, memory)
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        logger.info("chat_intent_classified", extra={"chat_intent": decision.intent.value})
        return {
            "decision": decision,
            "traces": [
                ChatToolTrace(
                    node_name="classify_message",
                    tool_name="chat_model.classify",
                    duration_ms=duration_ms,
                    result={"intent": decision.intent.value},
                )
            ],
        }

    def route_input(state: ChatGraphState) -> str:
        return "confirmation" if state["request"].confirmation is not None else "message"

    def route_decision(state: ChatGraphState) -> str:
        return state["decision"].intent.value

    def read_profile(state: ChatGraphState) -> dict[str, object]:
        requested = state["decision"].fields
        if requested:
            # Validate the model's suggestion against the same hard whitelist
            # used by REST. Duplicate names are removed without changing order.
            fields = tuple(dict.fromkeys(requested))
            if any(field not in READABLE_FIELDS for field in fields):
                raise ChatUnderstandingError("The requested profile fields were not understood")
        else:
            fields = READABLE_FIELD_ORDER

        started_at = perf_counter()
        profile = get_patient(patient_client, current_user.id, fields)
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        data = profile.model_dump(mode="json", exclude_unset=True)
        return {
            "response": ChatResponse(
                action=ChatAction.ANSWER,
                message="Here are the requested details from your patient profile.",
                data=data,
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="read_profile",
                    tool_name="patient_profile.read",
                    duration_ms=duration_ms,
                    arguments={"fields": list(fields)},
                    result={"field_count": len(data)},
                ),
            ],
        }

    def propose_update(state: ChatGraphState) -> dict[str, object]:
        decision = state["decision"]
        if decision.key is None:
            raise ChatUnderstandingError("The profile field to update was not understood")

        proposed_value: object = decision.value
        if decision.key in {"address", "emergency_contact"} and decision.value is not None:
            # Structured Outputs represents complex update values as a JSON
            # string so the provider schema remains strict and portable. Parse
            # it only inside this trusted node, then use the existing patient
            # validators to enforce the exact object shape.
            try:
                proposed_value = json.loads(decision.value)
            except json.JSONDecodeError as exc:
                raise ChatUnderstandingError(
                    "The structured profile value was not understood"
                ) from exc
            if not isinstance(proposed_value, dict):
                raise ChatUnderstandingError("The structured profile value must be an object")
        try:
            normalized = validate_attribute_value(decision.key, proposed_value)
        except ValidationError as exc:
            raise PatientAttributeValidationError(decision.key, exc.error_count()) from exc

        started_at = perf_counter()
        action_id, pending_action = create_pending_action_draft(
            decision.key,
            normalized,
            ttl_seconds=pending_action_ttl_seconds,
        )
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        return {
            "response": ChatResponse(
                action=ChatAction.CONFIRMATION_REQUIRED,
                message="Please confirm the proposed profile change before it is saved.",
                requires_confirmation=True,
                pending_action_id=action_id,
                data={"proposed_change": {"key": decision.key, "value": normalized}},
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="propose_update",
                    tool_name="pending_action.create",
                    duration_ms=duration_ms,
                    arguments={"attribute_key": decision.key},
                    result={"confirmation_required": True},
                ),
            ],
            "pending_action": pending_action,
        }

    def manage_appointment(state: ChatGraphState) -> dict[str, object]:
        """Hand off appointment language to a dedicated specialist subgraph."""

        message = state["request"].message
        if message is None:
            raise ChatUnderstandingError("Message is missing")
        if appointment_classifier is None or appointment_repository is None:
            raise ChatUnderstandingError("Appointment coordinator is unavailable")
        specialist = build_appointment_graph(
            classifier=appointment_classifier,
            repository=appointment_repository,
            patient_id=current_user.id,
            pending_action_ttl_seconds=pending_action_ttl_seconds,
            context=context,
            timezone_name=state["request"].timezone,
        )
        result = specialist.invoke({"message": message})
        return {
            "response": result["response"],
            "traces": [*state.get("traces", []), *result.get("traces", [])],
            **(
                {"pending_action": result["pending_action"]}
                if result.get("pending_action") is not None
                else {}
            ),
        }

    def handle_confirmation(state: ChatGraphState) -> dict[str, object]:
        confirmation = state["request"].confirmation
        if confirmation is None:  # Protected by graph routing.
            raise ChatUnderstandingError("Confirmation is missing")

        # Consuming first makes approval and cancellation one-time operations.
        started_at = perf_counter()
        pending = consume_patient_action(
            history_repository,
            confirmation.action_id,
            current_user.id,
            state["request"].session_id,
            decision="approved" if confirmation.approved else "rejected",
        )
        if not confirmation.approved:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            logger.info("chat_action_cancelled", extra={"action_type": pending.action_type})
            return {
                "response": ChatResponse(
                    action=ChatAction.CANCELLED,
                    message="The proposed action was cancelled. Nothing was changed.",
                ),
                "traces": [
                    ChatToolTrace(
                        node_name="handle_confirmation",
                        tool_name="pending_action.consume",
                        duration_ms=duration_ms,
                        arguments={"approved": False},
                        result={"status": "cancelled"},
                    )
                ],
            }

        if pending.action_type == "update_profile":
            key = pending.payload.get("key")
            if not isinstance(key, str) or "value" not in pending.payload:
                raise ChatUnderstandingError("The stored profile action is invalid")
            updated = update_patient_attribute(
                patient_client,
                current_user.id,
                key,
                pending.payload["value"],
            )
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            return {
                "response": ChatResponse(
                    action=ChatAction.UPDATED,
                    message="Your confirmed profile change was saved.",
                    data=updated.model_dump(mode="json"),
                ),
                "traces": [
                    ChatToolTrace(
                        node_name="handle_confirmation",
                        tool_name="patient_profile.update",
                        duration_ms=duration_ms,
                        arguments={"attribute_key": key, "approved": True},
                        result={"status": "updated"},
                    )
                ],
            }

        try:
            appointment_payload = AppointmentActionPayload.model_validate(pending.payload)
        except ValidationError as exc:
            raise ChatUnderstandingError("The stored appointment action is invalid") from exc
        if appointment_repository is None:
            raise ChatUnderstandingError("Appointment coordinator is unavailable")
        mutation = execute_confirmed_action(
            appointment_repository, current_user.id, appointment_payload
        )
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        action_by_outcome = {
            "booked": ChatAction.APPOINTMENT_BOOKED,
            "rescheduled": ChatAction.APPOINTMENT_RESCHEDULED,
            "cancelled": ChatAction.APPOINTMENT_CANCELLED,
        }
        return {
            "response": ChatResponse(
                action=action_by_outcome[mutation.outcome],
                message=f"Your appointment was successfully {mutation.outcome}.",
                data=mutation.model_dump(mode="json"),
            ),
            "traces": [
                ChatToolTrace(
                    node_name="handle_confirmation",
                    tool_name=f"appointment.{appointment_payload.operation}",
                    duration_ms=duration_ms,
                    arguments={"approved": True},
                    result={"status": mutation.outcome},
                )
            ],
        }

    def unsupported(state: ChatGraphState) -> dict[str, object]:
        return {
            "response": ChatResponse(
                action=ChatAction.UNSUPPORTED,
                message=(
                    "I can currently read your patient profile or propose a change to one "
                    "editable profile field. I can also manage your appointments. I cannot "
                    "provide medical advice or delete accounts."
                ),
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="unsupported",
                    tool_name="policy.boundary_response",
                    duration_ms=0,
                    result={"status": "unsupported"},
                ),
            ],
        }

    graph = StateGraph(ChatGraphState)
    graph.add_node("classify_message", classify_message)
    graph.add_node("read_profile", read_profile)
    graph.add_node("propose_update", propose_update)
    graph.add_node("manage_appointment", manage_appointment)
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
            ChatIntent.READ_PROFILE.value: "read_profile",
            ChatIntent.UPDATE_PROFILE.value: "propose_update",
            ChatIntent.MANAGE_APPOINTMENT.value: "manage_appointment",
            ChatIntent.UNSUPPORTED.value: "unsupported",
        },
    )
    graph.add_edge("read_profile", END)
    graph.add_edge("propose_update", END)
    graph.add_edge("manage_appointment", END)
    graph.add_edge("handle_confirmation", END)
    graph.add_edge("unsupported", END)
    return graph.compile()
