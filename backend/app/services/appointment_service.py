"""Deterministic appointment tools shared by REST and the LangGraph agent."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.appointment import (
    AppointmentActionPayload,
    AppointmentMutationResult,
    AppointmentOut,
    AvailabilitySlot,
)

logger = logging.getLogger(__name__)

# Database specialties are category names, while patients often use the
# corresponding practitioner title. Normalize only explicit, well-understood
# aliases; unknown values remain untouched instead of being guessed.
_SPECIALTY_ALIASES = {
    "cardiologist": "Cardiology",
    "heart doctor": "Cardiology",
    "dermatologist": "Dermatology",
    "neurologist": "Neurology",
    "pediatrician": "Pediatrics",
    "paediatrician": "Pediatrics",
    "psychiatrist": "Psychiatry",
    "oncologist": "Oncology",
    "orthopedist": "Orthopedics",
    "orthopaedist": "Orthopedics",
}


class AppointmentValidationError(ValueError):
    """The requested date range, identifier, or operation is unsafe/invalid."""


class AppointmentConflictError(RuntimeError):
    """A slot or appointment changed before the confirmed action completed."""


class AppointmentServiceUnavailableError(RuntimeError):
    """Supabase could not complete an appointment operation."""


class AppointmentDataIntegrityError(RuntimeError):
    """Stored appointment data does not match the backend contract."""


def _normalize_specialty(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.strip().split())
    return _SPECIALTY_ALIASES.get(cleaned.casefold(), cleaned)


def _as_rows(raw: object, operation: str) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        logger.error("appointment_result_invalid", extra={"operation": operation})
        raise AppointmentDataIntegrityError("Appointment result is invalid")
    return raw


def _doctor(row: dict[str, object]) -> object:
    """Map a database join to the strict patient-facing doctor summary.

    Availability queries select ``is_active`` for server-side filtering. That
    internal flag is deliberately not part of ``DoctorSummary`` and must not
    leak into response validation or API output.
    """

    joined = row.get("doctor_profiles")
    if not isinstance(joined, dict):
        # Preserve malformed join values so Pydantic raises the expected data
        # integrity error instead of silently manufacturing doctor data.
        return joined
    return {
        "id": joined.get("id"),
        "display_name": joined.get("display_name"),
        "specialty": joined.get("specialty"),
    }


def list_appointments(
    repository: AppointmentRepository, patient_id: UUID, *, include_past: bool = False
) -> list[AppointmentOut]:
    try:
        rows = _as_rows(
            repository.select_patient_appointments(str(patient_id), include_past=include_past),
            "list",
        )
        results = []
        for row in rows:
            availability = row.pop("provider_availability", None)
            if not isinstance(availability, dict):
                raise AppointmentDataIntegrityError("Appointment availability is invalid")
            row["doctor"] = _doctor(row)
            row.pop("doctor_profiles", None)
            row["location_type"] = availability.get("location_type")
            row["location"] = availability.get("location")
            results.append(AppointmentOut.model_validate(row))
    except AppointmentDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error("appointment_validation_failed", extra={"operation": "list"})
        raise AppointmentDataIntegrityError("Stored appointment data is invalid") from exc
    except Exception as exc:
        logger.error(
            "appointment_query_failed",
            extra={"operation": "list", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Appointment query failed") from exc
    logger.info("appointments_retrieved", extra={"appointment_count": len(results)})
    return results


def search_availability(
    repository: AppointmentRepository,
    *,
    starts_at: datetime,
    ends_at: datetime,
    specialty: str | None,
    doctor_name: str | None,
) -> list[AvailabilitySlot]:
    if starts_at.tzinfo is None or ends_at.tzinfo is None or ends_at <= starts_at:
        raise AppointmentValidationError("A valid timezone-aware date range is required")
    if ends_at <= datetime.now(UTC):
        raise AppointmentValidationError("Availability must be searched in the future")
    if (ends_at - starts_at).days > 62:
        raise AppointmentValidationError("Availability searches are limited to 62 days")
    normalized_specialty = _normalize_specialty(specialty)
    try:
        rows = _as_rows(
            repository.select_availability(
                starts_at=starts_at.isoformat(),
                ends_at=ends_at.isoformat(),
                specialty=normalized_specialty,
                doctor_name=doctor_name,
            ),
            "search",
        )
        results = []
        for row in rows:
            row["doctor"] = _doctor(row)
            row.pop("doctor_profiles", None)
            results.append(AvailabilitySlot.model_validate(row))
    except AppointmentDataIntegrityError:
        raise
    except ValidationError as exc:
        logger.error("appointment_validation_failed", extra={"operation": "search"})
        raise AppointmentDataIntegrityError("Stored availability is invalid") from exc
    except Exception as exc:
        logger.error(
            "appointment_query_failed",
            extra={"operation": "search", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Availability query failed") from exc
    logger.info("appointment_availability_retrieved", extra={"slot_count": len(results)})
    return results


def get_available_slot(repository: AppointmentRepository, slot_id: UUID) -> AvailabilitySlot:
    """Resolve an exact real slot before asking the patient to confirm it."""

    try:
        rows = _as_rows(repository.select_available_slot(str(slot_id)), "get_slot")
        if len(rows) != 1:
            raise AppointmentConflictError("The appointment slot is not available")
        row = rows[0]
        row["doctor"] = _doctor(row)
        row.pop("doctor_profiles", None)
        result = AvailabilitySlot.model_validate(row)
    except AppointmentConflictError:
        raise
    except ValidationError as exc:
        raise AppointmentDataIntegrityError("Stored availability is invalid") from exc
    except Exception as exc:
        logger.error(
            "appointment_query_failed",
            extra={"operation": "get_slot", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Availability query failed") from exc
    if result.starts_at <= datetime.now(UTC):
        raise AppointmentConflictError("The appointment slot is not available")
    return result


def execute_confirmed_action(
    repository: AppointmentRepository,
    patient_id: UUID,
    payload: AppointmentActionPayload,
) -> AppointmentMutationResult:
    """Execute only a previously stored and explicitly approved action."""

    common = {
        "p_patient_id": str(patient_id),
        "p_actor_id": str(patient_id),
        "p_actor_role": "patient",
    }
    if payload.operation == "book_appointment":
        if payload.slot_id is None:
            raise AppointmentValidationError("Booking slot is missing")
        function_name = "careline_book_appointment"
        values = {**common, "p_slot_id": str(payload.slot_id), "p_reason": payload.reason or ""}
    elif payload.operation == "reschedule_appointment":
        if payload.slot_id is None or payload.appointment_id is None:
            raise AppointmentValidationError("Reschedule identifiers are missing")
        function_name = "careline_reschedule_appointment"
        values = {
            **common,
            "p_appointment_id": str(payload.appointment_id),
            "p_new_slot_id": str(payload.slot_id),
        }
    else:
        if payload.appointment_id is None:
            raise AppointmentValidationError("Cancellation appointment is missing")
        function_name = "careline_cancel_appointment"
        values = {
            **common,
            "p_appointment_id": str(payload.appointment_id),
            "p_reason": payload.reason or "",
        }
    try:
        raw = repository.mutate(function_name, values)
        rows = _as_rows(raw, payload.operation)
        if len(rows) != 1:
            raise AppointmentDataIntegrityError("Appointment mutation result is invalid")
        result = AppointmentMutationResult.model_validate(rows[0])
    except (AppointmentDataIntegrityError, ValidationError) as exc:
        raise AppointmentDataIntegrityError("Appointment mutation result is invalid") from exc
    except Exception as exc:
        logger.error(
            "appointment_mutation_failed",
            extra={"operation": payload.operation, "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Appointment mutation failed") from exc
    if result.outcome in {"slot_unavailable", "appointment_unavailable"}:
        logger.warning("appointment_confirmation_conflict", extra={"operation": payload.operation})
        raise AppointmentConflictError("The appointment or time slot is no longer available")
    logger.info(
        "appointment_mutation_completed",
        extra={"operation": payload.operation, "outcome": result.outcome},
    )
    return result
