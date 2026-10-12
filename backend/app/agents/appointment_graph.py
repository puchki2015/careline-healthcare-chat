"""LangGraph subgraph for appointment discovery and mutation proposals."""

import logging
from collections.abc import Sequence
from time import perf_counter
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.agents.appointment_classifier import AppointmentClassifier
from app.agents.pending_actions import create_appointment_action_draft
from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.appointment import AppointmentActionPayload, AppointmentDecision, AppointmentIntent
from app.schemas.chat import ChatAction, ChatPendingActionDraft, ChatResponse, ChatToolTrace
from app.schemas.chat_history import ChatContextMessage
from app.services.appointment_date_service import (
    AppointmentDateResolutionError,
    resolve_appointment_date_range,
)
from app.services.appointment_service import (
    get_available_slot,
    list_appointments,
    search_availability,
)

logger = logging.getLogger(__name__)


class AppointmentUnderstandingError(ValueError):
    """The model output is incomplete or fails deterministic validation."""


class AppointmentGraphState(TypedDict, total=False):
    message: str
    decision: AppointmentDecision
    response: ChatResponse
    traces: list[ChatToolTrace]
    pending_action: ChatPendingActionDraft


def _uuid(value: str | None, label: str) -> UUID:
    try:
        return UUID(value) if value else (_ for _ in ()).throw(ValueError())
    except ValueError as exc:
        raise AppointmentUnderstandingError(f"A valid {label} is required") from exc


