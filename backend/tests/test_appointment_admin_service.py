"""Unit tests for privileged provider and availability management."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.schemas.admin import CurrentAdmin
from app.schemas.appointment_admin import AvailabilityCreate, DoctorCreate
from app.services.appointment_admin_service import (
    create_availability,
    create_doctor,
    set_availability_status,
)
from app.services.appointment_service import AppointmentConflictError


class FakeRepository:
    def __init__(self) -> None:
        self.doctor_id = uuid4()
        self.slot_id = uuid4()
        self.status_rows = []

    def insert_doctor(self, values):
        now = datetime.now(UTC).isoformat()
        return [
            {
                "id": str(self.doctor_id),
                **values,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        ]

    def insert_availability(self, values):
        now = datetime.now(UTC).isoformat()
        self.slot_values = {"id": str(self.slot_id), **values, "created_at": now, "updated_at": now}
        return [self.slot_values]

    def select_admin_availability(self, **kwargs):
        if not hasattr(self, "slot_values"):
            return []
        now = datetime.now(UTC).isoformat()
        return [
            {
                **self.slot_values,
                "doctor_profiles": {
                    "id": str(self.doctor_id),
                    "display_name": "Dr. Rivera",
                    "specialty": "Cardiology",
                    "timezone": "UTC",
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            }
        ]

    def update_availability_status(self, slot_id, status):
        return self.status_rows


def admin() -> CurrentAdmin:
    return CurrentAdmin(id=uuid4(), display_name="Admin User")


def test_admin_can_create_doctor() -> None:
    repository = FakeRepository()
    result = create_doctor(
        repository,  # type: ignore[arg-type]
        admin(),
        DoctorCreate(display_name="Dr. Rivera", specialty="Cardiology", timezone="UTC"),
    )
    assert result.id == repository.doctor_id
    assert result.specialty == "Cardiology"


def test_admin_can_publish_future_availability() -> None:
    repository = FakeRepository()
    starts_at = datetime.now(UTC) + timedelta(days=2)
    result = create_availability(
        repository,  # type: ignore[arg-type]
        admin(),
        AvailabilityCreate(
            doctor_id=repository.doctor_id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            location_type="video",
        ),
    )
    assert result.id == repository.slot_id
    assert result.status == "available"


def test_booked_slot_cannot_be_blocked() -> None:
    repository = FakeRepository()
    with pytest.raises(AppointmentConflictError):
        set_availability_status(
            repository,  # type: ignore[arg-type]
            admin(),
            repository.slot_id,
            "blocked",
        )
