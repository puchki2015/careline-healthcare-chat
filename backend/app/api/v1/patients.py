"""Authenticated patient-profile HTTP endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.errors import PATIENT_GET_ERROR_RESPONSES, PATIENT_UPDATE_ERROR_RESPONSES
from app.deps import CurrentUserDependency, UserSupabaseClientDependency
from app.schemas.patient import AttributeUpdate, PatientAttributesOut, PatientOut
from app.services.patient_service import (
    get_patient,
    parse_requested_fields,
    update_patient_attribute,
)

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.get(
    "/me",
    response_model=PatientAttributesOut,
    response_model_exclude_unset=True,
    responses=PATIENT_GET_ERROR_RESPONSES,
    summary="Get the authenticated patient's profile",
    description=(
        "Returns the current patient's complete profile or a validated comma-separated field "
        "selection. The patient identity always comes from the verified access token."
    ),
)
def get_my_profile(
    current_user: CurrentUserDependency,
    patient_client: UserSupabaseClientDependency,
    fields: Annotated[
        str | None,
        Query(
            max_length=500,
            description="Optional comma-separated fields, for example: email,phone",
        ),
    ] = None,
) -> PatientAttributesOut:
    """Return only the authenticated patient's RLS-visible profile."""

    requested_fields = parse_requested_fields(fields)
    return get_patient(patient_client, current_user.id, requested_fields)


@router.patch(
    "/me/attributes/{key}",
    response_model=PatientOut,
    responses=PATIENT_UPDATE_ERROR_RESPONSES,
    summary="Update one patient profile attribute",
    description=(
        "Validates and updates exactly one editable attribute for the authenticated patient. "
        "The patient identity always comes from the verified access token."
    ),
)
def update_my_attribute(
    key: str,
    update: AttributeUpdate,
    current_user: CurrentUserDependency,
    patient_client: UserSupabaseClientDependency,
) -> PatientOut:
    """Update one whitelisted field and return the complete updated profile."""

    return update_patient_attribute(patient_client, current_user.id, key, update.value)
