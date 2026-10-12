"""Static portability checks for the complete Supabase installation bundle."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
SUPABASE = ROOT / "supabase"
MIGRATIONS = SUPABASE / "migrations"

EXPECTED_MIGRATIONS = [
    "20261006165524_create_patients_table.sql",
    "20261006165647_create_patient_on_signup.sql",
    "20261006165722_add_updated_at_trigger.sql",
    "20261006165743_enable_rls_patients.sql",
    "20261007120000_create_chat_history_tables.sql",
    "20261007160000_add_atomic_chat_turn_functions.sql",
    "20261007170000_serialize_chat_session_turns.sql",
    "20261007180000_add_durable_pending_actions.sql",
    "20261007190000_add_cross_session_memory.sql",
    "20261008120000_make_chat_role_aware.sql",
    "20261009120000_create_appointment_coordination.sql",
    "20261010120000_allow_appointment_chat_intent.sql",
]


def test_complete_migration_manifest_is_present_in_order() -> None:
    """Fail when a required migration is renamed, misplaced, or accidentally removed."""

    actual = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert actual == EXPECTED_MIGRATIONS
    assert all((MIGRATIONS / name).stat().st_size > 0 for name in actual)


def test_seed_configuration_points_to_existing_environment_neutral_file() -> None:
    """A clean `db reset` must not fail because its configured seed is absent."""

    config = (SUPABASE / "config.toml").read_text(encoding="utf-8")
    seed = SUPABASE / "seed.sql"

    assert 'sql_paths = ["./seed.sql"]' in config
    assert seed.is_file()
    seed_text = seed.read_text(encoding="utf-8").lower()
    assert "insert into" not in seed_text
    assert "auth.users" not in seed_text


def test_no_unclassified_sql_files_are_left_in_supabase_root() -> None:
    """Only the configured seed belongs outside the migrations directory."""

    assert sorted(path.name for path in SUPABASE.glob("*.sql")) == ["seed.sql"]
