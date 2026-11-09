"""Answer classes, retry schedules with jitter, max age and endpoint health as pure functions of the attempt history
(DESIGN.md sections 7.2, 7.3 and 7.7; conformance ``schedule.json``).

``scale`` is FI_WEBHOOKS_TIME_SCALE (section 7.3, R10). The core applies whatever it is given; the adapter decides
whether the setting may apply at all (never on hosted, never with DEBUG off) and passes 1.0 otherwise.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from email.utils import parsedate_to_datetime
from typing import Any

STANDARD_DELAYS_S = {
    2: 5,
    3: 30,
    4: 120,
    5: 600,
    6: 1800,
    7: 3600,
    8: 7200,
    9: 14400,
    10: 28800,
    11: 43200,
    12: 43200,
    13: 43200,
    14: 43200,
}
REJECTED_DELAYS_S = {2: 5, 3: 60, 4: 600, 5: 2700}
MAX_ATTEMPTS = {"standard": 14, "rejected": 5}
REJECTED_STATUSES = frozenset({400, 401, 403, 404, 405, 415, 422})
RETRY_AFTER_CAP_S = 3600
RATE_HALVE_S = 300
DEFAULT_MAX_AGE_S = 72 * 3600
NON_HTTP_OUTCOMES = frozenset(
    {
        "timeout",
        "dns_error",
        "connect_error",
        "tls_error",
        "ssrf_blocked",
        "worker_lost",
    }
)
FAILED_FOR_HEALTH = frozenset(
    {
        "http_error",
        "rejected",
        "timeout",
        "dns_error",
        "connect_error",
        "tls_error",
        "ssrf_blocked",
        "redirect_refused",
        "gone",
        "rate_limited",
    }
)
# Health windows (section 7.7), in seconds before scaling.
FAILING_SPAN_S = 600
AUTO_DISABLE_AGE_S = 24 * 3600
AUTO_DISABLE_MIN_FAILURES = 10
SSRF_DISABLE_RUN = 3
SLOW_P50_MS = 5000
SLOW_SAMPLES = 20
SLOW_TIMEOUTS = 3
SLOW_WINDOW_S = 600
SLOW_LANE_S = 1800


def jitter_permille(seed: str, attempt: int) -> int:
    """A uniform factor in [0.8, 1.2] as permille: 800 + floor(u32 * 401 / 2^32), u32 = the first 4 bytes of
    sha256(utf8(seed + ":" + attempt)). Seeded with the delivery id, so a schedule is reproducible."""
    u32 = int.from_bytes(
        hashlib.sha256(f"{seed}:{attempt}".encode()).digest()[:4], "big"
    )
    return 800 + (u32 * 401) // 2**32


def parse_retry_after(value: str | None, now_ms: int) -> int | None:
    """Seconds from integer seconds or an HTTP-date; None when absent, unusable or in the past."""
    if value is None:
        return None
    v = value.strip()
    if re.fullmatch(r"[0-9]{1,10}", v):
        return int(v)
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None or dt.tzinfo is None:
        return None
    secs = int(dt.timestamp()) - now_ms // 1000
    return secs if secs >= 0 else None


def classify_answer(answer: Mapping[str, Any], now_ms: int) -> dict:
    """One attempt's answer, ``{status, retry_after?}`` or ``{error}``, as ``{outcome, class, retry_after_s}``."""
    if "error" in answer:
        if answer["error"] not in NON_HTTP_OUTCOMES:
            raise ValueError(f"unknown attempt error {answer['error']!r}")
        return {"outcome": answer["error"], "class": "standard", "retry_after_s": None}
    status = int(answer["status"])
    ra = parse_retry_after(answer.get("retry_after"), now_ms)
    if 200 <= status <= 299:
        return {"outcome": "success", "class": None, "retry_after_s": None}
    if status == 410:
        return {"outcome": "gone", "class": None, "retry_after_s": None}
    if status == 413:
        return {"outcome": "rejected", "class": None, "retry_after_s": None}
    if status == 429 or (status == 503 and ra is not None):
        return {"outcome": "rate_limited", "class": "standard", "retry_after_s": ra}
    if status in REJECTED_STATUSES:
        return {"outcome": "rejected", "class": "rejected", "retry_after_s": None}
    if 300 <= status <= 399:
        return {
            "outcome": "redirect_refused",
            "class": "standard",
            "retry_after_s": None,
        }
    return {"outcome": "http_error", "class": "standard", "retry_after_s": None}


