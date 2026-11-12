"""The security-report SLA clock (SPEC §10): business hours in the Hub's support calendar.

The calendar is three settings (omega_hub/conf.py):

- OMEGA_HUB_SUPPORT_TIMEZONE, an IANA zone (default UTC);
- OMEGA_HUB_SUPPORT_HOURS, the support day as `HH:MM-HH:MM` in that zone, Monday to Friday
  (default 09:00-17:00);
- OMEGA_HUB_SUPPORT_HOLIDAYS, comma-separated `YYYY-MM-DD` dates in that zone that count no hours.

A value that does not parse is reported by `manage.py check` (omega_hub.W008, ops_checks.py) and by
an `omega_hub_support_calendar_invalid` error log on every SLA evaluation; the clock then runs on
the default calendar rather than not at all, so a typo can never stop the security page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from omega_hub import conf

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Calendar:
    zone: ZoneInfo
    opens: time
    closes: time
    holidays: frozenset[date]


DEFAULT = Calendar(ZoneInfo("UTC"), time(9), time(17), frozenset())


def _clock(text: str) -> time:
    hours, minutes = text.strip().split(":")
    return time(int(hours), int(minutes))


def parse(zone: str, hours: str, holidays: str) -> Calendar:
    """The calendar the three settings describe; ValueError naming the bad setting otherwise."""
    try:
        tz = ZoneInfo(zone.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"OMEGA_HUB_SUPPORT_TIMEZONE {zone!r} is not an IANA time zone") from exc
    try:
        opens, closes = (_clock(part) for part in hours.split("-"))
    except ValueError as exc:
        raise ValueError(f"OMEGA_HUB_SUPPORT_HOURS {hours!r} is not HH:MM-HH:MM") from exc
    if opens >= closes:
        raise ValueError(f"OMEGA_HUB_SUPPORT_HOURS {hours!r} closes before it opens")
    try:
        days = frozenset(date.fromisoformat(part.strip()) for part in holidays.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError(f"OMEGA_HUB_SUPPORT_HOLIDAYS {holidays!r} is not a list of YYYY-MM-DD dates") from exc
    return Calendar(tz, opens, closes, days)


def configured() -> Calendar:
    """The configured calendar; ValueError when a setting does not parse."""
    return parse(conf.support_timezone(), conf.support_hours(), conf.support_holidays())


def problems() -> list[str]:
    try:
        configured()
    except ValueError as exc:
        return [str(exc)]
    return []


def current() -> Calendar:
    try:
        return configured()
    except ValueError as exc:
        logger.error("omega_hub_support_calendar_invalid", error=str(exc), using="09:00-17:00 UTC, Monday to Friday")
        return DEFAULT


def business_hours(start: datetime, end: datetime, calendar: Calendar | None = None) -> float:
    """Hours between `start` and `end` that fall inside the calendar's support days."""
    calendar = calendar or current()
    if start >= end:
        return 0.0
    local_start, local_end = start.astimezone(calendar.zone), end.astimezone(calendar.zone)
    total = 0.0
    day = local_start.date()
    while day <= local_end.date():
        if day.weekday() < 5 and day not in calendar.holidays:
            opens = datetime.combine(day, calendar.opens, tzinfo=calendar.zone)
            closes = datetime.combine(day, calendar.closes, tzinfo=calendar.zone)
            left, right = max(local_start, opens), min(local_end, closes)
            if right > left:
                # Subtract in UTC: aware datetimes sharing one tzinfo subtract as wall-clock times.
                total += (right.astimezone(UTC) - left.astimezone(UTC)).total_seconds() / 3600
        day += timedelta(days=1)
    return total
