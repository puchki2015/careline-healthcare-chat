"""Reusable FastAPI dependencies for request authentication and settings."""

import logging
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client

from app.core.config import Settings
from app.core.security import AuthenticationError, verify_access_token
from app.core.supabase import create_user_client, get_admin_client
from app.repositories.admin_chat_repository import AdminChatRepository
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.chat_history_repository import ChatHistoryRepository
from app.schemas.admin import CurrentAdmin
from app.schemas.auth import CurrentUser
from app.services.admin_service import require_active_admin

logger = logging.getLogger(__name__)

# auto_error=False lets our exception handler return the same documented error
# shape for missing, malformed, and invalid credentials.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="Supabase access token",
    description="Paste a Supabase access token. Do not include the word Bearer.",
)


def get_request_settings(request: Request) -> Settings:
    """Read the settings instance attached by the application factory."""

    return request.app.state.settings


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> CurrentUser:
    """Authenticate the request and return a verified Supabase identity."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        logger.warning("authentication_failed", extra={"auth_failure_reason": "missing_bearer"})
        raise AuthenticationError("invalid_token")

    try:
        current_user = verify_access_token(credentials.credentials, settings)
    except AuthenticationError as exc:
        # The safe category supports operations/debugging without recording the
        # token, its claims, its signing key ID, or any patient identifier.
        logger.warning(
            "authentication_failed",
            extra={"auth_failure_reason": exc.reason},
        )
        raise

    logger.info("authentication_succeeded")
    return current_user


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]


def get_user_supabase_client(
    current_user: CurrentUserDependency,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> Client:
    """Provide a request-local Supabase client carrying the patient's JWT."""

    return create_user_client(current_user, settings)


# Patient routes can request this alias without knowing how credentials are
# attached. There is deliberately no equivalent route-level admin dependency;
# privileged access remains confined to service-layer code.
UserSupabaseClientDependency = Annotated[Client, Depends(get_user_supabase_client)]


def get_admin_supabase_client(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> Client:
    """Provide the cached service-role client for explicitly privileged writes.

    Routes should request this dependency only when their service operation is
    designed for a backend-only table or command. Ordinary patient reads stay
    on ``UserSupabaseClientDependency`` so RLS remains active.
    """

    return get_admin_client(settings)


AdminSupabaseClientDependency = Annotated[Client, Depends(get_admin_supabase_client)]


def get_appointment_repository(
    admin_client: AdminSupabaseClientDependency,
) -> AppointmentRepository:
    """Provide backend-only appointment access; callers still pass verified patient IDs."""

    return AppointmentRepository(admin_client)


AppointmentRepositoryDependency = Annotated[
    AppointmentRepository,
    Depends(get_appointment_repository),
]


def get_chat_history_repository(
    patient_client: UserSupabaseClientDependency,
    admin_client: AdminSupabaseClientDependency,
) -> ChatHistoryRepository:
    """Combine the RLS read client and privileged write client explicitly."""

    return ChatHistoryRepository(read_client=patient_client, write_client=admin_client)


ChatHistoryRepositoryDependency = Annotated[
    ChatHistoryRepository,
    Depends(get_chat_history_repository),
]


def get_admin_chat_repository(
    admin_client: AdminSupabaseClientDependency,
) -> AdminChatRepository:
    """Provide the only persistence gateway used by administrator chat."""

    return AdminChatRepository(admin_client)


AdminChatRepositoryDependency = Annotated[
    AdminChatRepository,
    Depends(get_admin_chat_repository),
]


def get_current_admin(
    current_user: CurrentUserDependency,
    repository: AdminChatRepositoryDependency,
) -> CurrentAdmin:
    """Require both a valid JWT and an active database admin profile."""

    return require_active_admin(repository, current_user.id)


CurrentAdminDependency = Annotated[CurrentAdmin, Depends(get_current_admin)]
