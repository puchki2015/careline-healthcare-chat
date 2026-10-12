"""Patient response models and per-attribute update validation.

This module defines the public patient data contract in one place. Routes and
services must use ``validate_attribute_value`` instead of accepting arbitrary
column names or values from a request.
"""

import logging
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
)

logger = logging.getLogger(__name__)

PHONE_PATTERN = r"^\+[1-9][0-9]{7,14}$"
EARLIEST_DATE_OF_BIRTH = date(1900, 1, 1)

# These constants are security boundaries, not just UI hints. The service
# layer will reject any requested field or update key not present here.
READABLE_FIELDS = frozenset(
    {
        "id",
        "email",
        "full_name",
        "phone",
        "date_of_birth",
        "gender",
        "address",
        "emergency_contact",
        "created_at",
        "updated_at",
    }
)
# SQL selection uses a stable order for predictable tests, logs and responses.
READABLE_FIELD_ORDER = (
    "id",
    "email",
    "full_name",
    "phone",
    "date_of_birth",
    "gender",
    "address",
    "emergency_contact",
    "created_at",
    "updated_at",
)
EDITABLE_KEYS = frozenset(
    {
        "full_name",
        "phone",
        "date_of_birth",
        "gender",
        "address",
        "emergency_contact",
    }
)
EDITABLE_KEY_ORDER = (
    "full_name",
    "phone",
    "date_of_birth",
    "gender",
    "address",
    "emergency_contact",
)

TrimmedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TrimmedShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PHONE_PATTERN)]


class Gender(StrEnum):
    """Gender values allowed by both the API and database constraint."""

    FEMALE = "female"
    MALE = "male"
    NON_BINARY = "non_binary"
    OTHER = "other"
    PREFER_NOT_TO_SAY = "prefer_not_to_say"


class Address(BaseModel):
    """Structured postal address stored in the patient's JSONB column."""

    model_config = ConfigDict(extra="forbid")

    line1: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    line2: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    region: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    postal_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = (
        None
    )
    country: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None

    @field_validator("line1", "line2", "city", "region", "postal_code", "country", mode="before")
    @classmethod
    def convert_blank_strings_to_none(cls, value: object) -> object:
        """Treat blank optional address fields as absent after trimming."""

        if isinstance(value, str) and not value.strip():
            return None
        return value


class EmergencyContactOut(BaseModel):
    """Emergency-contact shape returned from existing database records.

    Fields are optional because newly created patient rows store an empty JSON
    object until the patient supplies contact information.
    """

    model_config = ConfigDict(extra="forbid")

    name: TrimmedName | None = None
    phone: PhoneNumber | None = None
    relationship: TrimmedShortText | None = None


class EmergencyContactUpdate(BaseModel):
    """Complete emergency contact required when replacing this attribute."""

    model_config = ConfigDict(extra="forbid")

    name: TrimmedName
    phone: PhoneNumber
    relationship: TrimmedShortText


class PatientOut(BaseModel):
    """Complete patient profile returned by the API."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    email: str
    full_name: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    gender: Gender | None = None
    address: Address = Field(default_factory=Address)
    emergency_contact: EmergencyContactOut = Field(default_factory=EmergencyContactOut)
    created_at: datetime
    updated_at: datetime


class PatientAttributesOut(BaseModel):
    """Full or field-filtered patient response.

    Every field has a default so a filtered database selection can be validated.
    Routes serialize this model with ``exclude_unset=True``: a requested field
    whose value is null remains present, while fields not requested are omitted.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    email: str | None = None
    full_name: str | None = None
    phone: str | None = None
    date_of_birth: date | None = None
    gender: Gender | None = None
    address: Address | None = None
    emergency_contact: EmergencyContactOut | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AttributeUpdate(BaseModel):
    """Generic request body for updating exactly one path-selected attribute.

    The route path selects the field, for example ``/attributes/phone``. The
    body therefore contains only ``value``. Unknown body properties are rejected
    to catch client mistakes and prevent mass assignment.
    """

    model_config = ConfigDict(extra="forbid")

    value: Any


class UnsupportedPatientAttributeError(ValueError):
    """Raised when a requested update key is not explicitly editable."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Patient attribute is not editable: {key}")
        self.key = key


def _validate_date_of_birth(value: date | None) -> date | None:
    """Reject dates that are implausibly old or later than today."""

    if value is None:
        return None
    if value < EARLIEST_DATE_OF_BIRTH:
        raise ValueError("Date of birth cannot be earlier than 1900-01-01")
    if value > date.today():
        raise ValueError("Date of birth cannot be in the future")
    return value


DateOfBirthValue = Annotated[date | None, AfterValidator(_validate_date_of_birth)]

# TypeAdapter applies the correct Pydantic rules after the route identifies the
# update key. Adding an editable field requires adding an adapter here, which
# prevents an unvalidated column from entering the update path accidentally.
ATTRIBUTE_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "full_name": TypeAdapter(TrimmedName),
    "phone": TypeAdapter(PhoneNumber | None),
    "date_of_birth": TypeAdapter(DateOfBirthValue),
    "gender": TypeAdapter(Gender | None),
    "address": TypeAdapter(Address | None),
    "emergency_contact": TypeAdapter(EmergencyContactUpdate | None),
}


def validate_attribute_value(key: str, value: Any) -> Any:
    """Validate and normalize one patient attribute into JSON-ready data.

    Validation logs contain only the attribute key and error count. The supplied
    value and Pydantic error representation are deliberately excluded because
    they may contain protected health or contact information.
    """

    adapter = ATTRIBUTE_ADAPTERS.get(key)
    if adapter is None:
        logger.warning(
            "patient_attribute_rejected",
            extra={"attribute_key": key, "validation_reason": "not_editable"},
        )
        raise UnsupportedPatientAttributeError(key)

    try:
        validated_value = adapter.validate_python(value)
    except ValidationError as exc:
        logger.warning(
            "patient_attribute_validation_failed",
            extra={
                "attribute_key": key,
                "validation_error_count": exc.error_count(),
            },
        )
        raise

    # JSON mode converts dates and enums to database-ready strings and nested
    # models to dictionaries while preserving explicit null values.
    normalized_value = adapter.dump_python(validated_value, mode="json")
    logger.debug(
        "patient_attribute_validation_succeeded",
        extra={"attribute_key": key},
    )
    return normalized_value
