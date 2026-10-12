"""API tests for bearer extraction, safe errors, logs, and OpenAPI security."""

import logging
from uuid import uuid4

from fastapi.testclient import TestClient

import app.deps
from app.core.security import AuthenticationError
from app.schemas.auth import CurrentUser


def test_missing_token_returns_consistent_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/verify")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "A valid bearer access token is required.",
            "request_id": response.headers["X-Request-ID"],
        }
    }


def test_non_bearer_scheme_returns_401(client: TestClient) -> None:
    response = client.get(
        "/api/v1/auth/verify",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_valid_token_reaches_protected_endpoint(
    client: TestClient,
    monkeypatch,
) -> None:
    def accept_test_token(token: str, _settings) -> CurrentUser:
        assert token == "valid-test-token"
        return CurrentUser(id=uuid4(), access_token=token)

    monkeypatch.setattr(app.deps, "verify_access_token", accept_test_token)

    response = client.get(
        "/api/v1/auth/verify",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


def test_invalid_token_reason_and_token_are_not_returned(
    client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    sensitive_token = "sensitive-token-value"

    def reject_test_token(_token: str, _settings) -> CurrentUser:
        raise AuthenticationError("expired")

    monkeypatch.setattr(app.deps, "verify_access_token", reject_test_token)
    caplog.set_level(logging.INFO)

    response = client.get(
        "/api/v1/auth/verify",
        headers={"Authorization": f"Bearer {sensitive_token}"},
    )

    assert response.status_code == 401
    assert "expired" not in response.text
    assert sensitive_token not in response.text
    assert sensitive_token not in caplog.text
    assert any(
        getattr(record, "auth_failure_reason", None) == "expired" for record in caplog.records
    )


def test_openapi_declares_bearer_security(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/auth/verify"]["get"]
    security_schemes = schema["components"]["securitySchemes"]

    assert operation["security"] == [{"Supabase access token": []}]
    assert security_schemes["Supabase access token"]["scheme"] == "bearer"
