"""Persistence orchestration for admin sessions and invitation graph turns."""

import base64
import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from pydantic import ValidationError

from app.agents.admin_summarizer import AdminChatSummarizer
from app.repositories.admin_chat_repository import AdminChatRepository
from app.schemas.admin import (
    AdminChatAction,
    AdminChatDecision,
    AdminChatRequest,
    AdminChatResponse,
    AdminChatSessionCreate,
    AdminChatSessionOut,
    AdminChatSessionPage,
    AdminChatSessionRecord,
    AdminChatTurnCompleteRecord,
    AdminChatTurnStartRecord,
    AdminPendingActionDraft,
    AdminStartedTurn,
    AdminTurnMetadata,
)
from app.schemas.chat import ChatToolTrace
from app.schemas.chat_history import (
    ChatContextMessage,
    ChatMessageOut,
    ChatMessagePage,
    ChatMessageRecord,
    ChatSessionMemoryRecord,
    ChatSessionUpdate,
)
from app.services.chat_history_service import (
    ChatHistoryCursorError,
    ChatHistoryDataIntegrityError,
    ChatHistoryServiceUnavailableError,
    ChatSessionNotFoundError,
    ChatSessionStateError,
)
from app.services.chat_turn_service import ChatTurnAlreadyProcessedError

logger = logging.getLogger(__name__)


