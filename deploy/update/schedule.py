"""Pure UTC scheduling. Windows permit starts, not guaranteed completion.

Callers persist the returned deadlines and reserve occurrences before backup.
No sleeps, Docker, network, host-local time, missed-tick queue or mutations here.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import AutomationPolicy, ContractError, MaintenanceWindowSpec, ReasonCode

UTC = timezone.utc


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(ReasonCode.CLOCK_SKEW.value)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ClockSample:
    wall: datetime
    monotonic: float

    def __post_init__(self) -> None:
        utc(self.wall)
        if type(self.monotonic) not in (int, float) or not math.isfinite(self.monotonic):
            raise ContractError(ReasonCode.CLOCK_SKEW.value)


class Clock:
    """Injected UTC and monotonic sources; monotonic values never survive reboot."""

    def __init__(self, wall: Callable[[], datetime], monotonic: Callable[[], float]) -> None:
        self.wall = wall
        self.monotonic = monotonic

    def sample(self) -> ClockSample:
        return ClockSample(utc(self.wall()), self.monotonic())


def validate_clock(current: ClockSample, previous: Optional[ClockSample] = None,
                   observed: Iterable[Optional[datetime]] = ()) -> None:
    now = utc(current.wall)
    if previous is not None:
        elapsed = current.monotonic - previous.monotonic
        wall_elapsed = (now - utc(previous.wall)).total_seconds()
        if elapsed < 0 or elapsed - wall_elapsed > 300:
            raise ContractError(ReasonCode.CLOCK_SKEW.value)
    # Future *deadlines* are not observations and must not be supplied here.
    if any(value is not None and (utc(value) - now).total_seconds() > 300 for value in observed):
        raise ContractError(ReasonCode.CLOCK_SKEW.value)


def _zone(spec: MaintenanceWindowSpec):
    if spec.timezone == "UTC":
        return UTC
    try:
        return ZoneInfo(spec.timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ContractError(ReasonCode.TIMEZONE_UNAVAILABLE.value) from None


def _minutes(endpoint: str) -> int:
    hour, minute = endpoint.split(":")
    return 60 * int(hour) + int(minute)


def occurrence(spec: Optional[MaintenanceWindowSpec], now: datetime) -> Optional[str]:
    """Both folds share a key; a cross-midnight tail uses its start weekday."""
    now = utc(now)
    if spec is None:
        return None
    local = now.astimezone(_zone(spec))
    if spec.kind == "always":
        return "always"
    start, end = _minutes(spec.start), _minutes(spec.end)
    minute = local.hour * 60 + local.minute
    start_date = local.date()
    if start < end:
        if not start <= minute < end:
            return None
    else:
        if minute < end:
            start_date -= timedelta(days=1)
        elif minute < start:
            return None
    if start_date.isoweekday() not in spec.days_of_week:
        return None
    return "{}|{}|{}-{}".format(spec.timezone, start_date.isoformat(), spec.start, spec.end)


def next_open(spec: Optional[MaintenanceWindowSpec], now: datetime) -> Optional[datetime]:
    """Enumerate real instants, never normalize a nonexistent local time forward.

    Eight start dates cover a complete week plus the cross-midnight tail. Each
    local minute is round-tripped in both folds, using the membership predicate.
    Thus a wholly missing spring window is skipped, not delayed into a new one.
    """
    now = utc(now)
    if spec is None:
        return None
    if occurrence(spec, now) is not None:
        return now
    zone = _zone(spec)
    local_date = now.astimezone(zone).date()
    start, end = _minutes(spec.start), _minutes(spec.end)
    duration = (end - start) % 1440
    candidates = []
    for offset in range(-1, 9):
        day = local_date + timedelta(days=offset)
        if day.isoweekday() not in spec.days_of_week:
            continue
        beginning = datetime.combine(day, datetime.min.time()) + timedelta(minutes=start)
        for minute in range(duration):
            naive = beginning + timedelta(minutes=minute)
            for fold in (0, 1):
                candidate = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                if candidate >= now and candidate.astimezone(zone).replace(tzinfo=None) == naive:
                    if occurrence(spec, candidate) is not None:
                        candidates.append(candidate)
    return min(candidates) if candidates else None


def remaining_seconds(spec: Optional[MaintenanceWindowSpec], now: datetime) -> float:
    """Continuous remaining opening, not the sum of disconnected fold pieces."""
    now = utc(now)
    key = occurrence(spec, now)
    if key is None:
        return 0
    if spec.kind == "always":
        return math.inf
    # Local endpoints have minute precision. Test each following real second to
    # support historical IANA offsets/second-resolution transitions too. Bounded
    # at 49h, including date-line changes; no I/O or clock reads in this loop.
    cursor = now.replace(microsecond=0) + timedelta(seconds=1)
    for _ in range(49 * 3600):
        if occurrence(spec, cursor) != key:
            return (cursor - now).total_seconds()
        cursor += timedelta(seconds=1)
    raise ContractError(ReasonCode.TIMEZONE_UNAVAILABLE.value)


def start_gate(policy: AutomationPolicy, now: datetime, *, consumed: Optional[str] = None) -> Optional[ReasonCode]:
    key = occurrence(policy.maintenance_window, now)
    if key is None or (key != "always" and key == consumed):
        return ReasonCode.OUTSIDE_WINDOW
    if remaining_seconds(policy.maintenance_window, now) < policy.min_start_budget_seconds:
        return ReasonCode.INSUFFICIENT_WINDOW
    return None


def jitter_seconds(seed: str, label: str, seconds: int) -> int:
    """Stable positive-only 0..10% jitter; persist the resulting UTC deadline."""
    if type(seed) is not str or not seed or type(seconds) is not int or seconds < 0:
        raise ContractError()
    digest = hashlib.sha256((seed + "\0" + label).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (seconds // 10 + 1)


def successful_check_deadline(policy: AutomationPolicy, now: datetime, seed: str) -> datetime:
    now = utc(now)
    base = policy.check_interval_seconds
    return now + timedelta(seconds=base + jitter_seconds(seed, "check:" + now.isoformat(), base))


@dataclass(frozen=True)
class RetryDeadline:
    at: datetime
    retry_count: int
    reason_code: Optional[ReasonCode]


def retry_deadline(policy: AutomationPolicy, now: datetime, seed: str, retry_count: int,
                   retry_after: Optional[datetime] = None) -> RetryDeadline:
    """retry_count is the number of *prior* transient failures (first is zero)."""
    now = utc(now)
    if type(retry_count) is not int or not 0 <= retry_count < 2**63 - 1:
        raise ContractError(ReasonCode.CONTROL_INVALID.value)
    cap = min(86400, policy.max_backoff_seconds)
    base = min(cap, 300 * 2 ** min(retry_count, 9))
    delay = min(cap, base + jitter_seconds(seed, "retry:" + str(retry_count), base))
    reason = None
    if retry_after is not None:
        requested = max(0, (utc(retry_after) - now).total_seconds())
        delay = max(delay, min(86400, math.ceil(requested)))
        reason = ReasonCode.RATE_LIMITED
    return RetryDeadline(now + timedelta(seconds=delay), retry_count + 1, reason)


def check_due(now: datetime, next_check_at: Optional[datetime], retry_not_before: Optional[datetime]) -> bool:
    now = utc(now)
    # A retry replaces the normal interval deadline; otherwise a transient
    # failure at a revalidation would accidentally wait six hours.
    deadline = retry_not_before if retry_not_before is not None else next_check_at
    return deadline is None or now >= utc(deadline)
