"""Validated application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Careline runtime settings.

    Required values have no defaults so the application fails during startup
    instead of running with missing credentials. SecretStr prevents accidental
    disclosure when settings are printed or included in an exception.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIRECTORY / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Careline API"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    supabase_url: AnyHttpUrl
    supabase_anon_key: SecretStr = Field(min_length=1)
    supabase_service_role_key: SecretStr = Field(min_length=1)
    frontend_origin: AnyHttpUrl

    # The API can still start without an OpenAI key so health, authentication
    # and deterministic patient endpoints remain usable. Only /chat returns a
    # configuration error until the key is supplied.
    openai_api_key: SecretStr | None = None
    # OPENAI_MODEL is the preferred public name. CHAT_MODEL remains accepted
    # so existing developer environments continue to work; when both are set,
    # CHAT_MODEL wins because it appears first in AliasChoices.
    chat_model: str = Field(
        default="gpt-5.4-mini",
        validation_alias=AliasChoices("CHAT_MODEL", "OPENAI_MODEL"),
    )
    chat_pending_action_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    chat_prompt_version: str = Field(default="patient-router-v1", min_length=1, max_length=100)
    chat_context_message_limit: int = Field(default=20, ge=0, le=50)
    # Cross-session memory is intentionally small. Only summaries from this
    # many closed conversations are linked to a newly created session.
    chat_prior_session_limit: int = Field(default=3, ge=0, le=10)
    # Closing a session summarizes only its most recent bounded messages. This
    # prevents an unexpectedly large provider request for a long conversation.
    chat_summary_message_limit: int = Field(default=30, ge=1, le=100)
    chat_summary_max_chars: int = Field(default=2000, ge=200, le=4000)
    admin_chat_prompt_version: str = Field(
        default="admin-patient-invitation-v1",
        min_length=1,
        max_length=100,
    )
    patient_invitation_redirect_url: AnyHttpUrl | None = None

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Require one clean path prefix such as ``/api/v1``."""

        normalized = value.rstrip("/")
        if not normalized.startswith("/") or normalized == "":
            raise ValueError("API prefix must begin with '/' and cannot be empty")
        return normalized

    @property
    def frontend_origin_value(self) -> str:
        """Return an exact browser Origin value without a trailing slash.

        Pydantic normalizes a URL like ``http://localhost:5173`` by appending
        ``/``. Browser Origin headers omit that slash, so CORS comparison must
        use the normalized value below.
        """

        return str(self.frontend_origin).rstrip("/")

    @property
    def supabase_issuer(self) -> str:
        """Return the exact issuer Supabase places in access tokens."""

        return f"{str(self.supabase_url).rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        """Return the public endpoint containing Supabase JWT signing keys."""

        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @property
    def patient_invitation_redirect_url_value(self) -> str:
        """Return the configured invite landing page or the frontend default."""

        if self.patient_invitation_redirect_url is not None:
            return str(self.patient_invitation_redirect_url)
        return f"{self.frontend_origin_value}/set-password"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process to avoid repeatedly parsing ``.env``."""

    return Settings()  # type: ignore[call-arg]
