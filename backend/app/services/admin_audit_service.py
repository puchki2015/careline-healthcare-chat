"""Build sanitized, administrator-only audit views across all chat tables."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.repositories.admin_chat_repository import AdminChatRepository
from app.schemas.admin import CurrentAdmin
from app.schemas.audit import AuditBundle, AuditSessionPage
from app.services.chat_history_service import (
    ChatHistoryDataIntegrityError,
    ChatHistoryServiceUnavailableError,
    ChatSessionNotFoundError,
)

logger = logging.getLogger(__name__)


def _rows(value: object, operation: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        logger.error("admin_audit_response_invalid", extra={"operation": operation})
        raise ChatHistoryDataIntegrityError("Administrative audit data is invalid")
    return value


def list_audit_sessions(
    repository: AdminChatRepository, admin: CurrentAdmin, *, limit: int
) -> AuditSessionPage:
    try:
        rows = _rows(repository.select_audit_sessions(row_count=limit), "list_sessions")
    except ChatHistoryDataIntegrityError:
        raise
    except Exception as exc:
        logger.error("admin_audit_session_list_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Audit session list failed") from exc
    logger.info(
        "admin_audit_sessions_accessed",
        extra={"admin_id": str(admin.id), "record_count": len(rows)},
    )
    return AuditSessionPage(items=rows)


def _pending_status(row: dict[str, Any]) -> str:
    decision = row.get("decision")
    if decision in {"approved", "rejected"}:
        return str(decision)
    expires_at = row.get("expires_at")
    if isinstance(expires_at, str):
        try:
            if datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.now(UTC):
                return "expired"
        except ValueError:
            pass
    return "pending"


def get_audit_bundle(
    repository: AdminChatRepository,
    admin: CurrentAdmin,
    session_id: UUID,
    *,
    summary_limit: int = 3,
) -> AuditBundle:
    try:
        sessions = _rows(repository.select_audit_session(str(session_id)), "get_session")
        if not sessions:
            raise ChatSessionNotFoundError("Audit session was not found")
        session = sessions[0]
        messages = _rows(
            repository.select_audit_table(
                "chat_messages", str(session_id), order="sequence_number"
            ),
            "messages",
        )
        runs = _rows(
            repository.select_audit_table("chat_runs", str(session_id), order="created_at"),
            "runs",
        )
        tool_events = _rows(
            repository.select_audit_table(
                "chat_tool_events", str(session_id), order="sequence_number"
            ),
            "tool_events",
        )
        pending_rows = _rows(
            repository.select_audit_table(
                "chat_pending_actions", str(session_id), order="created_at"
            ),
            "pending_actions",
        )
        context_rows = _rows(
            repository.select_audit_table(
                "chat_session_context_sources", str(session_id), order="position"
            ),
            "context_sources",
        )
        source_ids = [str(row["source_session_id"]) for row in context_rows]
        source_rows = _rows(repository.select_sessions_by_ids(source_ids), "context_sessions")
        sources = {str(row["id"]): row for row in source_rows}
        for row in context_rows:
            source = sources.get(str(row["source_session_id"]), {})
            row["source_title"] = source.get("title")
            row["source_summary"] = source.get("summary")

        pending_actions = []
        for row in pending_rows:
            sanitized = {key: value for key, value in row.items() if key != "token_hash"}
            sanitized["status"] = _pending_status(row)
            pending_actions.append(sanitized)

        patient_id = session.get("subject_patient_id")
        patient_summaries = (
            _rows(
                repository.select_patient_summaries(str(patient_id), row_count=summary_limit),
                "patient_summaries",
            )
            if patient_id
            else []
        )
    except (ChatHistoryDataIntegrityError, ChatSessionNotFoundError):
        raise
    except Exception as exc:
        logger.error("admin_audit_bundle_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Audit details are unavailable") from exc

    logger.info(
        "admin_audit_session_accessed",
        extra={
            "admin_id": str(admin.id),
            "session_id": str(session_id),
            "subject_patient_id": str(patient_id) if patient_id else None,
            "message_count": len(messages),
            "run_count": len(runs),
            "tool_event_count": len(tool_events),
        },
    )
    return AuditBundle(
        session=session,
        messages=messages,
        runs=runs,
        tool_events=tool_events,
        pending_actions=pending_actions,
        context_sources=context_rows,
        patient_summaries=patient_summaries,
    )
