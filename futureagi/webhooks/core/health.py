"""Endpoint health kept incrementally on the endpoint's state row (DESIGN.md section 7.7; R6, R9).

:func:`webhooks.core.schedule.endpoint_health` states the rules over a whole attempt history. A dispatcher cannot
re-read every attempt of an endpoint on each settle, so the state row keeps just enough to decide the same thing:
the current run of consecutive failures (its length, its first and last time, the trailing ``ssrf_blocked`` count and
the last failure's outcome), the last 20 latency samples and the timeouts of the last 10 minutes. :func:`fold` adds
one settled attempt; :func:`decide` answers exactly what ``endpoint_health`` answers for the history folded so far
(``test_core_health`` proves it on every ``schedule.json`` health vector and on random histories).

Pure functions; ``scale`` is FI_WEBHOOKS_TIME_SCALE as the adapter decided it (section 7.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .schedule import (
    AUTO_DISABLE_AGE_S,
    AUTO_DISABLE_MIN_FAILURES,
    FAILED_FOR_HEALTH,
    FAILING_SPAN_S,
    SLOW_LANE_S,
    SLOW_P50_MS,
    SLOW_SAMPLES,
    SLOW_TIMEOUTS,
    SLOW_WINDOW_S,
    SSRF_DISABLE_RUN,
    _ms,
)

FAILING_NOTICE_EVERY_S = 24 * 3600
"""The "failing" email goes out at most once per 24 h per endpoint (section 7.7, ``last_failing_notice_at``)."""

IGNORED = frozenset({"worker_lost", "superseded"})


@dataclass(frozen=True)
class HealthState:
    """What the state row keeps. Times are epoch milliseconds; lists are oldest first."""

    consecutive_failed: int = 0
    consecutive_ssrf: int = 0
    failing_since_ms: int | None = None
    """The first failed attempt of the current run (``failing_since``)."""
    last_failure_ms: int | None = None
    last_failure_outcome: str | None = None
    latencies_ms: tuple[int, ...] = field(default_factory=tuple)
    """The last 20 ``duration_ms`` samples of non-test attempts, any outcome."""
    timeouts_ms: tuple[int, ...] = field(default_factory=tuple)
    """``at_ms`` of the timeouts inside the slow-lane window."""


def counts(attempt: Mapping[str, Any]) -> bool:
    """Whether an attempt counts toward health: tests, ``worker_lost`` and ``superseded`` never do."""
    return not attempt.get("test") and attempt["outcome"] not in IGNORED


def fold(
    state: HealthState, attempt: Mapping[str, Any], scale: float = 1.0
) -> HealthState:
    """``state`` after one more settled attempt ``{outcome, at_ms, duration_ms?, test?}``, oldest first."""
    if not counts(attempt):
        return state
    at = int(attempt["at_ms"])
    lat = state.latencies_ms
    if attempt.get("duration_ms") is not None:
        lat = (*lat, int(attempt["duration_ms"]))[-SLOW_SAMPLES:]
    window = _ms(SLOW_WINDOW_S, scale)
    tos = tuple(t for t in state.timeouts_ms if at - t <= window)
    if attempt["outcome"] == "timeout":
        tos = (*tos, at)
    s = replace(state, latencies_ms=lat, timeouts_ms=tos)
    outcome = attempt["outcome"]
    if outcome == "success":
        return replace(
            s,
            consecutive_failed=0,
            consecutive_ssrf=0,
            failing_since_ms=None,
            last_failure_ms=None,
            last_failure_outcome=None,
        )
    if outcome not in FAILED_FOR_HEALTH:
        return s
    return replace(
        s,
        consecutive_failed=s.consecutive_failed + 1,
        consecutive_ssrf=s.consecutive_ssrf + 1 if outcome == "ssrf_blocked" else 0,
        failing_since_ms=s.failing_since_ms if s.consecutive_failed else at,
        last_failure_ms=at,
        last_failure_outcome=outcome,
    )


def decide(state: HealthState, now_ms: int, scale: float = 1.0) -> dict:
    """The same answer as ``schedule.endpoint_health`` over the folded history, at ``now_ms``."""
    run = state.consecutive_failed
    disable = None
    if run and state.last_failure_outcome == "gone":
        disable = "gone_410"
    elif run >= SSRF_DISABLE_RUN and state.consecutive_ssrf >= SSRF_DISABLE_RUN:
        disable = "unsafe_address"
    elif (
        run >= AUTO_DISABLE_MIN_FAILURES
        and state.failing_since_ms is not None
        and now_ms - state.failing_since_ms >= _ms(AUTO_DISABLE_AGE_S, scale)
    ):
        disable = "failing"
    lat = state.latencies_ms[-SLOW_SAMPLES:]
    p50 = sorted(lat)[SLOW_SAMPLES // 2 - 1] if len(lat) == SLOW_SAMPLES else None
    window = _ms(SLOW_WINDOW_S, scale)
    timeouts = [t for t in state.timeouts_ms if now_ms - t <= window]
    slow = (p50 is not None and p50 > SLOW_P50_MS) or len(timeouts) >= SLOW_TIMEOUTS
    failing = (
        run >= 3
        and state.failing_since_ms is not None
        and state.last_failure_ms is not None
        and state.last_failure_ms - state.failing_since_ms >= _ms(FAILING_SPAN_S, scale)
    )
    return {
        "consecutive_failed_attempts": run,
        "disable_endpoint": disable,
        "failing": failing,
        "slow_lane": slow,
        "slow_until_ms": now_ms + _ms(SLOW_LANE_S, scale) if slow else None,
    }


def failing_notice_due(
    failing: bool, last_notice_ms: int | None, now_ms: int, scale: float = 1.0
) -> bool:
    """The "failing" email: when the endpoint is failing and none went out in the last 24 h."""
    if not failing:
        return False
    return last_notice_ms is None or now_ms - last_notice_ms >= _ms(
        FAILING_NOTICE_EVERY_S, scale
    )


def lane_after(
    lane: str, slow_until_ms: int | None, decision: Mapping[str, Any], now_ms: int
) -> tuple[str, int | None]:
    """The lane the endpoint sends from next. Slow when the rules say slow (for 30 min from now, never shortened);
    once ``slow_until`` passed and the rules no longer say slow, back to normal (the 30-minute re-check)."""
    if decision["slow_lane"]:
        until = decision["slow_until_ms"]
        if lane == "slow" and slow_until_ms is not None and slow_until_ms > until:
            until = slow_until_ms
        return "slow", until
    if lane == "slow" and slow_until_ms is not None and slow_until_ms > now_ms:
        return "slow", slow_until_ms
    return "normal", None
