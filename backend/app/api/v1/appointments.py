"""Read-only Swagger endpoints for patient appointments and availability."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.errors import AUTHENTICATION_ERROR_RESPONSE
from app.deps import AppointmentRepositoryDependency, CurrentUserDependency
from app.schemas.appointment import AppointmentListResponse, AvailabilityResponse
from app.services.appointment_service import list_appointments, search_availability

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.get(
    "",
    response_model=AppointmentListResponse,
    responses={
        **AUTHENTICATION_ERROR_RESPONSE,
        500: {"description": "Stored appointment data is invalid."},
        503: {"description": "The appointment service is temporarily unavailable."},
    },
    summary="List my appointments",
)
def get_my_appointments(
    current_user: CurrentUserDependency,
    repository: AppointmentRepositoryDependency,
    include_past: Annotated[
        bool, Query(description="Include cancelled and historical records.")
    ] = False,
) -> AppointmentListResponse:
    """Return only appointments owned by the authenticated patient."""

    return AppointmentListResponse(
        items=list_appointments(repository, current_user.id, include_past=include_past)
    )


@router.get(
    "/availability",
    response_model=AvailabilityResponse,
    responses={
        **AUTHENTICATION_ERROR_RESPONSE,
        400: {"description": "The date range is invalid."},
        500: {"description": "Stored availability data is invalid."},
        503: {"description": "The appointment service is temporarily unavailable."},
    },
    summary="Search available appointment times",
)
def get_appointment_availability(
    current_user: CurrentUserDependency,
    repository: AppointmentRepositoryDependency,
    starts_at: Annotated[datetime, Query(description="Timezone-aware ISO-8601 start.")],
    ends_at: Annotated[datetime, Query(description="Timezone-aware ISO-8601 end.")],
    specialty: Annotated[str | None, Query(min_length=2, max_length=120)] = None,
    doctor_name: Annotated[str | None, Query(min_length=2, max_length=120)] = None,
) -> AvailabilityResponse:
    """Search authoritative future availability; authentication is required."""

    # current_user is intentionally present even though availability is not
    # patient-specific. It prevents anonymous schedule enumeration.
    del current_user
    return AvailabilityResponse(
        items=search_availability(
            repository,
            starts_at=starts_at,
            ends_at=ends_at,
            specialty=specialty,
            doctor_name=doctor_name,
        )
    )