def _page_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.b64decode(padded, altchars=b"-_", validate=True).decode("ascii")
        if not value.isdigit() or int(value) > 1_000_000:
            raise ValueError
        return int(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChatHistoryCursorError("Invalid admin history cursor") from exc


def _next_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _one_row(data: object, operation: str) -> object:
    if not isinstance(data, list) or len(data) != 1:
        logger.error(
            "admin_chat_response_invalid",
            extra={"operation": operation, "response_shape": type(data).__name__},
        )
        raise ChatHistoryDataIntegrityError("Admin chat response is invalid")
    return data[0]


def _session_record(row: object) -> AdminChatSessionRecord:
    try:
        return AdminChatSessionRecord.model_validate(row)
    except ValidationError as exc:
        logger.error(
            "admin_chat_session_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Admin chat session is invalid") from exc


def _session_out(record: AdminChatSessionRecord) -> AdminChatSessionOut:
    return AdminChatSessionOut.model_validate(
        record.model_dump(
            exclude={
                "chat_initiated_by",
                "chat_initiator_role",
                "model_name",
                "prompt_version",
                "created_at",
                "updated_at",
            }
        )
    )


def create_admin_session(
    repository: AdminChatRepository,
    admin_id: UUID,
    request: AdminChatSessionCreate,
    *,
    model_name: str,
    prompt_version: str,
    prior_session_limit: int,
) -> AdminChatSessionOut:
    try:
        row = _one_row(
            repository.create_session(
                {
                    "p_chat_initiated_by": str(admin_id),
                    "p_chat_initiator_role": "admin",
                    "p_subject_patient_id": None,
                    "p_title": request.title or "New patient invitation",
                    "p_model_name": model_name,
                    "p_prompt_version": prompt_version,
                    "p_prior_session_limit": prior_session_limit,
                }
            ),
            "create_session",
        )
        record = _session_record(row)
    except (ChatHistoryDataIntegrityError, ValidationError):
        raise
    except Exception as exc:
        logger.error("admin_chat_session_create_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat session creation failed") from exc
    if record.chat_initiated_by != admin_id or record.chat_initiator_role != "admin":
        raise ChatHistoryDataIntegrityError("Admin chat session owner is invalid")
    logger.info("admin_chat_session_created")
    return _session_out(record)


def list_admin_sessions(
    repository: AdminChatRepository,
    admin_id: UUID,
    *,
    limit: int,
    cursor: str | None,
) -> AdminChatSessionPage:
    """Return only sessions initiated by the authenticated admin."""

    offset = _page_offset(cursor)
    try:
        rows = repository.select_sessions(str(admin_id), offset=offset, row_count=limit + 1)
        if not isinstance(rows, list):
            raise ChatHistoryDataIntegrityError("Admin session list is invalid")
        records = [_session_record(row) for row in rows[:limit]]
    except (ChatHistoryDataIntegrityError, ChatHistoryCursorError):
        raise
    except Exception as exc:
        logger.error("admin_chat_session_list_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat session list failed") from exc
    if any(
        item.chat_initiated_by != admin_id or item.chat_initiator_role != "admin"
        for item in records
    ):
        raise ChatHistoryDataIntegrityError("Admin chat session owner is invalid")
    has_more = len(rows) > limit
    logger.info(
        "admin_chat_sessions_retrieved",
        extra={"returned_count": len(records), "has_more": has_more},
    )
    return AdminChatSessionPage(
        items=[_session_out(item) for item in records],
        next_cursor=_next_cursor(offset + limit) if has_more else None,
    )


def list_admin_messages(
    repository: AdminChatRepository,
    admin_id: UUID,
    session_id: UUID,
    *,
    limit: int,
    cursor: str | None,
    newest_first: bool = False,
) -> ChatMessagePage:
    """Restore one admin conversation while enforcing role-aware ownership."""

    session = _session_record(
        _one_row(repository.select_session(str(admin_id), str(session_id)), "get_session")
    )
    if session.chat_initiated_by != admin_id or session.chat_initiator_role != "admin":
        raise ChatSessionNotFoundError("Admin chat session was not found")
    offset = _page_offset(cursor)
    try:
        rows = repository.select_messages(
            str(admin_id),
            str(session_id),
            offset=offset,
            row_count=limit + 1,
            **({"newest_first": True} if newest_first else {}),
        )
        if not isinstance(rows, list):
            raise ChatHistoryDataIntegrityError("Admin message list is invalid")
        records = [ChatMessageRecord.model_validate(row) for row in rows[:limit]]
    except (ChatHistoryDataIntegrityError, ValidationError, ChatHistoryCursorError):
        raise
    except Exception as exc:
        logger.error("admin_chat_message_list_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat message list failed") from exc
    if any(
        item.chat_initiated_by != admin_id
        or item.chat_initiator_role != "admin"
        or item.session_id != session_id
        for item in records
    ):
        raise ChatHistoryDataIntegrityError("Admin chat message owner is invalid")
    has_more = len(rows) > limit
    logger.info(
        "admin_chat_messages_retrieved",
        extra={"returned_count": len(records), "has_more": has_more},
    )
    if newest_first:
        records.reverse()
    return ChatMessagePage(
        session_id=session_id,
        items=[ChatMessageOut.from_record(item) for item in records],
        next_cursor=_next_cursor(offset + limit) if has_more else None,
    )


def rename_admin_session(
    repository: AdminChatRepository,
    admin_id: UUID,
    session_id: UUID,
    request: ChatSessionUpdate,
) -> AdminChatSessionOut:
    """Rename one administrator-owned session with a status compare-and-set."""

    record = _session_record(
        _one_row(repository.select_session(str(admin_id), str(session_id)), "get_session")
    )
    if record.chat_initiated_by != admin_id or record.chat_initiator_role != "admin":
        raise ChatSessionNotFoundError("Admin chat session was not found")
    try:
        updated = repository.update_session(
            str(admin_id),
            str(session_id),
            {"title": request.title},
            expected_status=record.status,
        )
        renamed = _session_record(_one_row(updated, "rename_session"))
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error("admin_chat_session_rename_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat session rename failed") from exc
    logger.info("admin_chat_session_renamed")
    return _session_out(renamed)


def set_admin_session_archived(
    repository: AdminChatRepository,
    admin_id: UUID,
    session_id: UUID,
    *,
    archived: bool,
) -> AdminChatSessionOut:
    """Archive a closed admin session or restore it back to closed."""

    record = _session_record(
        _one_row(repository.select_session(str(admin_id), str(session_id)), "get_session")
    )
    if record.chat_initiated_by != admin_id or record.chat_initiator_role != "admin":
        raise ChatSessionNotFoundError("Admin chat session was not found")
    target = "archived" if archived else "closed"
    required = "closed" if archived else "archived"
    if record.status == target:
        return _session_out(record)
    if record.status != required:
        raise ChatSessionStateError("Admin chat session cannot change archive state")
    try:
        updated = repository.update_session(
            str(admin_id),
            str(session_id),
            {"status": target},
            expected_status=required,
        )
        changed = _session_record(_one_row(updated, "archive_session"))
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "admin_chat_session_archive_state_failed",
            extra={"error_type": type(exc).__name__, "archived": archived},
        )
        raise ChatHistoryServiceUnavailableError("Admin chat archive update failed") from exc
    logger.info("admin_chat_session_archive_state_changed", extra={"archived": archived})
    return _session_out(changed)


