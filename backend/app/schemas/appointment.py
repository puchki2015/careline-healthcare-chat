"""Strict contracts used by appointment REST endpoints and LangGraph tools."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppointmentIntent(StrEnum):
    LIST_APPOINTMENTS = "list_appointments"
    SEARCH_AVAILABILITY = "search_availability"
    BOOK_APPOINTMENT = "book_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    UNSUPPORTED = "unsupported"


class AppointmentDecision(BaseModel):
    """Structured model suggestion; deterministic nodes validate every value."""

    model_config = ConfigDict(extra="forbid")

    intent: AppointmentIntent
    specialty: str | None
    doctor_name: str | None
    date_from: str | None
    date_to: str | None
    appointment_id: str | None
    slot_id: str | None
    reason: str | None
    missing_fields: list[Literal["specialty", "date_from", "date_to", "appointment_id", "slot_id"]]


class DoctorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_name: str
    specialty: str


class AvailabilitySlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    starts_at: datetime
    ends_at: datetime
    location_type: Literal["in_person", "video", "phone"]
    location: str | None
    doctor: DoctorSummary


class AppointmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    starts_at: datetime
    ends_at: datetime
    status: Literal["scheduled", "cancelled", "completed", "no_show"]
    reason: str | None
    cancellation_reason: str | None
    location_type: Literal["in_person", "video", "phone"]
    location: str | None
    doctor: DoctorSummary


class AppointmentListResponse(BaseModel):
    items: list[AppointmentOut]


class AvailabilityResponse(BaseModel):
    items: list[AvailabilitySlot]


class AppointmentMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "booked",
        "rescheduled",
        "cancelled",
        "slot_unavailable",
        "appointment_unavailable",
    ]
    appointment_id: UUID | None


class AppointmentActionPayload(BaseModel):
    """Payload stored behind the hashed one-time confirmation token."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["book_appointment", "reschedule_appointment", "cancel_appointment"]
    appointment_id: UUID | None = None
    slot_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=1000)
