"""Persistence orchestration for one authenticated LangGraph chat turn.

The database functions called here make each history phase transactional. This
module validates their results and deliberately logs no message content,
patient identifiers, decisions, profile values or confirmation tokens.
"""

import logging
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError

from app.repositories.chat_history_repository import ChatHistoryRepository
from app.schemas.chat import (
    ChatAction,
    ChatDecision,
    ChatPendingActionDraft,
    ChatRequest,
    ChatResponse,
    ChatToolTrace,
    ChatTurnMetadata,
)
from app.schemas.chat_history import (
    ChatContextMessage,
    ChatSessionMemoryRecord,
    ChatTurnCompleteRecord,
    ChatTurnStartRecord,
)
from app.services.chat_history_service import (
    ChatHistoryDataIntegrityError,
    ChatHistoryServiceUnavailableError,
    ChatSessionNotFoundError,
    ChatSessionStateError,
)

logger = logging.getLogger(__name__)


class ChatTurnAlreadyProcessedError(RuntimeError):
    """Raised when a client message UUID has already started a turn."""


@dataclass(frozen=True, slots=True)
class StartedChatTurn:
    """Trusted identifiers and context returned after atomic turn creation."""

    session_id: UUID
    run_id: UUID
    input_message_id: UUID
    input_sequence: int
    context: tuple[ChatContextMessage, ...]
    memory: tuple[ChatSessionMemoryRecord, ...]
    started_at: float


def _one_row(data: object, operation: str) -> object:
    if not isinstance(data, list) or len(data) != 1:
        logger.error(
            "chat_turn_response_invalid",
            extra={"operation": operation, "response_shape": type(data).__name__},
        )
        raise ChatHistoryDataIntegrityError("Chat turn response is invalid")
    return data[0]


def _input_content(request: ChatRequest) -> tuple[str, str]:
    """Return safe persisted text without ever storing an action token."""

    if request.message is not None:
        return request.message, "text"
    if request.confirmation is None:  # Pydantic already prevents this.
        raise ChatHistoryDataIntegrityError("Chat request input is invalid")
    if request.confirmation.approved:
        return "Confirmed the proposed profile change.", "confirmation_result"
    return "Declined the proposed profile change.", "confirmation_result"


def start_chat_turn(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    request_id: UUID,
    request: ChatRequest,
    *,
    model_name: str,
    prompt_version: str,
    context_limit: int,
) -> StartedChatTurn:
    """Atomically persist input/run, then load only bounded earlier context."""

    started_at = perf_counter()
    content, message_type = _input_content(request)
    try:
        raw = repository.start_chat_turn(
            {
                "p_chat_initiated_by": str(patient_id),
                "p_chat_initiator_role": "patient",
                "p_session_id": str(request.session_id),
                "p_request_id": str(request_id),
                "p_client_message_id": (
                    str(request.client_message_id) if request.client_message_id else None
                ),
                "p_content": content,
                "p_message_type": message_type,
                "p_model_name": model_name,
                "p_prompt_version": prompt_version,
            }
        )
        record = ChatTurnStartRecord.model_validate(_one_row(raw, "start_turn"))
    except ChatHistoryDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error(
            "chat_turn_start_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Started chat turn is invalid") from exc
    except Exception as exc:
        logger.error("chat_turn_start_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Chat turn start failed") from exc

    if record.outcome == "not_found":
        raise ChatSessionNotFoundError("Chat session was not found")
    if record.outcome == "not_active":
        raise ChatSessionStateError("Chat session is not active")
    if record.outcome == "in_progress":
        raise ChatSessionStateError("Another chat turn is already running")
    if record.outcome == "duplicate":
        raise ChatTurnAlreadyProcessedError("Client message was already processed")
    if record.run_id is None or record.input_message_id is None or record.input_sequence is None:
        raise ChatHistoryDataIntegrityError("Started chat turn identifiers are missing")

    try:
        context_rows = repository.select_context_messages(
            str(patient_id),
            "patient",
            str(request.session_id),
            before_sequence=record.input_sequence,
            row_count=context_limit,
        )
        if not isinstance(context_rows, list):
            raise ChatHistoryDataIntegrityError("Chat context response is invalid")
        # The repository selects newest first for an efficient bounded query;
        # the model receives normal chronological order.
        context = tuple(ChatContextMessage.model_validate(row) for row in reversed(context_rows))
        memory_rows = repository.select_session_memory(
            {
                "p_chat_initiated_by": str(patient_id),
                "p_chat_initiator_role": "patient",
                "p_session_id": str(request.session_id),
            }
        )
        if not isinstance(memory_rows, list):
            raise ChatHistoryDataIntegrityError("Chat memory response is invalid")
        memory = tuple(ChatSessionMemoryRecord.model_validate(row) for row in memory_rows)
        if [item.position for item in memory] != list(range(1, len(memory) + 1)):
            raise ChatHistoryDataIntegrityError("Chat memory ordering is invalid")
    except ChatHistoryDataIntegrityError:
        # The run exists, so mark it failed before returning the integrity error.
        fail_chat_turn(
            repository,
            patient_id,
            request.session_id,
            record.run_id,
            started_at=started_at,
            error_code="context_invalid",
        )
        raise
    except ValidationError as exc:
        fail_chat_turn(
            repository,
            patient_id,
            request.session_id,
            record.run_id,
            started_at=started_at,
            error_code="context_invalid",
        )
        logger.error(
            "chat_context_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Stored chat context is invalid") from exc
    except Exception as exc:
        fail_chat_turn(
            repository,
            patient_id,
            request.session_id,
            record.run_id,
            started_at=started_at,
            error_code="context_unavailable",
        )
        logger.error("chat_context_load_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Chat context load failed") from exc

    logger.info(
        "chat_turn_started",
        extra={
            "context_message_count": len(context),
            "prior_summary_count": len(memory),
        },
    )
    return StartedChatTurn(
        session_id=request.session_id,
        run_id=record.run_id,
        input_message_id=record.input_message_id,
        input_sequence=record.input_sequence,
        context=context,
        memory=memory,
        started_at=started_at,
    )


