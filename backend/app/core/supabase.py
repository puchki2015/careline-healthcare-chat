"""Supabase client factories with strict patient/admin separation.

Normal patient operations receive a fresh client carrying the verified user's
JWT, which allows PostgreSQL Row Level Security to evaluate ``auth.uid()``.
Administrative operations use a separately cached service-role client. Keeping
these construction paths separate reduces the chance of accidentally using
privileged credentials for an ordinary patient request.
"""

import logging
from functools import lru_cache
from typing import Literal

from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.core.config import Settings
from app.schemas.auth import CurrentUser

logger = logging.getLogger(__name__)


class SupabaseClientCreationError(Exception):
    """Safe internal error raised when a Supabase client cannot be created."""

    def __init__(self, client_scope: Literal["user", "admin"]) -> None:
        super().__init__(f"Unable to create {client_scope} Supabase client")
        self.client_scope = client_scope


def create_user_client(current_user: CurrentUser, settings: Settings) -> Client:
    """Create a request-local Supabase client authenticated as one patient.

    The anon key identifies the application, while the already verified access
    token identifies the patient. Supplying the token as the Authorization
    header means PostgREST evaluates RLS policies using that patient's
    ``auth.uid()``. This client must never be cached or shared between requests.
    """

    access_token = current_user.access_token.get_secret_value()
    options = SyncClientOptions(
        headers={"Authorization": f"Bearer {access_token}"},
        # FastAPI already receives and validates the token. This server-side
        # request client should not persist or independently refresh sessions.
        auto_refresh_token=False,
        persist_session=False,
    )

    try:
        client = create_client(
            str(settings.supabase_url).rstrip("/"),
            settings.supabase_anon_key.get_secret_value(),
            options=options,
        )
    except Exception as exc:
        # Library exception messages may include URLs or configuration values,
        # so only the exception type and safe client scope are logged.
        logger.error(
            "supabase_client_creation_failed",
            extra={"client_scope": "user", "error_type": type(exc).__name__},
        )
        raise SupabaseClientCreationError("user") from exc

    logger.debug("supabase_client_created", extra={"client_scope": "user"})
    return client


@lru_cache(maxsize=8)
def _create_cached_admin_client(supabase_url: str, service_role_key: str) -> Client:
    """Create and cache one privileged client per configuration pair.

    This private function accepts plain strings only because ``lru_cache``
    requires hashable arguments. Callers must use ``get_admin_client`` so
    SecretStr unwrapping remains confined to this module.
    """

    options = SyncClientOptions(
        auto_refresh_token=False,
        persist_session=False,
    )

    try:
        client = create_client(supabase_url, service_role_key, options=options)
    except Exception as exc:
        logger.error(
            "supabase_client_creation_failed",
            extra={"client_scope": "admin", "error_type": type(exc).__name__},
        )
        raise SupabaseClientCreationError("admin") from exc

    # The key, URL, client representation and admin authorization headers must
    # never appear in logs. Scope is sufficient to confirm successful setup.
    logger.info("supabase_client_created", extra={"client_scope": "admin"})
    return client


def get_admin_client(settings: Settings) -> Client:
    """Return the cached service-role client for controlled admin operations.

    This function is intended for explicitly privileged service-layer writes,
    including permanent account deletion and backend-owned chat-history rows.
    Patient-visible chat reads still use the patient's JWT/RLS client. The
    privileged client must never be exposed to request data or the browser.
    """

    logger.debug("supabase_client_requested", extra={"client_scope": "admin"})
    return _create_cached_admin_client(
        str(settings.supabase_url).rstrip("/"),
        settings.supabase_service_role_key.get_secret_value(),
    )