def close_admin_session(
    repository: AdminChatRepository,
    admin_id: UUID,
    session_id: UUID,
    *,
    summarizer: AdminChatSummarizer,
    summary_message_limit: int,
    summary_max_chars: int,
) -> AdminChatSessionOut:
    try:
        record = _session_record(
            _one_row(repository.select_session(str(admin_id), str(session_id)), "get_session")
        )
        if record.status == "closed":
            return _session_out(record)
        if record.status != "active":
            raise ChatSessionStateError("Admin session cannot be closed")
        rows = repository.select_summary_messages(
            str(admin_id), str(session_id), row_count=summary_message_limit
        )
        if not isinstance(rows, list):
            raise ChatHistoryDataIntegrityError("Admin summary source is invalid")
        messages = [ChatContextMessage.model_validate(row) for row in reversed(rows)]
        summary = None
        if messages:
            summary = summarizer.summarize(messages).strip()[:summary_max_chars].strip()
            if not summary:
                raise ChatHistoryDataIntegrityError("Admin chat summary is empty")
        updated = repository.update_session(
            str(admin_id),
            str(session_id),
            {
                "status": "closed",
                "ended_at": datetime.now(UTC).isoformat(),
                "summary": summary,
            },
            expected_status="active",
        )
        closed = _session_record(_one_row(updated, "close_session"))
    except (ChatHistoryDataIntegrityError, ChatSessionStateError):
        raise
    except ValidationError as exc:
        raise ChatHistoryDataIntegrityError("Admin summary source is invalid") from exc
    except Exception as exc:
        logger.error("admin_chat_session_close_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat session close failed") from exc
    logger.info(
        "admin_chat_session_closed",
        extra={"summary_created": summary is not None, "source_message_count": len(messages)},
    )
    return _session_out(closed)


def _input_content(request: AdminChatRequest) -> tuple[str, str]:
    if request.message is not None:
        return request.message, "text"
    if request.confirmation is None:
        raise ChatHistoryDataIntegrityError("Admin chat request is invalid")
    if request.confirmation.approved:
        return "Confirmed the proposed patient invitation.", "confirmation_result"
    return "Declined the proposed patient invitation.", "confirmation_result"


