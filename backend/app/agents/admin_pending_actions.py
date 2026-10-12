"""Hash and transactionally consume administrator invitation confirmations."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from app.repositories.admin_chat_repository import AdminChatRepository
from app.schemas.admin import (
    AdminPendingActionDraft,
    AdminPendingConsumeRecord,
    PatientInvitationDetails,
)
from app.services.admin_service import PatientInvitationServiceError

logger = logging.getLogger(__name__)


class AdminPendingActionNotFoundError(LookupError):
    """Hide whether an invitation token was unknown, expired or already used."""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_invitation_action(
    details: PatientInvitationDetails,
    *,
    ttl_seconds: int,
) -> tuple[str, AdminPendingActionDraft]:
    """Return a plaintext browser token and hash-only persistence draft."""

    token = secrets.token_urlsafe(32)
    draft = AdminPendingActionDraft(
        token_hash=_token_hash(token),
        action_payload=details.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    logger.info("admin_patient_invitation_proposal_created")
    return token, draft


def consume_invitation_action(
    repository: AdminChatRepository,
    token: str,
    admin_id: UUID,
    session_id: UUID,
    *,
    decision: Literal["approved", "rejected"],
) -> PatientInvitationDetails:
    """Consume one admin/session-scoped action and validate its payload."""

    try:
        raw = repository.consume_pending_action(
            {
                "p_chat_initiated_by": str(admin_id),
                "p_chat_initiator_role": "admin",
                "p_session_id": str(session_id),
                "p_token_hash": _token_hash(token),
                "p_decision": decision,
            }
        )
        if not isinstance(raw, list) or len(raw) != 1:
            raise PatientInvitationServiceError("Pending invitation result is invalid")
        record = AdminPendingConsumeRecord.model_validate(raw[0])
    except PatientInvitationServiceError:
        raise
    except ValidationError as exc:
        logger.error(
            "admin_pending_action_validation_failed",
            extra={"validation_error_count": exc.error_count()},
        )
        raise PatientInvitationServiceError("Pending invitation result is invalid") from exc
    except Exception as exc:
        logger.error(
            "admin_pending_action_consume_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise PatientInvitationServiceError("Pending invitation could not be consumed") from exc

    if record.outcome != "consumed" or record.action_payload is None:
        logger.warning("admin_pending_action_unavailable")
        raise AdminPendingActionNotFoundError("Pending invitation is unavailable")

    try:
        details = PatientInvitationDetails.model_validate(record.action_payload)
    except ValidationError as exc:
        logger.error(
            "admin_pending_action_payload_invalid",
            extra={"validation_error_count": exc.error_count()},
        )
        raise PatientInvitationServiceError("Pending invitation payload is invalid") from exc

    logger.info("admin_pending_action_consumed", extra={"decision": decision})
    return details
