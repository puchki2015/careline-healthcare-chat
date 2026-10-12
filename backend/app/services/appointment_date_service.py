"""Convert common patient date phrases into safe appointment search windows.

The language model may extract a phrase such as ``next week``, but it does not
perform date arithmetic. This module owns that deterministic calculation so
the same phrase always has the same meaning for a supplied clock and timezone.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class AppointmentDateResolutionError(ValueError):
    """Raised when a phrase cannot safely become one unambiguous date range."""


def _at(day: datetime, hour: int = 0) -> datetime:
    return datetime.combine(day.date(), time(hour=hour), tzinfo=day.tzinfo)


def _part_of_day(day: datetime, text: str) -> tuple[datetime, datetime]:
    """Apply patient-friendly, documented boundaries to a calendar day."""

    if "morning" in text:
        return _at(day, 8), _at(day, 12)
    if "afternoon" in text:
        return _at(day, 12), _at(day, 17)
    if "evening" in text:
        return _at(day, 17), _at(day, 21)
    return _at(day), _at(day + timedelta(days=1))


def _parse_iso_range(
    date_from: str | None, date_to: str | None
) -> tuple[datetime, datetime] | None:
    """Keep support for API clients that already send precise ISO timestamps."""

    if not date_from or not date_to:
        return None
    try:
        starts_at = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        ends_at = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
    except ValueError:
        return None
    if starts_at.tzinfo is None or ends_at.tzinfo is None or ends_at <= starts_at:
        return None
    return starts_at, ends_at


def resolve_appointment_date_range(
    *,
    date_from: str | None,
    date_to: str | None,
    fallback_text: str,
    timezone_name: str,
    now_provider: Callable[[ZoneInfo], datetime] | None = None,
) -> tuple[datetime, datetime]:
    """Resolve an exact ISO range or a supported natural-language expression.

    ``fallback_text`` lets the deterministic layer recognize phrases directly
    from the current patient message even if the model omitted a date field.
    This is especially useful for short follow-ups such as ``next week``.
    """

    precise = _parse_iso_range(date_from, date_to)
    if precise is not None:
        return precise

    zone = ZoneInfo(timezone_name)
    now = (now_provider or (lambda tz: datetime.now(tz)))(zone)
    if now.tzinfo is None:
        raise AppointmentDateResolutionError("The reference clock must include a timezone")

    candidates = " ".join(value for value in (date_from, date_to, fallback_text) if value)
    text = " ".join(candidates.lower().strip().split())

    # "How soon" means the earliest available slot in a bounded 30-day window.
    if any(phrase in text for phrase in ("how soon", "soonest", "earliest", "as soon as possible")):
        result = (now, now + timedelta(days=30))
    elif "day after tomorrow" in text:
        result = _part_of_day(now + timedelta(days=2), text)
    elif "tomorrow" in text:
        result = _part_of_day(now + timedelta(days=1), text)
    elif re.search(r"\btoday\b", text):
        _, end = _part_of_day(now, text)
        start = max(now, _part_of_day(now, text)[0])
        result = (start, end)
    elif "next weekend" in text or "this weekend" in text:
        days_until_saturday = (5 - now.weekday()) % 7
        if "next weekend" in text:
            days_until_saturday = days_until_saturday + 7 if days_until_saturday else 7
        start = _at(now + timedelta(days=days_until_saturday))
        result = (start, start + timedelta(days=2))
    elif re.search(r"\bnext week\b", text):
        days_until_monday = (7 - now.weekday()) % 7 or 7
        start = _at(now + timedelta(days=days_until_monday))
        result = (start, start + timedelta(days=7))
    elif "this week" in text:
        next_monday = _at(now + timedelta(days=7 - now.weekday()))
        result = (now, next_monday)
    elif "next month" in text:
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        start = datetime(year, month, 1, tzinfo=zone)
        end_year = year + (1 if month == 12 else 0)
        end_month = 1 if month == 12 else month + 1
        result = (start, datetime(end_year, end_month, 1, tzinfo=zone))
    elif "this month" in text:
        end_year = now.year + (1 if now.month == 12 else 0)
        end_month = 1 if now.month == 12 else now.month + 1
        result = (now, datetime(end_year, end_month, 1, tzinfo=zone))
    elif match := re.search(r"(?:in )?(?:the )?next (\d{1,2}) (day|days|week|weeks)\b", text):
        amount = int(match.group(1))
        if amount < 1 or amount > 12:
            raise AppointmentDateResolutionError("Relative date amount is outside the safe range")
        days = amount * (7 if match.group(2).startswith("week") else 1)
        result = (now, now + timedelta(days=days))
    elif match := re.search(r"\b(this|next) (" + "|".join(_WEEKDAYS) + r")\b", text):
        modifier, weekday_name = match.groups()
        target = _WEEKDAYS[weekday_name]
        days_ahead = (target - now.weekday()) % 7
        if modifier == "next":
            days_ahead = days_ahead + 7 if days_ahead else 7
        day = now + timedelta(days=days_ahead)
        result = _part_of_day(day, text)
    else:
        raise AppointmentDateResolutionError("No supported appointment date expression was found")

    if result[1] <= result[0]:
        raise AppointmentDateResolutionError("The resolved appointment range is empty")
    logger.info(
        "appointment_date_range_resolved",
        extra={
            "timezone": timezone_name,
            "starts_at": result[0].isoformat(),
            "ends_at": result[1].isoformat(),
        },
    )
    return result
