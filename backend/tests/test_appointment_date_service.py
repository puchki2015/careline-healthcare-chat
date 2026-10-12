"""Deterministic coverage for patient-friendly appointment date phrases."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.appointment_date_service import (
    AppointmentDateResolutionError,
    resolve_appointment_date_range,
)


def fixed_clock(zone: ZoneInfo) -> datetime:
    """Friday October 9, 2026 at 8:46 p.m. in the requested timezone."""

    return datetime(2026, 10, 9, 20, 46, tzinfo=zone)


@pytest.mark.parametrize(
    ("phrase", "expected_start", "expected_end"),
    [
        ("next week", "2026-10-12T00:00:00-07:00", "2026-10-19T00:00:00-07:00"),
        (
            "tomorrow afternoon",
            "2026-10-10T12:00:00-07:00",
            "2026-10-10T17:00:00-07:00",
        ),
        ("this Monday", "2026-10-12T00:00:00-07:00", "2026-10-13T00:00:00-07:00"),
        ("next weekend", "2026-10-17T00:00:00-07:00", "2026-10-19T00:00:00-07:00"),
    ],
)
def test_resolves_calendar_phrases_in_patient_timezone(
    phrase: str, expected_start: str, expected_end: str
) -> None:
    starts_at, ends_at = resolve_appointment_date_range(
        date_from=phrase,
        date_to=None,
        fallback_text="",
        timezone_name="America/Vancouver",
        now_provider=fixed_clock,
    )

    assert starts_at.isoformat() == expected_start
    assert ends_at.isoformat() == expected_end


def test_how_soon_uses_bounded_search_window_from_message() -> None:
    starts_at, ends_at = resolve_appointment_date_range(
        date_from=None,
        date_to=None,
        fallback_text="How soon is a cardiology appointment available?",
        timezone_name="America/Vancouver",
        now_provider=fixed_clock,
    )

    assert starts_at == fixed_clock(ZoneInfo("America/Vancouver"))
    assert (ends_at - starts_at).days == 30


def test_unrecognized_phrase_is_not_guessed() -> None:
    with pytest.raises(AppointmentDateResolutionError):
        resolve_appointment_date_range(
            date_from="when convenient",
            date_to=None,
            fallback_text="whenever",
            timezone_name="UTC",
            now_provider=fixed_clock,
        )
