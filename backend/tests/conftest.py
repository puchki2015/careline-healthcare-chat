"""Shared test fixtures with isolated, non-secret configuration."""

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Return valid settings that never depend on a developer's real ``.env``."""

    return Settings(
        environment="test",
        log_level="DEBUG",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-role-key",
        frontend_origin="http://localhost:5173",
    )


@pytest.fixture
def test_app(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    # app.main exposes a production-style module-level ``app`` for Uvicorn.
    # Supplying safe environment values before importing it keeps tests able to
    # run from a clean clone where the ignored developer .env does not exist.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("ENVIRONMENT", "test")

    from app.main import create_app

    return create_app(test_settings)


@pytest.fixture
def client(test_app: FastAPI) -> Generator[TestClient]:
    with TestClient(test_app) as test_client:
        yield test_client
