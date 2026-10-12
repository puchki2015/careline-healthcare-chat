"""Appointment specialist tests use fakes and never call OpenAI or Supabase."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.agents.appointment_graph import build_appointment_graph
from app.schemas.appointment import AppointmentDecision, AppointmentIntent


class FakeAppointmentClassifier:
    def __init__(self, decision: AppointmentDecision) -> None:
        self.decision = decision

    def classify(self, message, history=()):
        return self.decision


class FakeAppointmentRepository:
    def __init__(self, *, slots=None, appointments=None) -> None:
        self.slots = slots or []
        self.appointments = appointments or []
        self.availability_query = None

    def select_availability(self, **kwargs):
        self.availability_query = kwargs
        return self.slots

    def select_patient_appointments(self, patient_id, *, include_past):
        return self.appointments

    def select_available_slot(self, slot_id):
        return self.slots


def decision(intent: AppointmentIntent, **overrides) -> AppointmentDecision:
    values = {
        "intent": intent,
        "specialty": None,
        "doctor_name": None,
        "date_from": None,
        "date_to": None,
        "appointment_id": None,
        "slot_id": None,
        "reason": None,
        "missing_fields": [],
    }
    values.update(overrides)
    return AppointmentDecision.model_validate(values)


def test_search_availability_returns_real_slot_and_trace() -> None:
    doctor_id = uuid4()
    slot_id = uuid4()
    repository = FakeAppointmentRepository(
        slots=[
            {
                "id": str(slot_id),
                "starts_at": "2099-04-01T10:00:00+00:00",
                "ends_at": "2099-04-01T10:30:00+00:00",
                "location_type": "video",
                "location": None,
                "doctor_profiles": {
                    "id": str(doctor_id),
                    "display_name": "Dr. Rivera",
                    "specialty": "Cardiology",
                    "is_active": True,
                },
            }
        ]
    )
    graph = build_appointment_graph(
        classifier=FakeAppointmentClassifier(
            decision(
                AppointmentIntent.SEARCH_AVAILABILITY,
                specialty="Cardiology",
                date_from="2099-04-01T00:00:00+00:00",
                date_to="2099-04-02T00:00:00+00:00",
            )
        ),
        repository=repository,  # type: ignore[arg-type]
        patient_id=uuid4(),
        pending_action_ttl_seconds=600,
    )
    result = graph.invoke({"message": "Find a cardiologist on April 1"})
    assert result["response"].data["available_slots"][0]["id"] == str(slot_id)
    assert "is_active" not in result["response"].data["available_slots"][0]["doctor"]
    assert result["traces"][-1].tool_name == "appointment.availability.search"


def test_booking_creates_hash_only_confirmation() -> None:
    slot_id = uuid4()
    doctor_id = uuid4()
    graph = build_appointment_graph(
        classifier=FakeAppointmentClassifier(
            decision(AppointmentIntent.BOOK_APPOINTMENT, slot_id=str(slot_id))
        ),
        repository=FakeAppointmentRepository(
            slots=[
                {
                    "id": str(slot_id),
                    "starts_at": "2099-04-01T10:00:00+00:00",
                    "ends_at": "2099-04-01T10:30:00+00:00",
                    "location_type": "video",
                    "location": None,
                    "doctor_profiles": {
                        "id": str(doctor_id),
                        "display_name": "Dr. Rivera",
                        "specialty": "Cardiology",
                        "is_active": True,
                    },
                }
            ]
        ),  # type: ignore[arg-type]
        patient_id=uuid4(),
        pending_action_ttl_seconds=600,
    )
    result = graph.invoke({"message": f"Book slot {slot_id}"})
    response = result["response"]
    pending = result["pending_action"]
    assert response.requires_confirmation is True
    assert pending.action_type == "book_appointment"
    assert response.pending_action_id not in pending.model_dump_json()
    assert pending.action_payload["operation"] == "book_appointment"


def test_booking_without_slot_asks_for_information() -> None:
    graph = build_appointment_graph(
        classifier=FakeAppointmentClassifier(
            decision(
                AppointmentIntent.BOOK_APPOINTMENT,
                missing_fields=["slot_id"],
            )
        ),
        repository=FakeAppointmentRepository(),  # type: ignore[arg-type]
        patient_id=uuid4(),
        pending_action_ttl_seconds=600,
    )
    result = graph.invoke({"message": "Book a cardiologist"})
    assert result["response"].action == "information_required"


def test_natural_date_phrase_is_resolved_before_availability_search() -> None:
    repository = FakeAppointmentRepository()
    graph = build_appointment_graph(
        classifier=FakeAppointmentClassifier(
            decision(
                AppointmentIntent.SEARCH_AVAILABILITY,
                specialty="Cardiology",
                date_from="next week",
                missing_fields=["date_to"],
            )
        ),
        repository=repository,  # type: ignore[arg-type]
        patient_id=uuid4(),
        pending_action_ttl_seconds=600,
        timezone_name="America/Vancouver",
        now_provider=lambda zone: datetime(2026, 10, 9, 20, 46, tzinfo=zone),
    )

    result = graph.invoke({"message": "next week"})

    assert result["response"].action == "answer"
    assert repository.availability_query["starts_at"] == datetime(
        2026, 10, 12, tzinfo=ZoneInfo("America/Vancouver")
    ).isoformat()
    assert repository.availability_query["ends_at"] == datetime(
        2026, 10, 19, tzinfo=ZoneInfo("America/Vancouver")
    ).isoformat()
