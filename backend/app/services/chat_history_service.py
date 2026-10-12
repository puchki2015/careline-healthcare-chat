"""Deterministic conversation-session operations shared by HTTP and chat.

This service validates every database result and converts opaque pagination
cursors. It never logs patient IDs, titles, summaries, message content or JSON
payloads because all of those values may identify a patient or contain PHI.
"""

import base64
import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.agents.summarizer import ChatSummarizer
from app.repositories.chat_history_repository import ChatHistoryRepository
from app.schemas.chat_history import (
    ChatContextMessage,
    ChatMessageOut,
    ChatMessagePage,
    ChatMessageRecord,
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionPage,
    ChatSessionRecord,
    ChatSessionStatus,
    ChatSessionUpdate,
)

logger = logging.getLogger(__name__)


class ChatHistoryCursorError(ValueError):
    """Raised when an opaque page cursor cannot be safely decoded."""


class ChatSessionNotFoundError(LookupError):
    """Raised when no RLS-visible session belongs to the patient."""


class ChatSessionStateError(RuntimeError):
    """Raised when the requested transition is not allowed."""


class ChatHistoryServiceUnavailableError(RuntimeError):
    """Raised when Supabase cannot complete a history operation."""


class ChatHistoryDataIntegrityError(RuntimeError):
    """Raised when stored history violates the application contract."""


