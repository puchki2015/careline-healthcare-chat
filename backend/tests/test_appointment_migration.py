"""Regression checks for appointment values required by the SQL schema."""

from pathlib import Path

from app.schemas.chat import ChatIntent


def test_appointment_intent_is_supported_by_code_and_latest_migration() -> None:
    """Prevent code and the database check constraint from drifting again."""

    migration = (
        Path(__file__).parents[2]
        / "supabase"
        / "migrations"
        / "20261010120000_allow_appointment_chat_intent.sql"
    ).read_text(encoding="utf-8")

    assert ChatIntent.MANAGE_APPOINTMENT.value == "manage_appointment"
    assert "'manage_appointment'" in migration
    assert "chat_runs_intent_valid" in migration