def _response_message_type(action: ChatAction) -> str:
    return {
        ChatAction.ANSWER: "profile_result",
        ChatAction.INFORMATION_REQUIRED: "information_request",
        ChatAction.CONFIRMATION_REQUIRED: "confirmation_request",
        ChatAction.UPDATED: "confirmation_result",
        ChatAction.APPOINTMENT_BOOKED: "confirmation_result",
        ChatAction.APPOINTMENT_RESCHEDULED: "confirmation_result",
        ChatAction.APPOINTMENT_CANCELLED: "confirmation_result",
        ChatAction.CANCELLED: "confirmation_result",
        ChatAction.UNSUPPORTED: "text",
    }[action]


def complete_chat_turn(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    turn: StartedChatTurn,
    response: ChatResponse,
    decision: ChatDecision | None,
    traces: list[ChatToolTrace],
    pending_action: ChatPendingActionDraft | None,
) -> ChatResponse:
    """Atomically store the assistant output, run decision and safe traces."""

    latency_ms = round((perf_counter() - turn.started_at) * 1000, 2)
    # The plaintext pending-action token is intentionally omitted from durable
    # message JSON. It remains only in this immediate HTTPS response.
    stored_response = response.model_dump(mode="json", exclude={"turn", "pending_action_id"})
    try:
        raw = repository.complete_chat_turn(
            {
                "p_chat_initiated_by": str(patient_id),
                "p_chat_initiator_role": "patient",
                "p_session_id": str(turn.session_id),
                "p_run_id": str(turn.run_id),
                "p_content": response.message,
                "p_message_type": _response_message_type(response.action),
                "p_structured_data": stored_response,
                "p_intent": decision.intent.value if decision else None,
                "p_decision_data": decision.model_dump(mode="json") if decision else None,
                "p_latency_ms": latency_ms,
                "p_tool_events": [trace.model_dump(mode="json") for trace in traces],
                "p_pending_action": (
                    pending_action.model_dump(mode="json") if pending_action else None
                ),
            }
        )
        record = ChatTurnCompleteRecord.model_validate(_one_row(raw, "complete_turn"))
    except ChatHistoryDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error(
            "chat_turn_complete_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Completed chat turn is invalid") from exc
    except Exception as exc:
        logger.error("chat_turn_complete_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Chat turn completion failed") from exc

    if record.outcome == "not_found":
        raise ChatSessionNotFoundError("Chat run was not found")
    if record.outcome == "not_started":
        raise ChatSessionStateError("Chat run is not awaiting completion")
    if record.output_message_id is None:
        raise ChatHistoryDataIntegrityError("Completed output message ID is missing")

    logger.info(
        "chat_turn_completed",
        extra={"tool_event_count": len(traces), "latency_ms": latency_ms},
    )
    return response.model_copy(
        update={
            "turn": ChatTurnMetadata(
                session_id=turn.session_id,
                run_id=turn.run_id,
                input_message_id=turn.input_message_id,
                output_message_id=record.output_message_id,
            )
        }
    )


def fail_chat_turn(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
    run_id: UUID,
    *,
    started_at: float,
    error_code: str,
) -> None:
    """Best-effort failure recording that never masks the original error."""

    latency_ms = round(max(0.0, (perf_counter() - started_at) * 1000), 2)
    try:
        updated = repository.fail_chat_turn(
            {
                "p_chat_initiated_by": str(patient_id),
                "p_chat_initiator_role": "patient",
                "p_session_id": str(session_id),
                "p_run_id": str(run_id),
                "p_error_code": error_code[:100],
                "p_latency_ms": latency_ms,
            }
        )
        logger.info("chat_turn_failed_recorded", extra={"record_updated": updated is True})
    except Exception as exc:
        logger.error(
            "chat_turn_failure_record_failed",
            extra={"error_type": type(exc).__name__},
        )
