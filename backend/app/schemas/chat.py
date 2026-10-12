"""Validated request, model-decision and response contracts for patient chat."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChatIntent(StrEnum):
    """Small set of operations the first Careline graph is allowed to route."""

    READ_PROFILE = "read_profile"
    UPDATE_PROFILE = "update_profile"
    MANAGE_APPOINTMENT = "manage_appointment"
    UNSUPPORTED = "unsupported"


class ChatDecision(BaseModel):
    """Structured output produced by the language model.

    This object is only a suggestion. Graph nodes still apply hard-coded field
    whitelists and Pydantic value validation before reading or proposing work.
    """

    model_config = ConfigDict(extra="forbid")

    intent: ChatIntent
    # All properties are required because OpenAI strict structured output
    # expects required schema properties. Irrelevant properties use []/null.
    fields: list[str]
    key: str | None
    # Keep the provider schema explicit. OpenAI strict structured output does
    # not accept an unconstrained/empty JSON Schema. Scalar updates use their
    # normal text representation; address/contact objects use a JSON string
    # which the trusted graph parses before normal Pydantic validation.
    value: str | None


class ChatConfirmation(BaseModel):
    """Explicit browser response to a previously proposed update."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=32, max_length=100)
    approved: bool


class ChatRequest(BaseModel):
    """Accept either a new message or an explicit confirmation, never both."""

    model_config = ConfigDict(extra="forbid")

    # The conversation is created first through POST /chat/sessions. Requiring
    # its server-generated UUID prevents an untracked graph turn.
    session_id: UUID
    # The frontend should generate one UUID per user action and reuse it only
    # when retrying that same HTTP request. The database unique constraint then
    # prevents a network retry from storing/processing the message twice.
    client_message_id: UUID | None = None
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    confirmation: ChatConfirmation | None = None
    # The patient portal supplies the browser's IANA timezone. Keeping this in
    # the typed request makes phrases such as "tomorrow" deterministic. UTC is
    # retained as a backward-compatible default for Swagger and older clients.
    timezone: str = Field(default="UTC", min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def require_exactly_one_input(self) -> "ChatRequest":
        if (self.message is None) == (self.confirmation is None):
            raise ValueError("Supply exactly one of message or confirmation")
        return self


class ChatAction(StrEnum):
    """Machine-readable outcome for the frontend chat UI."""

    ANSWER = "answer"
    CONFIRMATION_REQUIRED = "confirmation_required"
    UPDATED = "updated"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    INFORMATION_REQUIRED = "information_required"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


class ChatResponse(BaseModel):
    """Safe response returned by POST /chat."""

    model_config = ConfigDict(extra="forbid")

    action: ChatAction
    message: str
    requires_confirmation: bool = False
    pending_action_id: str | None = None
    data: dict[str, Any] | None = None
    turn: "ChatTurnMetadata | None" = None


class ChatTurnMetadata(BaseModel):
    """Public identifiers that connect an API response to stored history."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    run_id: UUID
    input_message_id: UUID
    output_message_id: UUID


class ChatToolTrace(BaseModel):
    """Internal, sanitized graph/tool telemetry persisted after a turn."""

    model_config = ConfigDict(extra="forbid")

    node_name: str = Field(min_length=1, max_length=100)
    tool_name: str = Field(min_length=1, max_length=100)
    duration_ms: float = Field(ge=0)
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class ChatPendingActionDraft(BaseModel):
    """Internal pending action inserted with its proposal response."""

    model_config = ConfigDict(extra="forbid")

    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_type: Literal[
        "update_profile",
        "book_appointment",
        "reschedule_appointment",
        "cancel_appointment",
    ] = "update_profile"
    action_payload: dict[str, Any]
    expires_at: datetime
