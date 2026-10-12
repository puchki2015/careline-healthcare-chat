"""Public and internal contracts for persistent Careline conversations."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ChatSessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class ChatInitiatorRole(StrEnum):
    """Application roles allowed to own a conversation."""

    PATIENT = "patient"
    ADMIN = "admin"
    DOCTOR = "doctor"


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM_NOTICE = "system_notice"


class ChatMessageType(StrEnum):
    TEXT = "text"
    PROFILE_RESULT = "profile_result"
    # Administrator invitation turns share the same chat_messages table and
    # public message response model as patient turns. These values must remain
    # accepted here so an admin can restore a message that the admin graph has
    # already persisted.
    INFORMATION_REQUEST = "information_request"
    INVITATION_RESULT = "invitation_result"
    CONFIRMATION_REQUEST = "confirmation_request"
    CONFIRMATION_RESULT = "confirmation_result"
    ERROR = "error"
    SYSTEM_NOTICE = "system_notice"


SessionTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class ChatSessionCreate(BaseModel):
    """Optional user-visible title for a new conversation."""

    model_config = ConfigDict(extra="forbid")
    title: SessionTitle | None = None


class ChatSessionUpdate(BaseModel):
    """User-editable session metadata; ownership remains server-derived."""

    model_config = ConfigDict(extra="forbid")

    title: SessionTitle


class ChatSessionRecord(BaseModel):
    """Strict representation of a complete database session row."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    chat_initiated_by: UUID
    chat_initiator_role: ChatInitiatorRole
    subject_patient_id: UUID | None
    title: str | None
    status: ChatSessionStatus
    summary: str | None
    model_name: str
    prompt_version: str
    started_at: datetime
    last_message_at: datetime
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChatSessionOut(BaseModel):
    """Patient-facing session representation without redundant owner UUID."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str | None
    status: ChatSessionStatus
    summary: str | None
    model_name: str
    prompt_version: str
    started_at: datetime
    last_message_at: datetime
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ChatSessionRecord) -> "ChatSessionOut":
        return cls.model_validate(
            record.model_dump(
                exclude={"chat_initiated_by", "chat_initiator_role", "subject_patient_id"}
            )
        )


class ChatSessionPage(BaseModel):
    items: list[ChatSessionOut]
    next_cursor: str | None


class ChatMessageRecord(BaseModel):
    """Strict representation of a complete database message row."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    session_id: UUID
    chat_initiated_by: UUID
    chat_initiator_role: ChatInitiatorRole
    sequence_number: int = Field(gt=0)
    role: ChatMessageRole
    message_type: ChatMessageType
    content: str
    structured_data: dict[str, Any] | None
    client_message_id: UUID | None
    sent_by_user_id: UUID | None
    sent_by_role: ChatInitiatorRole | None
    created_at: datetime


class ChatMessageOut(BaseModel):
    """Patient-facing message without the redundant owner UUID."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    session_id: UUID
    sequence_number: int
    role: ChatMessageRole
    message_type: ChatMessageType
    content: str
    structured_data: dict[str, Any] | None
    client_message_id: UUID | None
    sent_by_user_id: UUID | None
    sent_by_role: ChatInitiatorRole | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: ChatMessageRecord) -> "ChatMessageOut":
        return cls.model_validate(
            record.model_dump(exclude={"chat_initiated_by", "chat_initiator_role"})
        )


class ChatMessagePage(BaseModel):
    session_id: UUID
    items: list[ChatMessageOut]
    next_cursor: str | None


class ChatTurnStartRecord(BaseModel):
    """Result returned by the transactional start-turn database function."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "started",
        "not_found",
        "not_active",
        "duplicate",
        "in_progress",
    ]
    run_id: UUID | None
    input_message_id: UUID | None
    input_sequence: int | None


class ChatTurnCompleteRecord(BaseModel):
    """Result returned by the transactional complete-turn database function."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["completed", "not_found", "not_started"]
    output_message_id: UUID | None


class ChatContextMessage(BaseModel):
    """Minimal prior message passed to the classifier in chronological order."""

    model_config = ConfigDict(extra="forbid")

    sequence_number: int = Field(gt=0)
    role: ChatMessageRole
    content: str
    # Tool results are included so a patient can say "book the first option"
    # after an availability response. They remain untrusted model input.
    structured_data: dict[str, Any] | None = None


class ChatSessionMemoryRecord(BaseModel):
    """One audited prior-session summary supplied as historical context."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(gt=0)
    source_session_id: UUID
    summary: str = Field(min_length=1, max_length=4000)


class ChatSummaryResult(BaseModel):
    """Strict structured output generated when an active session is closed."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000)


class PendingActionConsumeRecord(BaseModel):
    """Result returned by the transactional pending-action consumer."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["consumed", "unavailable"]
    action_payload: dict[str, Any] | None
