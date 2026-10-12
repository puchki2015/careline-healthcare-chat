"""Administrator contracts for provider schedules and appointment oversight."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]


class DoctorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: ShortText
    specialty: ShortText
    timezone: ShortText = "UTC"


class DoctorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: ShortText | None = None
    specialty: ShortText | None = None
    timezone: ShortText | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one doctor field is required")
        return self


class DoctorAdminOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    # Optional until a provider receives a doctor-login account. Keeping the
    # link in the contract matches the canonical Supabase row.
    user_id: UUID | None = None
    display_name: str
    specialty: str
    timezone: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AvailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doctor_id: UUID
    starts_at: datetime
    ends_at: datetime
    location_type: Literal["in_person", "video", "phone"]
    location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)] | None = None

    @model_validator(mode="after")
    def validate_time(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Timezone-aware timestamps are required")
        if self.ends_at <= self.starts_at:
            raise ValueError("Slot end must be after start")
        return self


class AvailabilityStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available", "blocked"]


class AvailabilityAdminOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    doctor_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: Literal["available", "booked", "blocked"]
    location_type: Literal["in_person", "video", "phone"]
    location: str | None
    doctor: DoctorAdminOut
    created_at: datetime
    updated_at: datetime


class AdminAppointmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    patient_id: UUID
    patient_name: str | None
    patient_email: str | None
    starts_at: datetime
    ends_at: datetime
    status: Literal["scheduled", "cancelled", "completed", "no_show"]
    reason: str | None
    cancellation_reason: str | None
    doctor: DoctorAdminOut
    location_type: Literal["in_person", "video", "phone"]
    location: str | None
    created_at: datetime


class DoctorListResponse(BaseModel):
    items: list[DoctorAdminOut]


class AvailabilityAdminListResponse(BaseModel):
    items: list[AvailabilityAdminOut]


class AdminAppointmentListResponse(BaseModel):
    items: list[AdminAppointmentOut]
