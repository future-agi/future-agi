"""The ops alerts of DESIGN.md section 13.3 as pure rules over one gauge snapshot and the alerts' previous state.

The health pass (every 60 s) computes the snapshot from SQL, calls :func:`evaluate`, stores the new states and logs
each change: a firing alert is an error log line with the alert id (the error pipeline and the critical channel), a
cleared one an info line. An alert with a duration ("for 5 min") fires only once its condition has held that long; it
clears at the first snapshot where the condition no longer holds.

Event-driven alerts are not here: WA6 (a decrypt failure) is raised where the decrypt fails, WA7 (B8 intake) by
the B8 intake on the FA fork, and WA11 (a producer could not publish an event; the user's change committed without it)
by the producer's own error line (``webhooks.producer.failed``, today the prompt events of ``model_hub``).

WA10 and WA11 are added to section 13.3's list here. WA10 (WH-1a left "a dedicated alert" for a poisoned event to
this lane): an event whose fan-out failed in the last 10 minutes (malformed data, no builder, a body over 64 KB, a
database error on its rows). Nothing is delivered for such an event and it is not retried, so one is enough to fire.

``scale`` is FI_WEBHOOKS_TIME_SCALE as the adapter decided it: it scales the windows and the age thresholds, never
the ratios or counts. While sending is deliberately off on the install (``sending_blocked`` in the snapshot:
``FI_WEBHOOKS_ENABLED=false`` or a hosted gate), events queue by design, so the backlog alerts WA1-WA3 hold back.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .schedule import _ms

WA1_OLDEST_DUE_S = 60
WA1_FOR_S = 300
WA2_NO_BEAT_S = 120
WA3_LAG_P95_S = 10
WA3_FOR_S = 600
WA4_NET_SHARE = 0.5
WA4_MIN_ENDPOINTS = 20
WA5_SSRF_PER_MIN = 20
ATTEMPT_LOG_QUOTA = 5_000_000
"""Attempt rows kept per org (section 7.8); past it the oldest excerpts are dropped and WA9 fires."""


@dataclass(frozen=True)
class AlertState:
    active: bool = False
    since_ms: int | None = None
    """When the condition started to hold (it may not have fired yet)."""
    fired_at_ms: int | None = None
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    means: str
    sustain_s: int
    holds: Callable[[Mapping[str, Any], float], tuple[bool, dict]]


def _wa1(s, scale):
    if s.get("sending_blocked"):
        return False, {"sending_blocked": True}
    age = s.get("oldest_due_seconds")
    return (
        age is not None and age * 1000 > _ms(WA1_OLDEST_DUE_S, scale),
        {"oldest_due_seconds": age},
    )


def _wa2(s, scale):
    """Per loop: ``loop_beat_age_seconds`` maps each configured loop to its beat age (None: never beaten), so one of
    two loops down fires with its id. A snapshot without the map is judged on ``last_beat_age_seconds`` alone."""
    if s.get("sending_blocked"):
        return False, {"sending_blocked": True}
    due = s.get("due", 0)
    loops = s.get("loop_beat_age_seconds")
    if loops is None:
        loops = {"any": s.get("last_beat_age_seconds")}
    stale = sorted(
        loop
        for loop, age in loops.items()
        if age is None or age * 1000 > _ms(WA2_NO_BEAT_S, scale)
    )
    return due > 0 and bool(stale), {"due": due, "stale_loops": stale}


def _wa3(s, scale):
    if s.get("sending_blocked"):
        return False, {"sending_blocked": True}
    p95 = s.get("fanout_lag_p95_seconds")
    return (
        p95 is not None and p95 * 1000 > _ms(WA3_LAG_P95_S, scale),
        {"fanout_lag_p95_seconds": p95},
    )


def _wa4(s, _scale):
    total, net = s.get("attempts_5m", 0), s.get("net_errors_5m", 0)
    eps = s.get("net_error_endpoints_5m", 0)
    held = total > 0 and net / total > WA4_NET_SHARE and eps >= WA4_MIN_ENDPOINTS
    return held, {"attempts": total, "net_errors": net, "endpoints": eps}


def _wa5(s, _scale):
    orgs = sorted(
        org
        for org, n in (s.get("ssrf_blocked_1m_by_org") or {}).items()
        if n > WA5_SSRF_PER_MIN
    )
    return bool(orgs), {"organizations": orgs}


def _wa8(s, _scale):
    n = s.get("superseded_10m", 0)
    return n > 0, {"superseded": n}


def _wa9(s, _scale):
    orgs = sorted(s.get("orgs_over_attempt_quota") or ())
    return bool(orgs), {"organizations": orgs}


def _wa10(s, _scale):
    n = s.get("fanout_failed_10m", 0)
    return n > 0, {"events": n}


RULES: tuple[Rule, ...] = (
    Rule("WA1", "sending is behind", WA1_FOR_S, _wa1),
    Rule("WA2", "a dispatcher loop is down or the queue is unserved", 0, _wa2),
    Rule("WA3", "the outbox is backing up", WA3_FOR_S, _wa3),
    Rule("WA4", "our egress (NAT or resolvers) is broken, not one receiver", 0, _wa4),
    Rule("WA5", "possible probing", 0, _wa5),
    Rule("WA8", "a sender stalled past its lease (a host or GC problem)", 0, _wa8),
    Rule("WA9", "a failing, busy endpoint is filling the log", 0, _wa9),
    Rule(
        "WA10",
        "an event could not be fanned out; nothing was delivered for it",
        0,
        _wa10,
    ),
)


@dataclass(frozen=True)
class Change:
    id: str
    fired: bool
    """True when the alert fired now; False when it cleared now."""
    means: str
    detail: Mapping[str, Any]


def evaluate(
    snapshot: Mapping[str, Any],
    prior: Mapping[str, AlertState],
    now_ms: int,
    scale: float = 1.0,
) -> tuple[dict[str, AlertState], list[Change]]:
    """The alerts' new states and the changes to log. ``prior`` may miss ids (never evaluated)."""
    states: dict[str, AlertState] = {}
    changes: list[Change] = []
    for rule in RULES:
        old = prior.get(rule.id, AlertState())
        held, detail = rule.holds(snapshot, scale)
        if not held:
            states[rule.id] = AlertState(detail=detail)
            if old.active:
                changes.append(Change(rule.id, False, rule.means, detail))
            continue
        since = old.since_ms if old.since_ms is not None else now_ms
        if old.active:
            states[rule.id] = AlertState(True, since, old.fired_at_ms, detail)
            continue
        sustained = not rule.sustain_s or now_ms - since >= _ms(rule.sustain_s, scale)
        if sustained:
            states[rule.id] = AlertState(True, since, now_ms, detail)
            changes.append(Change(rule.id, True, rule.means, detail))
        else:
            states[rule.id] = AlertState(False, since, None, detail)
    return states, changes