def build_appointment_graph(
    *,
    classifier: AppointmentClassifier,
    repository: AppointmentRepository,
    patient_id: UUID,
    pending_action_ttl_seconds: int,
    context: Sequence[ChatContextMessage] = (),
    timezone_name: str = "UTC",
    now_provider=None,
):
    """Compile the appointment specialist with deterministic database tools."""

    def classify(state: AppointmentGraphState) -> dict[str, object]:
        started = perf_counter()
        decision = classifier.classify(state["message"], context)
        return {
            "decision": decision,
            "traces": [
                ChatToolTrace(
                    node_name="appointment.classify_intent",
                    tool_name="appointment_model.classify",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    result={"intent": decision.intent.value},
                )
            ],
        }

    def route(state: AppointmentGraphState) -> str:
        return state["decision"].intent.value

    def information_required(state: AppointmentGraphState, message: str) -> dict[str, object]:
        return {
            "response": ChatResponse(
                action=ChatAction.INFORMATION_REQUIRED,
                message=message,
                data={"missing_fields": state["decision"].missing_fields},
            ),
            "traces": state.get("traces", []),
        }

    def list_node(state: AppointmentGraphState) -> dict[str, object]:
        started = perf_counter()
        items = list_appointments(repository, patient_id)
        return {
            "response": ChatResponse(
                action=ChatAction.ANSWER,
                message=(
                    "Here are your upcoming appointments."
                    if items
                    else "You do not have any upcoming appointments."
                ),
                data={"appointments": [item.model_dump(mode="json") for item in items]},
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="appointment.listAppointments",
                    tool_name="appointment.list",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    result={"appointment_count": len(items)},
                ),
            ],
        }

    def search_node(state: AppointmentGraphState) -> dict[str, object]:
        decision = state["decision"]
        try:
            starts_at, ends_at = resolve_appointment_date_range(
                date_from=decision.date_from,
                date_to=decision.date_to,
                fallback_text=state["message"],
                timezone_name=timezone_name,
                now_provider=now_provider,
            )
        except AppointmentDateResolutionError:
            logger.info(
                "appointment_date_information_required",
                extra={
                    "timezone": timezone_name,
                    "has_date_from": decision.date_from is not None,
                    "has_date_to": decision.date_to is not None,
                },
            )
            starts_at = ends_at = None

        # A successfully resolved phrase satisfies both date fields even when
        # the model conservatively listed one of them as missing.
        unresolved = [
            field
            for field in decision.missing_fields
            if field not in {"date_from", "date_to"} or starts_at is None
        ]
        if starts_at is None or ends_at is None or unresolved:
            return information_required(
                state,
                (
                    "When should I search? You can say 'tomorrow afternoon', "
                    "'next week', 'this Friday', or provide exact dates."
                ),
            )
        started = perf_counter()
        items = search_availability(
            repository,
            starts_at=starts_at,
            ends_at=ends_at,
            specialty=decision.specialty,
            doctor_name=decision.doctor_name,
        )
        return {
            "response": ChatResponse(
                action=ChatAction.ANSWER,
                message=(
                    "Here are the available appointment times. Choose one slot ID to book."
                    if items
                    else "No matching appointment times are currently available."
                ),
                data={"available_slots": [item.model_dump(mode="json") for item in items]},
            ),
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name="appointment.searchAvailability",
                    tool_name="appointment.availability.search",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    arguments={
                        "specialty": decision.specialty,
                        "doctor_name": decision.doctor_name,
                        "starts_at": starts_at.isoformat(),
                        "ends_at": ends_at.isoformat(),
                        "timezone": timezone_name,
                    },
                    result={"slot_count": len(items)},
                ),
            ],
        }

    def proposal_node(state: AppointmentGraphState) -> dict[str, object]:
        decision = state["decision"]
        if decision.missing_fields:
            names = ", ".join(decision.missing_fields)
            return information_required(state, f"Please provide: {names}.")
        appointment_id = None
        slot_id = None
        current_appointment = None
        selected_slot = None
        if decision.intent in {
            AppointmentIntent.RESCHEDULE_APPOINTMENT,
            AppointmentIntent.CANCEL_APPOINTMENT,
        }:
            appointment_id = _uuid(decision.appointment_id, "appointment ID")
            owned = {item.id: item for item in list_appointments(repository, patient_id)}
            if appointment_id not in owned:
                raise AppointmentUnderstandingError("The appointment is not active or not yours")
            current_appointment = owned[appointment_id]
        if decision.intent in {
            AppointmentIntent.BOOK_APPOINTMENT,
            AppointmentIntent.RESCHEDULE_APPOINTMENT,
        }:
            slot_id = _uuid(decision.slot_id, "slot ID")
            selected_slot = get_available_slot(repository, slot_id)
        payload = AppointmentActionPayload(
            operation=decision.intent.value,
            appointment_id=appointment_id,
            slot_id=slot_id,
            reason=decision.reason,
        )
        started = perf_counter()
        token, draft = create_appointment_action_draft(
            decision.intent.value,  # type: ignore[arg-type]
            payload.model_dump(mode="json", exclude={"operation"}),
            ttl_seconds=pending_action_ttl_seconds,
        )
        proposed = payload.model_dump(mode="json", exclude_none=True)
        if current_appointment is not None:
            proposed["current_appointment"] = current_appointment.model_dump(mode="json")
        if selected_slot is not None:
            proposed["selected_slot"] = selected_slot.model_dump(mode="json")
        return {
            "response": ChatResponse(
                action=ChatAction.CONFIRMATION_REQUIRED,
                message="Please confirm this appointment action before it is applied.",
                requires_confirmation=True,
                pending_action_id=token,
                data={"proposed_appointment_action": proposed},
            ),
            "pending_action": draft,
            "traces": [
                *state.get("traces", []),
                ChatToolTrace(
                    node_name=f"appointment.{decision.intent.value}",
                    tool_name="appointment.pending_action.create",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    arguments={"operation": decision.intent.value},
                    result={"confirmation_required": True},
                ),
            ],
        }

    def unsupported(state: AppointmentGraphState) -> dict[str, object]:
        return {
            "response": ChatResponse(
                action=ChatAction.UNSUPPORTED,
                message=(
                    "I can list your appointments, find available times, and propose booking, "
                    "rescheduling, or cancellation. I cannot provide medical advice."
                ),
            ),
            "traces": state.get("traces", []),
        }

    graph = StateGraph(AppointmentGraphState)
    graph.add_node("classifyAppointmentIntent", classify)
    graph.add_node("listAppointments", list_node)
    graph.add_node("searchAvailability", search_node)
    graph.add_node("proposeAppointment", proposal_node)
    graph.add_node("unsupported", unsupported)
    graph.add_edge(START, "classifyAppointmentIntent")
    graph.add_conditional_edges(
        "classifyAppointmentIntent",
        route,
        {
            AppointmentIntent.LIST_APPOINTMENTS.value: "listAppointments",
            AppointmentIntent.SEARCH_AVAILABILITY.value: "searchAvailability",
            AppointmentIntent.BOOK_APPOINTMENT.value: "proposeAppointment",
            AppointmentIntent.RESCHEDULE_APPOINTMENT.value: "proposeAppointment",
            AppointmentIntent.CANCEL_APPOINTMENT.value: "proposeAppointment",
            AppointmentIntent.UNSUPPORTED.value: "unsupported",
        },
    )
    for node in ("listAppointments", "searchAvailability", "proposeAppointment", "unsupported"):
        graph.add_edge(node, END)
    return graph.compile()
