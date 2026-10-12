"""API tests for routing, CORS, health checks, and request IDs."""

from uuid import UUID

from fastapi.testclient import TestClient


def test_versioned_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    UUID(response.headers["X-Request-ID"])


def test_legacy_health_alias(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_valid_request_id_is_preserved(client: TestClient) -> None:
    request_id = "7b777a5b-0d2a-464a-95c5-e37acbd57c00"

    response = client.get("/api/v1/health", headers={"X-Request-ID": request_id})

    assert response.headers["X-Request-ID"] == request_id


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "unsafe-value"})

    assert response.headers["X-Request-ID"] != "unsafe-value"
    UUID(response.headers["X-Request-ID"])


def test_cors_allows_configured_frontend(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_does_not_allow_unknown_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://malicious.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_openapi_documents_only_versioned_health_route(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/health" in paths
    assert "/health" not in paths
