"""Authentication-related models shared by dependencies and routes."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr


class CurrentUser(BaseModel):
    """Verified identity derived only from a signed Supabase access token.

    ``access_token`` remains available for future user-scoped Supabase calls,
    but SecretStr prevents its value from appearing in model representations,
    tracebacks, or routine logs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    access_token: SecretStr


class AuthVerificationResponse(BaseModel):
    """Minimal response proving that bearer authentication succeeded."""

    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True]
