"""Deterministic appointment search and transaction handoff tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas.appointment import AppointmentActionPayload
from app.services.appointment_service import (
    AppointmentConflictError,
    execute_confirmed_action,
    search_availability,
)


class FakeRepository:
    def __init__(self, result) -> None:
        self.result = result
        self.call = None

    def mutate(self, function_name, values):
        self.call = (function_name, values)
        return self.result


class FakeSearchRepository:
    def __init__(self, row) -> None:
        self.row = row
        self.query = None

    def select_availability(self, **kwargs):
        self.query = kwargs
        return [self.row]


def test_practitioner_title_is_normalized_to_database_specialty() -> None:
    doctor_id = uuid4()
    slot_id = uuid4()
    repository = FakeSearchRepository(
        {
            "id": str(slot_id),
            "starts_at": "2099-04-01T17:00:00+00:00",
            "ends_at": "2099-04-01T17:30:00+00:00",
            "location_type": "video",
            "location": "Careline Video",
            "doctor_profiles": {
                "id": str(doctor_id),
                "display_name": "Dr. Rivera",
                "specialty": "Cardiology",
                "is_active": True,
            },
        }
    )

    results = search_availability(
        repository,  # type: ignore[arg-type]
        starts_at=datetime(2099, 4, 1, tzinfo=UTC),
        ends_at=datetime(2099, 4, 2, tzinfo=UTC),
        specialty="cardiologist",
        doctor_name=None,
    )

    assert repository.query["specialty"] == "Cardiology"
    assert results[0].doctor.specialty == "Cardiology"


def test_confirmed_booking_uses_verified_patient_as_actor() -> None:
    patient_id = uuid4()
    appointment_id = uuid4()
    slot_id = uuid4()
    repository = FakeRepository([{"outcome": "booked", "appointment_id": str(appointment_id)}])
    result = execute_confirmed_action(
        repository,  # type: ignore[arg-type]
        patient_id,
        AppointmentActionPayload(operation="book_appointment", slot_id=slot_id),
    )
    assert result.appointment_id == appointment_id
    assert repository.call[0] == "careline_book_appointment"
    assert repository.call[1]["p_patient_id"] == str(patient_id)
    assert repository.call[1]["p_actor_id"] == str(patient_id)


def test_unavailable_slot_becomes_safe_conflict() -> None:
    repository = FakeRepository([{"outcome": "slot_unavailable", "appointment_id": None}])
    with pytest.raises(AppointmentConflictError):
        execute_confirmed_action(
            repository,  # type: ignore[arg-type]
            uuid4(),
            AppointmentActionPayload(operation="book_appointment", slot_id=uuid4()),
        )