def start_admin_turn(
    repository: AdminChatRepository,
    admin_id: UUID,
    request_id: UUID,
    request: AdminChatRequest,
    *,
    model_name: str,
    prompt_version: str,
    context_limit: int,
) -> tuple[AdminStartedTurn, tuple[ChatSessionMemoryRecord, ...]]:
    started_at = perf_counter()
    content, message_type = _input_content(request)
    try:
        record = AdminChatTurnStartRecord.model_validate(
            _one_row(
                repository.start_turn(
                    {
                        "p_chat_initiated_by": str(admin_id),
                        "p_chat_initiator_role": "admin",
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
                ),
                "start_turn",
            )
        )
    except (ChatHistoryDataIntegrityError, ValidationError):
        raise
    except Exception as exc:
        logger.error("admin_chat_turn_start_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat turn start failed") from exc

    if record.outcome == "not_found":
        raise ChatSessionNotFoundError("Admin chat session was not found")
    if record.outcome in {"not_active", "in_progress"}:
        raise ChatSessionStateError("Admin chat session is not available")
    if record.outcome == "duplicate":
        raise ChatTurnAlreadyProcessedError("Admin client message was already processed")
    if record.run_id is None or record.input_message_id is None or record.input_sequence is None:
        raise ChatHistoryDataIntegrityError("Admin turn identifiers are missing")

    try:
        context_rows = repository.select_context_messages(
            str(admin_id),
            str(request.session_id),
            before_sequence=record.input_sequence,
            row_count=context_limit,
        )
        memory_rows = repository.select_memory(
            {
                "p_chat_initiated_by": str(admin_id),
                "p_chat_initiator_role": "admin",
                "p_session_id": str(request.session_id),
            }
        )
        if not isinstance(context_rows, list) or not isinstance(memory_rows, list):
            raise ChatHistoryDataIntegrityError("Admin chat context is invalid")
        context = tuple(ChatContextMessage.model_validate(row) for row in reversed(context_rows))
        memory = tuple(ChatSessionMemoryRecord.model_validate(row) for row in memory_rows)
    except Exception as exc:
        fail_admin_turn(
            repository,
            admin_id,
            request.session_id,
            record.run_id,
            started_at=started_at,
            error_code="context_unavailable",
        )
        if isinstance(exc, ChatHistoryDataIntegrityError):
            raise
        logger.error("admin_chat_context_load_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat context load failed") from exc

    logger.info(
        "admin_chat_turn_started",
        extra={"context_message_count": len(context), "prior_summary_count": len(memory)},
    )
    return (
        AdminStartedTurn(
            session_id=request.session_id,
            run_id=record.run_id,
            input_message_id=record.input_message_id,
            input_sequence=record.input_sequence,
            context=context,
            started_at=started_at,
        ),
        memory,
    )


def _message_type(action: AdminChatAction) -> str:
    return {
        AdminChatAction.INFORMATION_REQUIRED: "information_request",
        AdminChatAction.CONFIRMATION_REQUIRED: "confirmation_request",
        AdminChatAction.PATIENT_INVITED: "invitation_result",
        AdminChatAction.CANCELLED: "confirmation_result",
        AdminChatAction.UNSUPPORTED: "text",
    }[action]


def complete_admin_turn(
    repository: AdminChatRepository,
    admin_id: UUID,
    turn: AdminStartedTurn,
    response: AdminChatResponse,
    decision: AdminChatDecision | None,
    traces: list[ChatToolTrace],
    pending_action: AdminPendingActionDraft | None,
) -> AdminChatResponse:
    latency_ms = round((perf_counter() - turn.started_at) * 1000, 2)
    stored = response.model_dump(mode="json", exclude={"turn", "pending_action_id"})
    try:
        record = AdminChatTurnCompleteRecord.model_validate(
            _one_row(
                repository.complete_turn(
                    {
                        "p_chat_initiated_by": str(admin_id),
                        "p_chat_initiator_role": "admin",
                        "p_session_id": str(turn.session_id),
                        "p_run_id": str(turn.run_id),
                        "p_content": response.message,
                        "p_message_type": _message_type(response.action),
                        "p_structured_data": stored,
                        "p_intent": decision.intent.value if decision else None,
                        "p_decision_data": decision.model_dump(mode="json") if decision else None,
                        "p_latency_ms": latency_ms,
                        "p_tool_events": [trace.model_dump(mode="json") for trace in traces],
                        "p_pending_action": (
                            pending_action.model_dump(mode="json") if pending_action else None
                        ),
                    }
                ),
                "complete_turn",
            )
        )
    except (ChatHistoryDataIntegrityError, ValidationError):
        raise
    except Exception as exc:
        logger.error("admin_chat_turn_complete_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Admin chat turn completion failed") from exc
    if record.outcome != "completed" or record.output_message_id is None:
        raise ChatSessionStateError("Admin chat turn could not be completed")
    logger.info(
        "admin_chat_turn_completed",
        extra={"tool_event_count": len(traces), "latency_ms": latency_ms},
    )
    return response.model_copy(
        update={
            "turn": AdminTurnMetadata(
                session_id=turn.session_id,
                run_id=turn.run_id,
                input_message_id=turn.input_message_id,
                output_message_id=record.output_message_id,
            )
        }
    )


def fail_admin_turn(
    repository: AdminChatRepository,
    admin_id: UUID,
    session_id: UUID,
    run_id: UUID,
    *,
    started_at: float,
    error_code: str,
) -> None:
    try:
        repository.fail_turn(
            {
                "p_chat_initiated_by": str(admin_id),
                "p_chat_initiator_role": "admin",
                "p_session_id": str(session_id),
                "p_run_id": str(run_id),
                "p_error_code": error_code[:100],
                "p_latency_ms": round(max(0.0, (perf_counter() - started_at) * 1000), 2),
            }
        )
        logger.info("admin_chat_turn_failed_recorded")
    except Exception as exc:
        logger.error(
            "admin_chat_failure_record_failed",
            extra={"error_type": type(exc).__name__},
        )
