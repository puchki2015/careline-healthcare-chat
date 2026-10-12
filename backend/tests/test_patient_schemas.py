"""Tests for patient response models and per-key validation rules."""

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.patient import (
    EDITABLE_KEYS,
    READABLE_FIELDS,
    AttributeUpdate,
    PatientOut,
    UnsupportedPatientAttributeError,
    validate_attribute_value,
)


def test_field_whitelists_protect_system_columns() -> None:
    assert EDITABLE_KEYS == {
        "full_name",
        "phone",
        "date_of_birth",
        "gender",
        "address",
        "emergency_contact",
    }
    assert {"id", "email", "created_at", "updated_at"}.isdisjoint(EDITABLE_KEYS)
    assert EDITABLE_KEYS < READABLE_FIELDS


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("  Alice Patient  ", "Alice Patient"),
        ("A", "A"),
    ],
)
def test_full_name_is_trimmed(supplied: str, expected: str) -> None:
    assert validate_attribute_value("full_name", supplied) == expected


@pytest.mark.parametrize("supplied", [None, "", "   ", "a" * 101])
def test_full_name_rejects_missing_or_invalid_values(supplied: object) -> None:
    with pytest.raises(ValidationError):
        validate_attribute_value("full_name", supplied)


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("+16045550123", "+16045550123"),
        ("  +442071838750  ", "+442071838750"),
        (None, None),
    ],
)
def test_phone_accepts_e164_or_null(supplied: object, expected: str | None) -> None:
    assert validate_attribute_value("phone", supplied) == expected


@pytest.mark.parametrize(
    "supplied",
    ["6045550123", "+0123456789", "+123", "+1234567890123456", "phone"],
)
def test_phone_rejects_invalid_formats(supplied: str) -> None:
    with pytest.raises(ValidationError):
        validate_attribute_value("phone", supplied)


def test_date_of_birth_is_normalized_to_iso_date() -> None:
    assert validate_attribute_value("date_of_birth", "1990-04-12") == "1990-04-12"
    assert validate_attribute_value("date_of_birth", "1900-01-01") == "1900-01-01"
    assert (
        validate_attribute_value("date_of_birth", date.today().isoformat())
        == date.today().isoformat()
    )
    assert validate_attribute_value("date_of_birth", None) is None


@pytest.mark.parametrize(
    "supplied",
    [
        "1899-12-31",
        (date.today() + timedelta(days=1)).isoformat(),
        "not-a-date",
    ],
)
def test_date_of_birth_rejects_invalid_range_or_format(supplied: str) -> None:
    with pytest.raises(ValidationError):
        validate_attribute_value("date_of_birth", supplied)


@pytest.mark.parametrize(
    "gender",
    ["female", "male", "non_binary", "other", "prefer_not_to_say", None],
)
def test_gender_accepts_database_values_or_null(gender: str | None) -> None:
    assert validate_attribute_value("gender", gender) == gender


def test_gender_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        validate_attribute_value("gender", "unknown")


def test_address_is_trimmed_and_blank_fields_become_null() -> None:
    result = validate_attribute_value(
        "address",
        {
            "line1": "  123 Main Street  ",
            "line2": "   ",
            "city": " Vancouver ",
            "region": "BC",
            "postal_code": "V1V 1V1",
            "country": "Canada",
        },
    )

    assert result == {
        "line1": "123 Main Street",
        "line2": None,
        "city": "Vancouver",
        "region": "BC",
        "postal_code": "V1V 1V1",
        "country": "Canada",
    }


def test_address_allows_null_but_rejects_unknown_fields() -> None:
    assert validate_attribute_value("address", None) is None
    with pytest.raises(ValidationError):
        validate_attribute_value("address", {"city": "Vancouver", "secret": "value"})
    with pytest.raises(ValidationError):
        validate_attribute_value("address", {"line1": "a" * 201})


def test_emergency_contact_requires_complete_valid_structure() -> None:
    result = validate_attribute_value(
        "emergency_contact",
        {
            "name": "  Bob Patient ",
            "phone": "+16045550124",
            "relationship": " Parent ",
        },
    )

    assert result == {
        "name": "Bob Patient",
        "phone": "+16045550124",
        "relationship": "Parent",
    }
    assert validate_attribute_value("emergency_contact", None) is None


@pytest.mark.parametrize(
    "supplied",
    [
        {"name": "Bob", "phone": "+16045550124"},
        {"name": "Bob", "phone": "invalid", "relationship": "Parent"},
        {"name": "a" * 101, "phone": "+16045550124", "relationship": "Parent"},
        {"name": "Bob", "phone": "+16045550124", "relationship": "   "},
        {
            "name": "Bob",
            "phone": "+16045550124",
            "relationship": "Parent",
            "unknown": "value",
        },
    ],
)
def test_emergency_contact_rejects_incomplete_or_unknown_data(
    supplied: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        validate_attribute_value("emergency_contact", supplied)


@pytest.mark.parametrize("key", ["id", "email", "created_at", "updated_at", "password"])
def test_non_editable_attribute_is_rejected(key: str) -> None:
    with pytest.raises(UnsupportedPatientAttributeError, match=key):
        validate_attribute_value(key, "attempted-value")


def test_attribute_update_requires_only_value() -> None:
    assert AttributeUpdate(value="Alice").value == "Alice"

    with pytest.raises(ValidationError):
        AttributeUpdate.model_validate({})
    with pytest.raises(ValidationError):
        AttributeUpdate.model_validate({"value": "Alice", "email": "not-allowed"})


def test_patient_output_accepts_new_profile_json_defaults() -> None:
    now = datetime.now(UTC)
    patient = PatientOut(
        id=uuid4(),
        email="patient@example.com",
        address={},
        emergency_contact={},
        created_at=now,
        updated_at=now,
    )

    assert patient.address.model_dump() == {
        "line1": None,
        "line2": None,
        "city": None,
        "region": None,
        "postal_code": None,
        "country": None,
    }
    assert patient.emergency_contact.model_dump() == {
        "name": None,
        "phone": None,
        "relationship": None,
    }


def test_validation_logs_never_include_patient_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_value = "private-health-information"
    caplog.set_level(logging.DEBUG)

    with pytest.raises(ValidationError):
        validate_attribute_value("phone", sensitive_value)

    assert sensitive_value not in caplog.text
    assert any(getattr(record, "attribute_key", None) == "phone" for record in caplog.records)
