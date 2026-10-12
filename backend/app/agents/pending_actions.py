"""Secure creation and transactional consumption of pending profile updates.

The browser receives a random token once. Careline stores only SHA-256(token)
in Supabase, so database contents cannot be copied and submitted as approval.
Every lookup is also scoped to the JWT patient and active conversation.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from app.repositories.chat_history_repository import ChatHistoryRepository
from app.schemas.chat import ChatPendingActionDraft
from app.schemas.chat_history import PendingActionConsumeRecord
from app.services.chat_history_service import (
    ChatHistoryDataIntegrityError,
    ChatHistoryServiceUnavailableError,
)

logger = logging.getLogger(__name__)


class PendingActionNotFoundError(LookupError):
    """Raised for an unknown, expired, consumed or differently owned token."""


@dataclass(frozen=True, slots=True)
class PendingUpdate:
    """Validated action payload returned after one successful consumption."""

    key: str
    value: Any


@dataclass(frozen=True, slots=True)
class ConsumedPatientAction:
    """Generic one-time action used by profile and appointment confirmations."""

    action_type: str
    payload: dict[str, Any]


def _token_hash(action_id: str) -> str:
    """Return a lowercase fixed-length digest without logging the token."""

    return hashlib.sha256(action_id.encode("utf-8")).hexdigest()


def create_pending_action_draft(
    key: str,
    value: Any,
    *,
    ttl_seconds: int,
) -> tuple[str, ChatPendingActionDraft]:
    """Create the public token and backend-only row data for atomic insertion."""

    action_id = secrets.token_urlsafe(32)
    draft = ChatPendingActionDraft(
        token_hash=_token_hash(action_id),
        action_payload={"key": key, "value": value},
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    logger.info("chat_update_proposal_created", extra={"attribute_key": key})
    return action_id, draft


def create_appointment_action_draft(
    action_type: Literal["book_appointment", "reschedule_appointment", "cancel_appointment"],
    payload: dict[str, Any],
    *,
    ttl_seconds: int,
) -> tuple[str, ChatPendingActionDraft]:
    """Create a hash-only durable proposal for an appointment mutation."""

    action_id = secrets.token_urlsafe(32)
    # Operation is duplicated inside the protected payload because the existing
    # transactional consume RPC intentionally returns only the payload. It is
    # validated against the draft action_type before storage.
    action_payload = {**payload, "operation": action_type}
    draft = ChatPendingActionDraft(
        token_hash=_token_hash(action_id),
        action_type=action_type,
        action_payload=action_payload,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    logger.info("appointment_proposal_created", extra={"operation": action_type})
    return action_id, draft


def consume_patient_action(
    repository: ChatHistoryRepository,
    action_id: str,
    patient_id: UUID,
    session_id: UUID,
    *,
    decision: Literal["approved", "rejected"],
) -> ConsumedPatientAction:
    """Consume a patient action once and identify its validated operation."""

    try:
        raw = repository.consume_pending_action(
            {
                "p_chat_initiated_by": str(patient_id),
                "p_chat_initiator_role": "patient",
                "p_session_id": str(session_id),
                "p_token_hash": _token_hash(action_id),
                "p_decision": decision,
            }
        )
        if not isinstance(raw, list) or len(raw) != 1:
            raise ChatHistoryDataIntegrityError("Pending action response is invalid")
        record = PendingActionConsumeRecord.model_validate(raw[0])
    except ChatHistoryDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error("chat_pending_action_validation_failed")
        raise ChatHistoryDataIntegrityError("Stored pending action is invalid") from exc
    except Exception as exc:
        logger.error("chat_pending_action_consume_failed", extra={"error_type": type(exc).__name__})
        raise ChatHistoryServiceUnavailableError("Pending action consumption failed") from exc
    if record.outcome != "consumed" or record.action_payload is None:
        logger.warning("chat_pending_action_rejected")
        raise PendingActionNotFoundError("Pending action is unavailable")
    payload = record.action_payload
    operation = payload.get("operation")
    if isinstance(operation, str):
        action_type = operation
    elif isinstance(payload.get("key"), str) and "value" in payload:
        action_type = "update_profile"
    else:
        raise ChatHistoryDataIntegrityError("Pending action payload is invalid")
    logger.info(
        "chat_pending_action_consumed",
        extra={"action_type": action_type, "decision": decision},
    )
    return ConsumedPatientAction(action_type=action_type, payload=payload)


def consume_pending_action(
    repository: ChatHistoryRepository,
    action_id: str,
    patient_id: UUID,
    session_id: UUID,
    *,
    decision: Literal["approved", "rejected"],
) -> PendingUpdate:
    """Consume one owned/unexpired action and return its validated payload."""

    consumed = consume_patient_action(
        repository,
        action_id,
        patient_id,
        session_id,
        decision=decision,
    )
    key = consumed.payload.get("key")
    if (
        consumed.action_type != "update_profile"
        or not isinstance(key, str)
        or "value" not in consumed.payload
    ):
        logger.error("chat_pending_action_payload_invalid")
        raise ChatHistoryDataIntegrityError("Pending action payload is invalid")

    logger.info("chat_pending_action_consumed", extra={"attribute_key": key, "decision": decision})
    return PendingUpdate(key=key, value=consumed.payload["value"])
