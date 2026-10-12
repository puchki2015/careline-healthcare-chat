"""Administrator APIs for provider schedules and appointment oversight."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi import status as http_status

from app.deps import AppointmentRepositoryDependency, CurrentAdminDependency
from app.schemas.appointment_admin import (
    AdminAppointmentListResponse,
    AvailabilityAdminListResponse,
    AvailabilityAdminOut,
    AvailabilityCreate,
    AvailabilityStatusUpdate,
    DoctorAdminOut,
    DoctorCreate,
    DoctorListResponse,
    DoctorUpdate,
)
from app.services.appointment_admin_service import (
    create_availability,
    create_doctor,
    list_admin_appointments,
    list_availability,
    list_doctors,
    set_availability_status,
    update_doctor,
)

router = APIRouter(prefix="/admin/appointments", tags=["Admin appointments"])


@router.get("/doctors", response_model=DoctorListResponse)
def get_doctors(
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
    include_inactive: bool = True,
) -> DoctorListResponse:
    return DoctorListResponse(
        items=list_doctors(repository, current_admin, include_inactive=include_inactive)
    )


@router.post(
    "/doctors",
    response_model=DoctorAdminOut,
    status_code=http_status.HTTP_201_CREATED,
)
def post_doctor(
    body: DoctorCreate,
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
) -> DoctorAdminOut:
    return create_doctor(repository, current_admin, body)


@router.patch("/doctors/{doctor_id}", response_model=DoctorAdminOut)
def patch_doctor(
    doctor_id: UUID,
    body: DoctorUpdate,
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
) -> DoctorAdminOut:
    return update_doctor(repository, current_admin, doctor_id, body)


@router.get("/availability", response_model=AvailabilityAdminListResponse)
def get_admin_availability(
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
    doctor_id: UUID | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> AvailabilityAdminListResponse:
    return AvailabilityAdminListResponse(
        items=list_availability(
            repository,
            current_admin,
            doctor_id=doctor_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
    )


@router.post(
    "/availability",
    response_model=AvailabilityAdminOut,
    status_code=http_status.HTTP_201_CREATED,
)
def post_admin_availability(
    body: AvailabilityCreate,
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
) -> AvailabilityAdminOut:
    return create_availability(repository, current_admin, body)


@router.patch("/availability/{slot_id}/status", response_model=AvailabilityAdminOut)
def patch_availability_status(
    slot_id: UUID,
    body: AvailabilityStatusUpdate,
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
) -> AvailabilityAdminOut:
    return set_availability_status(repository, current_admin, slot_id, body.status)


@router.get("", response_model=AdminAppointmentListResponse)
def get_admin_appointments(
    current_admin: CurrentAdminDependency,
    repository: AppointmentRepositoryDependency,
    status: Annotated[
        Literal["scheduled", "cancelled", "completed", "no_show"] | None,
        Query(),
    ] = None,
    doctor_id: UUID | None = None,
    patient_id: UUID | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> AdminAppointmentListResponse:
    return AdminAppointmentListResponse(
        items=list_admin_appointments(
            repository,
            current_admin,
            status=status,
            doctor_id=doctor_id,
            patient_id=patient_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
    )
