"""Deterministic patient-profile operations shared by REST and LangGraph.

The service layer owns database query construction and response validation.
Neither HTTP routes nor language-model tools should build patient queries
directly.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from supabase import Client

from app.schemas.patient import (
    READABLE_FIELD_ORDER,
    READABLE_FIELDS,
    PatientAttributesOut,
    PatientOut,
    UnsupportedPatientAttributeError,
    validate_attribute_value,
)

logger = logging.getLogger(__name__)


class InvalidPatientFieldsError(ValueError):
    """Raised when a field filter contains empty or non-readable names."""


class PatientNotFoundError(LookupError):
    """Raised when no RLS-visible profile exists for the authenticated user."""


class PatientServiceUnavailableError(RuntimeError):
    """Raised when Supabase cannot complete the patient query."""


class PatientDataIntegrityError(RuntimeError):
    """Raised when stored patient data violates the API response contract."""


class PatientAttributeValidationError(ValueError):
    """Safe wrapper for a field-specific Pydantic validation failure."""

    def __init__(self, key: str, error_count: int) -> None:
        super().__init__(f"Invalid value for patient attribute: {key}")
        self.key = key
        self.error_count = error_count


def parse_requested_fields(fields: str | None) -> tuple[str, ...]:
    """Parse, validate and de-duplicate a comma-separated field selection.

    ``None`` means the complete profile. Field order follows the client's first
    occurrence, while duplicates are removed. Empty tokens are rejected rather
    than silently changing the meaning of a malformed request.
    """

    if fields is None:
        return READABLE_FIELD_ORDER

    raw_names = fields.split(",")
    normalized_names = [name.strip() for name in raw_names]
    if not normalized_names or any(not name for name in normalized_names):
        raise InvalidPatientFieldsError("Field selection contains an empty name")

    if any(name not in READABLE_FIELDS for name in normalized_names):
        raise InvalidPatientFieldsError("Field selection contains an unknown name")

    # dict preserves insertion order and provides simple stable de-duplication.
    return tuple(dict.fromkeys(normalized_names))


def get_patient(
    client: Client,
    patient_id: UUID,
    requested_fields: Sequence[str] = READABLE_FIELD_ORDER,
) -> PatientAttributesOut:
    """Return selected attributes for the authenticated patient.

    The ID comes only from ``CurrentUser`` after JWT verification. The explicit
    ``id`` filter is defense in depth and the patient-scoped client's JWT keeps
    PostgreSQL RLS active as a second authorization boundary.
    """

    selected_fields = tuple(requested_fields)
    if not selected_fields or any(field not in READABLE_FIELDS for field in selected_fields):
        # Service callers, including future agent tools, receive the same field
        # protection as HTTP callers even if they bypass the query parser.
        raise InvalidPatientFieldsError("Service received an invalid field selection")

    try:
        response = (
            client.table("patients")
            .select(",".join(selected_fields))
            .eq("id", str(patient_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        # Supabase/PostgREST messages can contain URLs, SQL details or response
        # bodies. Record only the type and safe operation name.
        logger.error(
            "patient_profile_query_failed",
            extra={"operation": "get_patient", "error_type": type(exc).__name__},
        )
        raise PatientServiceUnavailableError("Patient profile query failed") from exc

    rows = response.data
    if not isinstance(rows, list) or not rows:
        logger.info("patient_profile_not_found", extra={"operation": "get_patient"})
        raise PatientNotFoundError("Patient profile was not found")

    row = rows[0]
    try:
        if set(selected_fields) == READABLE_FIELDS:
            # Full reads use the strict response model first so missing required
            # database columns are treated as integrity errors, not null fields.
            full_patient = PatientOut.model_validate(row)
            result = PatientAttributesOut.model_validate(full_patient.model_dump())
        else:
            result = PatientAttributesOut.model_validate(row)
    except ValidationError as exc:
        logger.error(
            "patient_profile_validation_failed",
            extra={
                "operation": "get_patient",
                "validation_error_count": exc.error_count(),
            },
        )
        raise PatientDataIntegrityError("Stored patient profile is invalid") from exc

    logger.info(
        "patient_profile_retrieved",
        extra={
            "operation": "get_patient",
            "selected_field_count": len(selected_fields),
            "full_profile": set(selected_fields) == READABLE_FIELDS,
        },
    )
    return result


def update_patient_attribute(
    client: Client,
    patient_id: UUID,
    key: str,
    value: object,
) -> PatientOut:
    """Validate and update exactly one attribute for the authenticated patient.

    ``key`` becomes a database column only after the hard-coded validator map
    accepts it. The patient UUID comes from the verified JWT, and the
    patient-scoped client keeps RLS active during the update.
    """

    try:
        normalized_value = validate_attribute_value(key, value)
    except UnsupportedPatientAttributeError:
        # The schema validator already emitted a safe field-only log.
        raise
    except ValidationError as exc:
        # Do not retain or re-emit Pydantic's error payload because it can
        # include the rejected patient-supplied value.
        raise PatientAttributeValidationError(key, exc.error_count()) from exc

    try:
        response = (
            client.table("patients")
            .update({key: normalized_value})
            .eq("id", str(patient_id))
            .execute()
        )
    except Exception as exc:
        logger.error(
            "patient_profile_update_failed",
            extra={
                "operation": "update_patient_attribute",
                "attribute_key": key,
                "error_type": type(exc).__name__,
            },
        )
        raise PatientServiceUnavailableError("Patient profile update failed") from exc

    rows = response.data
    if not isinstance(rows, list) or not rows:
        logger.info(
            "patient_profile_not_found",
            extra={
                "operation": "update_patient_attribute",
                "attribute_key": key,
            },
        )
        raise PatientNotFoundError("Patient profile was not found")

    try:
        updated_patient = PatientOut.model_validate(rows[0])
    except ValidationError as exc:
        logger.error(
            "patient_profile_validation_failed",
            extra={
                "operation": "update_patient_attribute",
                "attribute_key": key,
                "validation_error_count": exc.error_count(),
            },
        )
        raise PatientDataIntegrityError("Updated patient profile is invalid") from exc

    logger.info(
        "patient_profile_attribute_updated",
        extra={
            "operation": "update_patient_attribute",
            "attribute_key": key,
        },
    )
    return updated_patient
