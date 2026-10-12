"""Administrator authorization and secure Supabase patient invitations."""

import logging
from uuid import UUID

from pydantic import ValidationError

from app.repositories.admin_chat_repository import AdminChatRepository
from app.schemas.admin import CurrentAdmin, PatientInvitationDetails

logger = logging.getLogger(__name__)


class AdminAuthorizationError(PermissionError):
    """Raised when an authenticated identity is not an active Careline admin."""


class PatientInvitationConflictError(RuntimeError):
    """Raised when Supabase cannot invite an already registered email address."""


class PatientInvitationServiceError(RuntimeError):
    """Raised when invitation or profile completion fails safely."""


def require_active_admin(
    repository: AdminChatRepository,
    user_id: UUID,
) -> CurrentAdmin:
    """Authorize using the backend-only admin_profiles table, never model output."""

    try:
        rows = repository.select_admin_profile(str(user_id))
        role_rows = repository.select_active_role(str(user_id), "admin")
    except Exception as exc:
        logger.error("admin_authorization_query_failed", extra={"error_type": type(exc).__name__})
        raise PatientInvitationServiceError("Admin authorization query failed") from exc

    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(role_rows, list)
        or len(role_rows) != 1
    ):
        logger.warning("admin_authorization_denied")
        raise AdminAuthorizationError("Active administrator access is required")
    row = rows[0]
    role_row = role_rows[0]
    if not isinstance(row, dict) or not isinstance(role_row, dict):
        raise PatientInvitationServiceError("Admin authorization result is invalid")
    try:
        admin_id = UUID(str(row.get("id")))
        display_name = str(row.get("display_name") or "").strip()
    except (TypeError, ValueError) as exc:
        raise PatientInvitationServiceError("Admin authorization result is invalid") from exc
    if (
        admin_id != user_id
        or not display_name
        or row.get("is_active") is not True
        or str(role_row.get("user_id")) != str(user_id)
        or role_row.get("role") != "admin"
        or role_row.get("is_active") is not True
    ):
        logger.warning("admin_authorization_denied")
        raise AdminAuthorizationError("Active administrator access is required")

    logger.info("admin_authorization_succeeded")
    return CurrentAdmin(id=admin_id, display_name=display_name)


def invite_patient(
    repository: AdminChatRepository,
    details: PatientInvitationDetails,
    *,
    redirect_to: str,
) -> UUID:
    """Invite one auth user and complete the trigger-created patient profile.

    If profile completion fails after Auth created the user, Careline attempts
    to remove that new Auth user so the administrator can safely retry.
    """

    invited_user_id: UUID | None = None

    def compensate() -> None:
        """Best-effort rollback when Auth succeeded but profile setup did not."""

        if invited_user_id is None:
            return
        try:
            repository.client.auth.admin.delete_user(str(invited_user_id))
            logger.warning("patient_invitation_compensated")
        except Exception as cleanup_exc:
            logger.error(
                "patient_invitation_compensation_failed",
                extra={"error_type": type(cleanup_exc).__name__},
            )

    try:
        response = repository.client.auth.admin.invite_user_by_email(
            details.email,
            {
                "data": {"full_name": details.full_name},
                "redirect_to": redirect_to,
            },
        )
        user = getattr(response, "user", None)
        raw_user_id = getattr(user, "id", None)
        if raw_user_id is None:
            raise PatientInvitationServiceError("Invitation did not return a user")
        invited_user_id = UUID(str(raw_user_id))

        rows = (
            repository.client.table("patients")
            .update(
                {
                    "full_name": details.full_name,
                    "date_of_birth": details.date_of_birth.isoformat(),
                    "phone": details.phone,
                }
            )
            .eq("id", str(invited_user_id))
            .execute()
            .data
        )
        if not isinstance(rows, list) or len(rows) != 1:
            logger.error(
                "patient_invitation_profile_completion_failed",
                extra={"returned_row_count": len(rows) if isinstance(rows, list) else None},
            )
            raise PatientInvitationServiceError("Invited patient profile was not completed")
    except PatientInvitationServiceError:
        compensate()
        raise
    except Exception as exc:
        # Supabase errors may contain the email address or request body. Only
        # the exception category is logged or propagated.
        error_code = getattr(exc, "code", None)
        status_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        logger.warning(
            "patient_invitation_failed",
            extra={
                "error_type": type(exc).__name__,
                "provider_status_code": status_code,
                "provider_error_code": str(error_code) if error_code is not None else None,
            },
        )
        compensate()
        if error_code in {"email_exists", "user_already_exists"} or status_code == 422:
            raise PatientInvitationConflictError("Patient email is already registered") from exc
        raise PatientInvitationServiceError("Patient invitation failed") from exc

    logger.info("patient_invitation_succeeded")
    return invited_user_id


def validate_invitation_payload(payload: object) -> PatientInvitationDetails:
    """Parse a consumed database payload without leaking validation details."""

    try:
        return PatientInvitationDetails.model_validate(payload)
    except ValidationError as exc:
        logger.error(
            "patient_invitation_payload_invalid",
            extra={"validation_error_count": exc.error_count()},
        )
        raise PatientInvitationServiceError("Invitation payload is invalid") from exc
