"""Retry schedule, answer classes and endpoint health (DESIGN.md sections 7.2, 7.3 and 7.7)."""

from __future__ import annotations

import hashlib
import re
from email.utils import parsedate_to_datetime

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

REJECTED_STATUSES = {400, 401, 403, 404, 405, 415, 422}

RETRY_AFTER_CAP_S = 3600

RATE_HALVE_S = 300

NON_HTTP_OUTCOMES = {
    "timeout",
    "dns_error",
    "connect_error",
    "tls_error",
    "ssrf_blocked",
    "worker_lost",
}


def jitter_permille(seed: str, attempt: int) -> int:
    """Uniform factor in [0.8, 1.2] as an integer permille in [800, 1200], from the first 4 bytes of
    sha256(utf8(seed + ":" + attempt)) read big-endian: 800 + floor(u32 * 401 / 2**32)."""
    u32 = int.from_bytes(
        hashlib.sha256(f"{seed}:{attempt}".encode()).digest()[:4], "big"
    )
    return 800 + (u32 * 401) // 2**32


def parse_retry_after(value: str | None, now_ms: int) -> int | None:
    """Seconds, or None when absent or unusable. Integer seconds or an HTTP-date (RFC 9110)."""
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


def classify_answer(answer: dict, now_ms: int) -> dict:
    """One attempt's answer -> {outcome, class, retry_after_s}. ``answer`` is {status, retry_after?} or {error}."""
    if "error" in answer:
        if answer["error"] not in NON_HTTP_OUTCOMES:
            raise ValueError(answer["error"])
        return {"outcome": answer["error"], "class": "standard", "retry_after_s": None}
    status = answer["status"]
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


def next_action(case: dict) -> dict:
    """The delivery's next action after attempt ``n`` (the last entry of ``attempts``) settled at ``now_ms``."""
    d = case["delivery"]
    attempts = case["attempts"]
    last = attempts[-1]
    n = len(attempts)
    now_ms = last["settled_at_ms"]
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
        out["rate_halved_until_ms"] = now_ms + RATE_HALVE_S * 1000
    if n >= MAX_ATTEMPTS[klass]:
        out.update(
            action="give_up", delivery_status="failed", final_outcome="max_attempts"
        )
        return out
    nxt = n + 1
    base_s = (STANDARD_DELAYS_S if klass == "standard" else REJECTED_DELAYS_S)[nxt]
    permille = jitter_permille(d["jitter_seed"], nxt)
    delay_ms = base_s * permille
    if c["retry_after_s"] is not None:
        delay_ms = max(delay_ms, min(c["retry_after_s"], RETRY_AFTER_CAP_S) * 1000)
    retry_at = now_ms + delay_ms
    if retry_at > d["created_at_ms"] + d["max_age_s"] * 1000:
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


FAILED_FOR_HEALTH = {
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


def endpoint_health(case: dict) -> dict:
    """Endpoint-level rules over the endpoint's attempt history (oldest first), evaluated at ``now_ms``."""
    now = case["now_ms"]
    hist = [
        a
        for a in case["attempts"]
        if not a.get("test") and a["outcome"] not in ("worker_lost", "superseded")
    ]
    run: list[dict] = []
    for a in hist:
        if a["outcome"] == "success":
            run = []
        elif a["outcome"] in FAILED_FOR_HEALTH:
            run.append(a)
    out: dict = {"consecutive_failed_attempts": len(run)}
    disable = None
    if run and run[-1]["outcome"] == "gone":
        disable = "gone_410"
    elif len(run) >= 3 and all(a["outcome"] == "ssrf_blocked" for a in run[-3:]):
        disable = "unsafe_address"
    elif len(run) >= 10 and now - run[0]["at_ms"] >= 24 * 3600 * 1000:
        disable = "failing"
    out["disable_endpoint"] = disable
    out["failing"] = len(run) >= 3 and run[-1]["at_ms"] - run[0]["at_ms"] >= 600_000
    lat = [a["duration_ms"] for a in hist if "duration_ms" in a][-20:]
    p50 = sorted(lat)[9] if len(lat) == 20 else None
    timeouts = [
        a for a in hist if a["outcome"] == "timeout" and now - a["at_ms"] <= 600_000
    ]
    slow = (p50 is not None and p50 > 5000) or len(timeouts) >= 3
    out["slow_lane"] = slow
    out["slow_until_ms"] = now + 1800 * 1000 if slow else None
    return out
