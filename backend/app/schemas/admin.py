"""Strict contracts for administrator identity and patient-invitation chat."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.chat_history import ChatContextMessage, ChatInitiatorRole
from app.schemas.patient import DateOfBirthValue, PhoneNumber, TrimmedName

EmailAddress = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    ),
]


class CurrentAdmin(BaseModel):
    """Active administrator verified from the database after JWT validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    display_name: str


class AdminChatIntent(StrEnum):
    CREATE_PATIENT = "create_patient"
    UNSUPPORTED = "unsupported"


class AdminChatDecision(BaseModel):
    """Model-extracted invitation intent and all currently known fields."""

    model_config = ConfigDict(extra="forbid")

    intent: AdminChatIntent
    full_name: str | None
    date_of_birth: str | None
    phone: str | None
    email: str | None
    missing_fields: list[Literal["full_name", "date_of_birth", "phone", "email"]]


class PatientInvitationDetails(BaseModel):
    """Deterministically validated information used for a Supabase invitation."""

    model_config = ConfigDict(extra="forbid")

    full_name: TrimmedName
    date_of_birth: DateOfBirthValue
    phone: PhoneNumber
    email: EmailAddress

    @field_validator("date_of_birth")
    @classmethod
    def require_date_of_birth(cls, value: date | None) -> date:
        if value is None:
            raise ValueError("Date of birth is required")
        return value


class AdminChatConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=32, max_length=100)
    approved: bool


class AdminChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    client_message_id: UUID | None = None
    message: str | None = Field(default=None, min_length=1, max_length=3000)
    confirmation: AdminChatConfirmation | None = None

    @model_validator(mode="after")
    def require_exactly_one_input(self) -> "AdminChatRequest":
        if (self.message is None) == (self.confirmation is None):
            raise ValueError("Supply exactly one of message or confirmation")
        return self


class AdminChatAction(StrEnum):
    INFORMATION_REQUIRED = "information_required"
    CONFIRMATION_REQUIRED = "confirmation_required"
    PATIENT_INVITED = "patient_invited"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


class AdminTurnMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    run_id: UUID
    input_message_id: UUID
    output_message_id: UUID


class AdminChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AdminChatAction
    message: str
    requires_confirmation: bool = False
    pending_action_id: str | None = None
    data: dict[str, Any] | None = None
    turn: AdminTurnMetadata | None = None


class AdminPendingActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_type: Literal["create_patient"] = "create_patient"
    action_payload: dict[str, Any]
    expires_at: datetime


class AdminChatSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        ]
        | None
    ) = None


class AdminChatSessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str | None
    status: Literal["active", "closed", "archived"]
    summary: str | None
    started_at: datetime
    last_message_at: datetime
    ended_at: datetime | None
    subject_patient_id: UUID | None


class AdminChatSessionRecord(AdminChatSessionOut):
    model_config = ConfigDict(extra="forbid")

    chat_initiated_by: UUID
    chat_initiator_role: ChatInitiatorRole
    model_name: str
    prompt_version: str
    created_at: datetime
    updated_at: datetime


class AdminChatSessionPage(BaseModel):
    """Cursor page used by an admin frontend to restore chat history."""

    items: list[AdminChatSessionOut]
    next_cursor: str | None


class AdminChatTurnStartRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["started", "not_found", "not_active", "duplicate", "in_progress"]
    run_id: UUID | None
    input_message_id: UUID | None
    input_sequence: int | None


class AdminChatTurnCompleteRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["completed", "not_found", "not_started"]
    output_message_id: UUID | None


class AdminPendingConsumeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["consumed", "unavailable"]
    action_payload: dict[str, Any] | None


class AdminStartedTurn(BaseModel):
    """Validated identifiers/context returned by the start-turn service."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    session_id: UUID
    run_id: UUID
    input_message_id: UUID
    input_sequence: int
    context: tuple[ChatContextMessage, ...]
    started_at: float
