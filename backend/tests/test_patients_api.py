"""HTTP tests for GET /api/v1/patients/me."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_user, get_user_supabase_client
from app.schemas.auth import CurrentUser
from tests.test_patient_service import FakePatientClient, FakePatientQuery


def api_patient_row(patient_id) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(patient_id),
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


@pytest.fixture
def authenticated_patient_api(test_app: FastAPI) -> Iterator[tuple[TestClient, FakePatientQuery]]:
    patient_id = uuid4()
    query = FakePatientQuery(rows=[api_patient_row(patient_id)])
    patient_client = FakePatientClient(query)
    current_user = CurrentUser(id=patient_id, access_token="verified-test-token")

    test_app.dependency_overrides[get_current_user] = lambda: current_user
    test_app.dependency_overrides[get_user_supabase_client] = lambda: patient_client
    with TestClient(test_app) as client:
        yield client, query
    test_app.dependency_overrides.clear()


def test_get_full_patient_profile(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.get("/api/v1/patients/me")

    assert response.status_code == 200
    assert response.json()["email"] == "patient@example.com"
    assert response.json()["id"] == query.rows[0]["id"]
    assert set(response.json()) == set(query.rows[0])


def test_get_selected_patient_fields(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api
    query.rows = [{"email": "patient@example.com", "phone": None}]

    response = client.get("/api/v1/patients/me?fields=email,phone")

    assert response.status_code == 200
    assert response.json() == {"email": "patient@example.com", "phone": None}
    assert query.selected == "email,phone"


def test_get_patient_rejects_unknown_field(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.get("/api/v1/patients/me?fields=password")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_fields"
    assert "password" not in response.json()["error"]["message"]
    assert query.selected is None


def test_get_patient_rejects_empty_field_name(authenticated_patient_api) -> None:
    client, _ = authenticated_patient_api

    response = client.get("/api/v1/patients/me?fields=email,,phone")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_fields"


def test_get_patient_returns_404(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api
    query.rows = []

    response = client.get("/api/v1/patients/me")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "patient_not_found"


def test_get_patient_returns_safe_503(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api
    query.failure = RuntimeError("private database failure")

    response = client.get("/api/v1/patients/me")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "patient_service_unavailable"
    assert "private database failure" not in response.text


def test_get_patient_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/patients/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_get_patient_is_documented_as_secured(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/patients/me"]["get"]

    assert operation["security"] == [{"Supabase access token": []}]
    assert {"200", "400", "401", "404", "500", "503"}.issubset(operation["responses"])


def test_update_patient_attribute(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.patch(
        "/api/v1/patients/me/attributes/phone",
        json={"value": "  +16045550999  "},
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+16045550999"
    assert query.updated == {"phone": "+16045550999"}


def test_update_patient_optional_attribute_to_null(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.patch(
        "/api/v1/patients/me/attributes/gender",
        json={"value": None},
    )

    assert response.status_code == 200
    assert response.json()["gender"] is None
    assert query.updated == {"gender": None}


def test_update_patient_rejects_protected_attribute(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.patch(
        "/api/v1/patients/me/attributes/email",
        json={"value": "new@example.com"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "attribute_not_editable"
    assert "new@example.com" not in response.text
    assert query.updated is None


def test_update_patient_rejects_invalid_attribute_value(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api

    response = client.patch(
        "/api/v1/patients/me/attributes/phone",
        json={"value": "not-a-phone-number"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_attribute_value"
    assert "not-a-phone-number" not in response.text
    assert query.updated is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"value": "Alice", "email": "unexpected@example.com"},
    ],
)
def test_update_patient_rejects_invalid_body_structure(
    authenticated_patient_api,
    body: dict[str, object],
) -> None:
    client, query = authenticated_patient_api

    response = client.patch(
        "/api/v1/patients/me/attributes/full_name",
        json=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert query.updated is None


def test_update_patient_returns_404(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api
    query.rows = []

    response = client.patch(
        "/api/v1/patients/me/attributes/gender",
        json={"value": "other"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "patient_not_found"


def test_update_patient_returns_safe_503(authenticated_patient_api) -> None:
    client, query = authenticated_patient_api
    query.failure = RuntimeError("private update failure")

    response = client.patch(
        "/api/v1/patients/me/attributes/gender",
        json={"value": "other"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "patient_service_unavailable"
    assert "private update failure" not in response.text


def test_update_patient_requires_authentication(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/patients/me/attributes/phone",
        json={"value": "+16045550123"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_update_patient_is_documented_as_secured(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/patients/me/attributes/{key}"][
        "patch"
    ]

    assert operation["security"] == [{"Supabase access token": []}]
    assert {"200", "400", "401", "404", "422", "500", "503"}.issubset(operation["responses"])
