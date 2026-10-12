"""Unit tests for deterministic patient-profile read operations."""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.schemas.patient import READABLE_FIELD_ORDER, UnsupportedPatientAttributeError
from app.services.patient_service import (
    InvalidPatientFieldsError,
    PatientAttributeValidationError,
    PatientDataIntegrityError,
    PatientNotFoundError,
    PatientServiceUnavailableError,
    get_patient,
    parse_requested_fields,
    update_patient_attribute,
)


class FakePatientQuery:
    """Record the query chain and return a configured result without network I/O."""

    def __init__(self, rows: object = None, failure: Exception | None = None) -> None:
        self.rows = [] if rows is None else rows
        self.failure = failure
        self.selected: str | None = None
        self.patient_filter: tuple[str, str] | None = None
        self.limit_value: int | None = None
        self.updated: dict[str, object] | None = None

    def select(self, fields: str):
        self.selected = fields
        return self

    def eq(self, column: str, value: str):
        self.patient_filter = (column, value)
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def update(self, values: dict[str, object]):
        self.updated = values
        # Simulate PostgREST's default representation of the updated row.
        if isinstance(self.rows, list) and self.rows and isinstance(self.rows[0], dict):
            self.rows[0].update(values)
        return self

    def execute(self):
        if self.failure:
            raise self.failure
        return SimpleNamespace(data=self.rows)


class FakePatientClient:
    def __init__(self, query: FakePatientQuery) -> None:
        self.query = query
        self.requested_table: str | None = None

    def table(self, name: str) -> FakePatientQuery:
        self.requested_table = name
        return self.query


def complete_patient_row(patient_id: UUID | None = None) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(patient_id or uuid4()),
        "email": "patient@example.com",
        "full_name": "Alice Patient",
        "phone": "+16045550123",
        "date_of_birth": "1990-04-12",
        "gender": "female",
        "address": {},
        "emergency_contact": {},
        "created_at": now,
        "updated_at": now,
    }


def test_parse_fields_defaults_to_complete_profile() -> None:
    assert parse_requested_fields(None) == READABLE_FIELD_ORDER


def test_parse_fields_trims_and_deduplicates_in_order() -> None:
    assert parse_requested_fields(" email, phone,email ") == ("email", "phone")


@pytest.mark.parametrize("fields", ["password", "email,,phone", "", "email,unknown"])
def test_parse_fields_rejects_unknown_or_empty_names(fields: str) -> None:
    with pytest.raises(InvalidPatientFieldsError):
        parse_requested_fields(fields)


def test_get_patient_builds_scoped_query_and_validates_full_response() -> None:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[complete_patient_row(patient_id)])
    client = FakePatientClient(query)

    result = get_patient(client, patient_id)

    assert client.requested_table == "patients"
    assert query.selected == ",".join(READABLE_FIELD_ORDER)
    assert query.patient_filter == ("id", str(patient_id))
    assert query.limit_value == 1
    assert result.id == patient_id
    assert result.email == "patient@example.com"


def test_get_patient_returns_only_selected_nullable_fields() -> None:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[{"email": "patient@example.com", "phone": None}])
    client = FakePatientClient(query)

    result = get_patient(client, patient_id, ("email", "phone"))

    assert result.model_dump(mode="json", exclude_unset=True) == {
        "email": "patient@example.com",
        "phone": None,
    }
    assert query.selected == "email,phone"


def test_get_patient_rejects_invalid_service_field_selection() -> None:
    with pytest.raises(InvalidPatientFieldsError):
        get_patient(FakePatientClient(FakePatientQuery()), uuid4(), ("password",))


def test_get_patient_returns_not_found_for_empty_rls_result() -> None:
    with pytest.raises(PatientNotFoundError):
        get_patient(FakePatientClient(FakePatientQuery(rows=[])), uuid4())


