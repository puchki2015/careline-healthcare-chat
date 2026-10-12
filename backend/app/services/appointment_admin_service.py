"""Authorized administrator operations for doctors, schedules, and appointments."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from app.repositories.appointment_repository import AppointmentRepository
from app.schemas.admin import CurrentAdmin
from app.schemas.appointment_admin import (
    AdminAppointmentOut,
    AvailabilityAdminOut,
    AvailabilityCreate,
    DoctorAdminOut,
    DoctorCreate,
    DoctorUpdate,
)
from app.services.appointment_service import (
    AppointmentConflictError,
    AppointmentDataIntegrityError,
    AppointmentServiceUnavailableError,
    AppointmentValidationError,
)

logger = logging.getLogger(__name__)


def _rows(raw: object, operation: str) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        logger.error("admin_schedule_result_invalid", extra={"operation": operation})
        raise AppointmentDataIntegrityError("Schedule result is invalid")
    return raw


def list_doctors(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    *,
    include_inactive: bool,
) -> list[DoctorAdminOut]:
    del admin
    try:
        result = [
            DoctorAdminOut.model_validate(row)
            for row in _rows(
                repository.select_doctors(include_inactive=include_inactive), "list_doctors"
            )
        ]
    except ValidationError as exc:
        raise AppointmentDataIntegrityError("Stored doctor data is invalid") from exc
    except AppointmentDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "admin_schedule_query_failed",
            extra={"operation": "list_doctors", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Doctor query failed") from exc
    logger.info("admin_doctors_retrieved", extra={"doctor_count": len(result)})
    return result


def create_doctor(
    repository: AppointmentRepository, admin: CurrentAdmin, body: DoctorCreate
) -> DoctorAdminOut:
    try:
        rows = _rows(repository.insert_doctor(body.model_dump(mode="json")), "create_doctor")
        if len(rows) != 1:
            raise AppointmentDataIntegrityError("Doctor creation result is invalid")
        result = DoctorAdminOut.model_validate(rows[0])
    except (ValidationError, AppointmentDataIntegrityError) as exc:
        raise AppointmentDataIntegrityError("Doctor creation result is invalid") from exc
    except Exception as exc:
        logger.error(
            "admin_schedule_write_failed",
            extra={"operation": "create_doctor", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Doctor creation failed") from exc
    logger.info(
        "admin_doctor_created", extra={"admin_id": str(admin.id), "doctor_id": str(result.id)}
    )
    return result


def update_doctor(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    doctor_id: UUID,
    body: DoctorUpdate,
) -> DoctorAdminOut:
    values = body.model_dump(mode="json", exclude_unset=True)
    values["updated_at"] = datetime.now(UTC).isoformat()
    try:
        rows = _rows(repository.update_doctor(str(doctor_id), values), "update_doctor")
        if len(rows) != 1:
            raise AppointmentValidationError("Doctor was not found")
        result = DoctorAdminOut.model_validate(rows[0])
    except (AppointmentValidationError, AppointmentDataIntegrityError):
        raise
    except ValidationError as exc:
        raise AppointmentDataIntegrityError("Updated doctor data is invalid") from exc
    except Exception as exc:
        logger.error(
            "admin_schedule_write_failed",
            extra={"operation": "update_doctor", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Doctor update failed") from exc
    logger.info(
        "admin_doctor_updated", extra={"admin_id": str(admin.id), "doctor_id": str(doctor_id)}
    )
    return result


def list_availability(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    *,
    doctor_id: UUID | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> list[AvailabilityAdminOut]:
    del admin
    try:
        rows = _rows(
            repository.select_admin_availability(
                doctor_id=str(doctor_id) if doctor_id else None,
                starts_at=starts_at.isoformat() if starts_at else None,
                ends_at=ends_at.isoformat() if ends_at else None,
            ),
            "list_availability",
        )
        result = []
        for row in rows:
            row["doctor"] = row.pop("doctor_profiles", None)
            result.append(AvailabilityAdminOut.model_validate(row))
    except ValidationError as exc:
        raise AppointmentDataIntegrityError("Stored availability is invalid") from exc
    except AppointmentDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "admin_schedule_query_failed",
            extra={"operation": "list_availability", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Availability query failed") from exc
    return result


def create_availability(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    body: AvailabilityCreate,
) -> AvailabilityAdminOut:
    if body.starts_at <= datetime.now(UTC):
        raise AppointmentValidationError("Availability must start in the future")
    values = body.model_dump(mode="json")
    values["status"] = "available"
    try:
        rows = _rows(repository.insert_availability(values), "create_availability")
        if len(rows) != 1:
            raise AppointmentDataIntegrityError("Availability creation result is invalid")
        # Insert responses do not contain the embedded doctor. Resolve the
        # canonical row through the admin list query before returning it.
        created_id = UUID(str(rows[0].get("id")))
        candidates = list_availability(
            repository,
            admin,
            doctor_id=body.doctor_id,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
        )
        result = next((item for item in candidates if item.id == created_id), None)
        if result is None:
            raise AppointmentDataIntegrityError("Created availability could not be read")
    except (AppointmentValidationError, AppointmentDataIntegrityError):
        raise
    except Exception as exc:
        logger.error(
            "admin_schedule_write_failed",
            extra={"operation": "create_availability", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Availability creation failed") from exc
    logger.info(
        "admin_availability_created", extra={"admin_id": str(admin.id), "slot_id": str(result.id)}
    )
    return result


def set_availability_status(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    slot_id: UUID,
    status: str,
) -> AvailabilityAdminOut:
    try:
        rows = _rows(
            repository.update_availability_status(str(slot_id), status),
            "update_availability",
        )
        if len(rows) != 1:
            raise AppointmentConflictError("Booked or unknown slots cannot be changed")
        doctor_id = UUID(str(rows[0].get("doctor_id")))
        candidates = list_availability(
            repository, admin, doctor_id=doctor_id, starts_at=None, ends_at=None
        )
        result = next((item for item in candidates if item.id == slot_id), None)
        if result is None:
            raise AppointmentDataIntegrityError("Updated availability could not be read")
    except (AppointmentConflictError, AppointmentDataIntegrityError):
        raise
    except Exception as exc:
        logger.error(
            "admin_schedule_write_failed",
            extra={"operation": "update_availability", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Availability update failed") from exc
    logger.info(
        "admin_availability_updated",
        extra={"admin_id": str(admin.id), "slot_id": str(slot_id), "status": status},
    )
    return result


def list_admin_appointments(
    repository: AppointmentRepository,
    admin: CurrentAdmin,
    *,
    status: str | None,
    doctor_id: UUID | None,
    patient_id: UUID | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> list[AdminAppointmentOut]:
    del admin
    try:
        rows = _rows(
            repository.select_admin_appointments(
                status=status,
                doctor_id=str(doctor_id) if doctor_id else None,
                patient_id=str(patient_id) if patient_id else None,
                starts_at=starts_at.isoformat() if starts_at else None,
                ends_at=ends_at.isoformat() if ends_at else None,
            ),
            "list_appointments",
        )
        result = []
        for row in rows:
            patient = row.pop("patients", None)
            availability = row.pop("provider_availability", None)
            row["doctor"] = row.pop("doctor_profiles", None)
            row["patient_name"] = patient.get("full_name") if isinstance(patient, dict) else None
            row["patient_email"] = patient.get("email") if isinstance(patient, dict) else None
            row["location_type"] = (
                availability.get("location_type") if isinstance(availability, dict) else None
            )
            row["location"] = (
                availability.get("location") if isinstance(availability, dict) else None
            )
            result.append(AdminAppointmentOut.model_validate(row))
    except ValidationError as exc:
        raise AppointmentDataIntegrityError("Stored appointment data is invalid") from exc
    except AppointmentDataIntegrityError:
        raise
    except Exception as exc:
        logger.error(
            "admin_schedule_query_failed",
            extra={"operation": "list_appointments", "error_type": type(exc).__name__},
        )
        raise AppointmentServiceUnavailableError("Appointment query failed") from exc
    logger.info("admin_appointments_retrieved", extra={"appointment_count": len(result)})
    return result