def _encode_cursor(offset: int) -> str:
    """Encode a non-secret numeric offset as an opaque URL-safe cursor."""

    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    """Decode a cursor without reflecting its untrusted content in errors."""

    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
        if not decoded.isdigit():
            raise ValueError
        offset = int(decoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChatHistoryCursorError("Invalid history cursor") from exc

    # Prevent deliberately enormous offsets from creating expensive database
    # work. Long histories will later use keyset cursors without changing the
    # public response envelope.
    if offset > 1_000_000:
        raise ChatHistoryCursorError("Invalid history cursor")
    return offset


def _validate_session_row(row: object) -> ChatSessionRecord:
    try:
        return ChatSessionRecord.model_validate(row)
    except ValidationError as exc:
        logger.error(
            "chat_session_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Stored chat session is invalid") from exc


def _require_rows(data: object, operation: str) -> list[object]:
    if not isinstance(data, list):
        logger.error(
            "chat_history_response_invalid",
            extra={"operation": operation, "response_shape": type(data).__name__},
        )
        raise ChatHistoryDataIntegrityError("Chat history response is invalid")
    return data


def create_chat_session(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    request: ChatSessionCreate,
    *,
    model_name: str,
    prompt_version: str,
    prior_session_limit: int,
) -> ChatSessionOut:
    """Create a session and atomically record bounded prior-summary sources."""

    values = {
        # Identity and role are derived by the backend, never accepted from
        # request JSON. A patient's own conversation also subjects that patient.
        "p_chat_initiated_by": str(patient_id),
        "p_chat_initiator_role": "patient",
        "p_subject_patient_id": str(patient_id),
        "p_title": request.title or "New conversation",
        "p_model_name": model_name,
        "p_prompt_version": prompt_version,
        "p_prior_session_limit": prior_session_limit,
    }
    try:
        rows = _require_rows(
            repository.create_session_with_context(values),
            "create_session",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_session_create_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat session creation failed") from exc

    if not rows:
        raise ChatHistoryDataIntegrityError("Created chat session was not returned")
    session = _validate_session_row(rows[0])
    if session.chat_initiated_by != patient_id or session.chat_initiator_role != "patient":
        logger.error("chat_session_owner_mismatch", extra={"operation": "create_session"})
        raise ChatHistoryDataIntegrityError("Created chat session owner is invalid")

    logger.info(
        "chat_session_created",
        extra={"prior_session_limit": prior_session_limit},
    )
    return ChatSessionOut.from_record(session)


def list_chat_sessions(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    *,
    limit: int,
    cursor: str | None,
) -> ChatSessionPage:
    """Return a bounded recent-session page for the authenticated patient."""

    offset = _decode_cursor(cursor)
    try:
        rows = _require_rows(
            repository.select_sessions(
                str(patient_id),
                "patient",
                offset=offset,
                row_count=limit + 1,
            ),
            "list_sessions",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_session_list_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat session list failed") from exc

    has_more = len(rows) > limit
    sessions = [_validate_session_row(row) for row in rows[:limit]]
    if any(
        session.chat_initiated_by != patient_id or session.chat_initiator_role != "patient"
        for session in sessions
    ):
        logger.error("chat_session_owner_mismatch", extra={"operation": "list_sessions"})
        raise ChatHistoryDataIntegrityError("Chat session owner is invalid")

    logger.info(
        "chat_sessions_retrieved",
        extra={"returned_count": len(sessions), "has_more": has_more},
    )
    return ChatSessionPage(
        items=[ChatSessionOut.from_record(session) for session in sessions],
        next_cursor=_encode_cursor(offset + limit) if has_more else None,
    )


def _get_session(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
) -> ChatSessionRecord:
    try:
        rows = _require_rows(
            repository.select_session(str(patient_id), "patient", str(session_id)),
            "get_session",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_session_query_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat session query failed") from exc

    if not rows:
        raise ChatSessionNotFoundError("Chat session was not found")
    session = _validate_session_row(rows[0])
    if (
        session.chat_initiated_by != patient_id
        or session.chat_initiator_role != "patient"
        or session.id != session_id
    ):
        logger.error("chat_session_owner_mismatch", extra={"operation": "get_session"})
        raise ChatHistoryDataIntegrityError("Chat session identity is invalid")
    return session


def list_chat_messages(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
    *,
    limit: int,
    cursor: str | None,
    newest_first: bool = False,
) -> ChatMessagePage:
    """Verify session ownership, then return its ordered message page."""

    _get_session(repository, patient_id, session_id)
    offset = _decode_cursor(cursor)
    try:
        rows = _require_rows(
            repository.select_messages(
                str(patient_id),
                "patient",
                str(session_id),
                offset=offset,
                row_count=limit + 1,
                **({"newest_first": True} if newest_first else {}),
            ),
            "list_messages",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_message_list_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat message list failed") from exc

    has_more = len(rows) > limit
    messages: list[ChatMessageRecord] = []
    for row in rows[:limit]:
        try:
            message = ChatMessageRecord.model_validate(row)
        except ValidationError as exc:
            logger.error(
                "chat_message_validation_failed",
                extra={"validation_error_count": exc.error_count()},
            )
            raise ChatHistoryDataIntegrityError("Stored chat message is invalid") from exc
        if (
            message.chat_initiated_by != patient_id
            or message.chat_initiator_role != "patient"
            or message.session_id != session_id
        ):
            logger.error("chat_message_owner_mismatch")
            raise ChatHistoryDataIntegrityError("Chat message identity is invalid")
        messages.append(message)

    logger.info(
        "chat_messages_retrieved",
        extra={"returned_count": len(messages), "has_more": has_more},
    )
    # The database reads the newest page in descending order. Reverse only
    # that page before returning it so the browser still renders top-to-bottom
    # chronological conversation order.
    if newest_first:
        messages.reverse()
    return ChatMessagePage(
        session_id=session_id,
        items=[ChatMessageOut.from_record(message) for message in messages],
        next_cursor=_encode_cursor(offset + limit) if has_more else None,
    )


def rename_chat_session(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
    request: ChatSessionUpdate,
) -> ChatSessionOut:
    """Rename one owned session without exposing owner fields to the client."""

    existing = _get_session(repository, patient_id, session_id)
    try:
        rows = _require_rows(
            repository.update_session(
                str(patient_id),
                "patient",
                str(session_id),
                {"title": request.title},
                expected_status=existing.status.value,
            ),
            "rename_session",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error("chat_session_rename_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Chat session rename failed") from exc
    if not rows:
        raise ChatSessionStateError("Chat session status changed during rename")
    renamed = _validate_session_row(rows[0])
    logger.info("chat_session_renamed")
    return ChatSessionOut.from_record(renamed)


def set_chat_session_archived(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
    *,
    archived: bool,
) -> ChatSessionOut:
    """Archive a closed session or restore an archived session to closed."""

    existing = _get_session(repository, patient_id, session_id)
    target = ChatSessionStatus.ARCHIVED if archived else ChatSessionStatus.CLOSED
    required = ChatSessionStatus.CLOSED if archived else ChatSessionStatus.ARCHIVED
    if existing.status == target:
        return ChatSessionOut.from_record(existing)
    if existing.status != required:
        raise ChatSessionStateError("Chat session cannot change archive state")
    try:
        rows = _require_rows(
            repository.update_session(
                str(patient_id),
                "patient",
                str(session_id),
                {"status": target.value},
                expected_status=required.value,
            ),
            "archive_session" if archived else "restore_session",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_session_archive_state_failed",
            extra={"error_type": type(exc).__name__, "archived": archived},
        )
        raise ChatHistoryServiceUnavailableError("Chat archive update failed") from exc
    if not rows:
        raise ChatSessionStateError("Chat session status changed during archive update")
    updated = _validate_session_row(rows[0])
    logger.info("chat_session_archive_state_changed", extra={"archived": archived})
    return ChatSessionOut.from_record(updated)


def close_chat_session(
    repository: ChatHistoryRepository,
    patient_id: UUID,
    session_id: UUID,
    *,
    summarizer: ChatSummarizer,
    summary_message_limit: int,
    summary_max_chars: int,
) -> ChatSessionOut:
    """Summarize and idempotently close an owned active conversation."""

    existing = _get_session(repository, patient_id, session_id)
    if existing.status == ChatSessionStatus.CLOSED:
        logger.info("chat_session_close_repeated")
        return ChatSessionOut.from_record(existing)
    if existing.status == ChatSessionStatus.ARCHIVED:
        raise ChatSessionStateError("Archived session cannot be closed")

    # Load newest-first for an indexed bounded query, then restore normal
    # conversation order before sending the messages to the summarizer.
    try:
        raw_messages = _require_rows(
            repository.select_summary_messages(
                str(patient_id),
                "patient",
                str(session_id),
                row_count=summary_message_limit,
            ),
            "summarize_session",
        )
        summary_messages = [
            ChatContextMessage.model_validate(row) for row in reversed(raw_messages)
        ]
    except ChatHistoryDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error(
            "chat_summary_source_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise ChatHistoryDataIntegrityError("Stored summary source is invalid") from exc
    except Exception as exc:
        logger.error(
            "chat_summary_source_load_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat summary source load failed") from exc

    summary: str | None = None
    if summary_messages:
        # Normalize and enforce the application limit even if a provider
        # returns unexpected surrounding whitespace or overly long text.
        summary = summarizer.summarize(summary_messages).strip()[:summary_max_chars].strip()
        if not summary:
            logger.error("chat_summary_empty")
            raise ChatHistoryDataIntegrityError("Generated chat summary is empty")

    ended_at = datetime.now(UTC).isoformat()
    try:
        rows = _require_rows(
            repository.update_session(
                str(patient_id),
                "patient",
                str(session_id),
                {
                    "status": ChatSessionStatus.CLOSED.value,
                    "ended_at": ended_at,
                    "summary": summary,
                },
                expected_status=ChatSessionStatus.ACTIVE.value,
            ),
            "close_session",
        )
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "chat_session_close_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ChatHistoryServiceUnavailableError("Chat session close failed") from exc

    if not rows:
        # The owned session was visible immediately before this write. An empty
        # compare-and-set result therefore means another request changed its
        # status concurrently, rather than revealing an ownership condition.
        raise ChatSessionStateError("Chat session status changed during close")
    closed = _validate_session_row(rows[0])
    if (
        closed.chat_initiated_by != patient_id
        or closed.chat_initiator_role != "patient"
        or closed.id != session_id
    ):
        logger.error("chat_session_owner_mismatch", extra={"operation": "close_session"})
        raise ChatHistoryDataIntegrityError("Closed chat session identity is invalid")

    logger.info(
        "chat_session_closed",
        extra={
            "summary_created": summary is not None,
            "summary_source_message_count": len(summary_messages),
        },
    )
    return ChatSessionOut.from_record(closed)