def test_get_patient_wraps_external_failure() -> None:
    query = FakePatientQuery(failure=RuntimeError("database details must stay private"))

    with pytest.raises(PatientServiceUnavailableError):
        get_patient(FakePatientClient(query), uuid4())


def test_get_patient_rejects_invalid_stored_full_profile() -> None:
    invalid_row = complete_patient_row()
    invalid_row.pop("created_at")

    with pytest.raises(PatientDataIntegrityError):
        get_patient(FakePatientClient(FakePatientQuery(rows=[invalid_row])), uuid4())


def test_get_patient_logs_no_profile_values_or_patient_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    patient_id = uuid4()
    sensitive_name = "Sensitive Patient Name"
    row = complete_patient_row(patient_id)
    row["full_name"] = sensitive_name
    caplog.set_level(logging.INFO)

    get_patient(FakePatientClient(FakePatientQuery(rows=[row])), patient_id)

    assert sensitive_name not in caplog.text
    assert str(patient_id) not in caplog.text
    assert any(record.message == "patient_profile_retrieved" for record in caplog.records)


def test_update_patient_attribute_normalizes_value_and_scopes_query() -> None:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[complete_patient_row(patient_id)])
    client = FakePatientClient(query)

    result = update_patient_attribute(client, patient_id, "phone", "  +16045550999  ")

    assert client.requested_table == "patients"
    assert query.updated == {"phone": "+16045550999"}
    assert query.patient_filter == ("id", str(patient_id))
    assert result.phone == "+16045550999"


def test_update_patient_attribute_supports_null_for_optional_field() -> None:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[complete_patient_row(patient_id)])

    result = update_patient_attribute(
        FakePatientClient(query),
        patient_id,
        "phone",
        None,
    )

    assert query.updated == {"phone": None}
    assert result.phone is None


def test_update_patient_attribute_rejects_protected_key_before_query() -> None:
    query = FakePatientQuery(rows=[complete_patient_row()])

    with pytest.raises(UnsupportedPatientAttributeError):
        update_patient_attribute(FakePatientClient(query), uuid4(), "email", "new@example.com")

    assert query.updated is None


def test_update_patient_attribute_wraps_invalid_value() -> None:
    query = FakePatientQuery(rows=[complete_patient_row()])

    with pytest.raises(PatientAttributeValidationError) as error:
        update_patient_attribute(FakePatientClient(query), uuid4(), "phone", "invalid")

    assert error.value.key == "phone"
    assert error.value.error_count >= 1
    assert query.updated is None


def test_update_patient_attribute_returns_not_found_for_no_updated_row() -> None:
    with pytest.raises(PatientNotFoundError):
        update_patient_attribute(
            FakePatientClient(FakePatientQuery(rows=[])),
            uuid4(),
            "gender",
            "other",
        )


def test_update_patient_attribute_wraps_external_failure() -> None:
    query = FakePatientQuery(failure=RuntimeError("private database failure"))

    with pytest.raises(PatientServiceUnavailableError):
        update_patient_attribute(FakePatientClient(query), uuid4(), "gender", "other")


def test_update_patient_attribute_validates_returned_complete_profile() -> None:
    patient_id = uuid4()
    invalid_row = complete_patient_row(patient_id)
    invalid_row.pop("updated_at")

    with pytest.raises(PatientDataIntegrityError):
        update_patient_attribute(
            FakePatientClient(FakePatientQuery(rows=[invalid_row])),
            patient_id,
            "gender",
            "other",
        )


def test_update_logs_never_include_value_or_patient_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    patient_id = uuid4()
    sensitive_name = "Private Patient Name"
    caplog.set_level(logging.INFO)

    update_patient_attribute(
        FakePatientClient(FakePatientQuery(rows=[complete_patient_row(patient_id)])),
        patient_id,
        "full_name",
        sensitive_name,
    )

    assert sensitive_name not in caplog.text
    assert str(patient_id) not in caplog.text
    assert any(record.message == "patient_profile_attribute_updated" for record in caplog.records)
