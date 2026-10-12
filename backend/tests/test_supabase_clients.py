"""Tests for patient-scoped and administrator Supabase client factories."""

import logging
from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.core.supabase as supabase_module
from app.core.config import Settings
from app.core.supabase import (
    SupabaseClientCreationError,
    _create_cached_admin_client,
    create_user_client,
    get_admin_client,
)
from app.schemas.auth import CurrentUser


@pytest.fixture(autouse=True)
def clear_admin_client_cache():
    """Prevent one test's cached privileged client from affecting another."""

    _create_cached_admin_client.cache_clear()
    yield
    _create_cached_admin_client.cache_clear()


def test_user_client_uses_anon_key_and_verified_token(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "patient-access-token"
    current_user = CurrentUser(id=uuid4(), access_token=token)
    expected_client = Mock(name="patient_client")
    create_client_mock = Mock(return_value=expected_client)
    monkeypatch.setattr(supabase_module, "create_client", create_client_mock)

    result = create_user_client(current_user, test_settings)

    assert result is expected_client
    _, supplied_key = create_client_mock.call_args.args
    supplied_options = create_client_mock.call_args.kwargs["options"]
    assert supplied_key == "test-anon-key"
    assert supplied_options.headers["Authorization"] == f"Bearer {token}"
    assert supplied_options.auto_refresh_token is False
    assert supplied_options.persist_session is False


def test_user_client_is_created_once_per_call(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = CurrentUser(id=uuid4(), access_token="patient-access-token")
    create_client_mock = Mock(side_effect=[Mock(name="first"), Mock(name="second")])
    monkeypatch.setattr(supabase_module, "create_client", create_client_mock)

    first = create_user_client(current_user, test_settings)
    second = create_user_client(current_user, test_settings)

    assert first is not second
    assert create_client_mock.call_count == 2


def test_admin_client_uses_service_role_key_and_is_cached(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_client = Mock(name="admin_client")
    create_client_mock = Mock(return_value=expected_client)
    monkeypatch.setattr(supabase_module, "create_client", create_client_mock)

    first = get_admin_client(test_settings)
    second = get_admin_client(test_settings)

    assert first is expected_client
    assert second is expected_client
    assert create_client_mock.call_count == 1
    _, supplied_key = create_client_mock.call_args.args
    supplied_options = create_client_mock.call_args.kwargs["options"]
    assert supplied_key == "test-service-role-key"
    assert supplied_options.auto_refresh_token is False
    assert supplied_options.persist_session is False


@pytest.mark.parametrize("scope", ["user", "admin"])
def test_client_creation_failures_raise_safe_error(
    scope: str,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_value = "patient-access-token" if scope == "user" else "test-service-role-key"
    monkeypatch.setattr(
        supabase_module,
        "create_client",
        Mock(side_effect=RuntimeError(f"failure involving {sensitive_value}")),
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SupabaseClientCreationError, match=scope):
        if scope == "user":
            create_user_client(
                CurrentUser(id=uuid4(), access_token=sensitive_value),
                test_settings,
            )
        else:
            get_admin_client(test_settings)

    assert sensitive_value not in caplog.text
    assert any(getattr(record, "client_scope", None) == scope for record in caplog.records)


def test_success_logs_do_not_contain_credentials(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(supabase_module, "create_client", Mock(return_value=Mock()))
    caplog.set_level(logging.DEBUG)

    create_user_client(
        CurrentUser(id=uuid4(), access_token="patient-access-token"),
        test_settings,
    )
    get_admin_client(test_settings)

    assert "patient-access-token" not in caplog.text
    assert "test-anon-key" not in caplog.text
    assert "test-service-role-key" not in caplog.text
    assert "example.supabase.co" not in caplog.text