def next_action(
    delivery: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    scale: float = 1.0,
) -> dict:
    """What happens after the last attempt settled. ``delivery`` holds ``jitter_seed`` (the delivery id),
    ``created_at_ms`` and ``max_age_s``; each attempt holds ``settled_at_ms`` and its answer. The class follows the
    LAST answer; max attempts is checked before max age; delays count from the attempt's settle time."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    last = attempts[-1]
    n = len(attempts)
    now_ms = int(last["settled_at_ms"])
    c = classify_answer(last, now_ms)
    out: dict = {"outcome": c["outcome"]}
    if c["outcome"] == "success":
        out.update(action="done", delivery_status="succeeded")
        return out
    if c["outcome"] == "gone":
        out.update(
            action="disable_endpoint",
            disabled_reason="gone_410",
            delivery_status="paused",
        )
        return out
    if c["outcome"] == "rejected" and c["class"] is None:
        out.update(
            action="give_up", delivery_status="failed", final_outcome="not_retried_413"
        )
        return out
    klass = c["class"]
    out["retry_class"] = klass
    if c["outcome"] == "rate_limited" and last.get("status") == 429:
        out["rate_halved_until_ms"] = now_ms + _ms(RATE_HALVE_S, scale)
    if n >= MAX_ATTEMPTS[klass]:
        out.update(
            action="give_up", delivery_status="failed", final_outcome="max_attempts"
        )
        return out
    nxt = n + 1
    base_s = (STANDARD_DELAYS_S if klass == "standard" else REJECTED_DELAYS_S)[nxt]
    permille = jitter_permille(str(delivery["jitter_seed"]), nxt)
    delay_ms = _scaled(base_s * permille, scale)
    if c["retry_after_s"] is not None:
        delay_ms = max(delay_ms, _ms(min(c["retry_after_s"], RETRY_AFTER_CAP_S), scale))
    retry_at = now_ms + delay_ms
    if retry_at > int(delivery["created_at_ms"]) + _ms(
        int(delivery["max_age_s"]), scale
    ):
        out.update(
            action="give_up", delivery_status="failed", final_outcome="max_age_exceeded"
        )
        return out
    out.update(
        action="retry",
        delivery_status="retrying",
        next_attempt=nxt,
        base_delay_s=base_s,
        jitter_permille=permille,
        delay_ms=delay_ms,
        retry_at_ms=retry_at,
    )
    return out


def endpoint_health(
    attempts: Sequence[Mapping[str, Any]], now_ms: int, scale: float = 1.0
) -> dict:
    """Endpoint rules over its attempt history, oldest first (section 7.7). ``worker_lost``, ``superseded`` and test
    attempts are ignored; a success resets the run of consecutive failures."""
    hist = [
        a
        for a in attempts
        if not a.get("test") and a["outcome"] not in ("worker_lost", "superseded")
    ]
    run: list[Mapping[str, Any]] = []
    for a in hist:
        if a["outcome"] == "success":
            run = []
        elif a["outcome"] in FAILED_FOR_HEALTH:
            run.append(a)
    disable = None
    if run and run[-1]["outcome"] == "gone":
        disable = "gone_410"
    elif len(run) >= SSRF_DISABLE_RUN and all(
        a["outcome"] == "ssrf_blocked" for a in run[-SSRF_DISABLE_RUN:]
    ):
        disable = "unsafe_address"
    elif len(run) >= AUTO_DISABLE_MIN_FAILURES and now_ms - run[0]["at_ms"] >= _ms(
        AUTO_DISABLE_AGE_S, scale
    ):
        disable = "failing"
    lat = [a["duration_ms"] for a in hist if "duration_ms" in a][-SLOW_SAMPLES:]
    p50 = sorted(lat)[SLOW_SAMPLES // 2 - 1] if len(lat) == SLOW_SAMPLES else None
    timeouts = [
        a
        for a in hist
        if a["outcome"] == "timeout"
        and now_ms - a["at_ms"] <= _ms(SLOW_WINDOW_S, scale)
    ]
    slow = (p50 is not None and p50 > SLOW_P50_MS) or len(timeouts) >= SLOW_TIMEOUTS
    return {
        "consecutive_failed_attempts": len(run),
        "disable_endpoint": disable,
        "failing": len(run) >= 3
        and run[-1]["at_ms"] - run[0]["at_ms"] >= _ms(FAILING_SPAN_S, scale),
        "slow_lane": slow,
        "slow_until_ms": now_ms + _ms(SLOW_LANE_S, scale) if slow else None,
    }


def lease_ms(timeout_ms: int) -> int:
    """``lease_until = now + 2 x (timeout_ms + 5 s)`` (section 13.1, R2). The attempt deadline is never scaled."""
    return 2 * (int(timeout_ms) + 5000)


def _ms(seconds: float, scale: float) -> int:
    return _scaled(int(seconds) * 1000, scale)


def _scaled(ms: int, scale: float) -> int:
    return ms if scale == 1.0 else max(1, int(ms * scale))
