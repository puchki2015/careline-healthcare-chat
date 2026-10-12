"""Tests for strict and safe application configuration."""

from pydantic import SecretStr

from app.core.config import Settings


def test_settings_hide_secret_values(test_settings: Settings) -> None:
    representation = repr(test_settings)

    assert isinstance(test_settings.supabase_anon_key, SecretStr)
    assert "test-anon-key" not in representation
    assert "test-service-role-key" not in representation


def test_frontend_origin_is_normalized_for_cors(test_settings: Settings) -> None:
    assert test_settings.frontend_origin_value == "http://localhost:5173"


def test_openai_model_accepts_existing_openai_model_environment_name(
    monkeypatch,
) -> None:
    """OPENAI_MODEL in a developer .env must configure the classifier model."""

    monkeypatch.delenv("CHAT_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.4-mini")

    settings = Settings(
        _env_file=None,
        supabase_url="https://example.supabase.co",
        supabase_anon_key="test-anon-key",
        supabase_service_role_key="test-service-role-key",
        frontend_origin="http://localhost:5173",
    )

    assert settings.chat_model == "gpt-5.4-mini"


def test_chat_context_window_is_bounded(test_settings: Settings) -> None:
    assert test_settings.chat_context_message_limit == 20
    assert test_settings.chat_prior_session_limit == 3
    assert test_settings.chat_summary_message_limit == 30
    assert test_settings.chat_summary_max_chars == 2000
