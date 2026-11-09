"""schedule.json (DESIGN.md sections 7.2, 7.3, 7.7 and 22.2)."""

from __future__ import annotations

from . import reference as R
from .common import CONFORMANCE_VERSION, dump


def schedule_file() -> bytes:
    T0 = 1792663200000
    cases = []

    def deliv(seed, created=T0, max_age_s=259200):
        return {"jitter_seed": seed, "created_at_ms": created, "max_age_s": max_age_s}

    def case(cid, d, answers, note, latency_ms=180):
        """answers are applied in order; each settles latency after it was due."""
        attempts = []
        due = d["created_at_ms"]
        for a in answers:
            at = dict(a)
            at["settled_at_ms"] = a.get("settled_at_ms", due + latency_ms)
            attempts.append(at)
            res = R.next_action({"delivery": d, "attempts": attempts})
            if res["action"] == "retry":
                due = res["retry_at_ms"]
        cases.append(
            {
                "id": cid,
                "delivery": d,
                "attempts": attempts,
                "expected": R.next_action({"delivery": d, "attempts": attempts}),
                "note": note,
            }
        )

    for n in range(1, 15):
        case(
            f"standard-500-x{n}",
            deliv("dlv_01JBF3QAAAAAAAAAAAAAAAAAAA"),
            [{"status": 500}] * n,
            f"{n} consecutive 500s; attempt {n + 1} follows the standard table"
            if n < 14
            else "the 14th failure: max_attempts",
        )
    for seed in (
        "dlv_01JBF3QBBBBBBBBBBBBBBBBBBB",
        "dlv_01JBF3QCCCCCCCCCCCCCCCCCCC",
        "dlv_01JBF3QDDDDDDDDDDDDDDDDDDD",
    ):
        case(
            f"standard-chain-{seed[-4:].lower()}",
            deliv(seed),
            [{"error": "timeout"}] * 14,
            "a full chain of timeouts with another jitter seed: max_attempts or max_age_exceeded, whichever comes "
            "first",
            latency_ms=15000,
        )
    for n in range(1, 6):
        case(
            f"rejected-400-x{n}",
            deliv("dlv_01JBF3QEEEEEEEEEEEEEEEEEEE"),
            [{"status": 400}] * n,
            "rejected class: attempts 2-5 after 5 s, 1 min, 10 min, 45 min; the 5th failure gives up",
        )
    case(
        "400-then-503",
        deliv("dlv_01JBF3QFFFFFFFFFFFFFFFFFFF"),
        [{"status": 400}, {"status": 503}],
        "the class follows the last answer: standard from now on (attempt 3 after 30 s)",
    )
    case(
        "503-then-400",
        deliv("dlv_01JBF3QFFFFFFFFFFFFFFFFFFF"),
        [{"status": 503}, {"status": 400}],
        "rejected after a 503: attempt 3 after 1 min",
    )
    case(
        "503-x6-then-400",
        deliv("dlv_01JBF3QFFFFFFFFFFFFFFFFFFF"),
        [{"status": 503}] * 6 + [{"status": 400}],
        "a rejected answer on attempt 7 is past the rejected class's 5 attempts: max_attempts",
    )
    for st in (401, 403, 404, 405, 415, 422):
        case(
            f"rejected-{st}",
            deliv("dlv_01JBF3QGGGGGGGGGGGGGGGGGGG"),
            [{"status": st}],
            "rejected class",
        )
    for st in (408, 409, 425, 418, 451, 500, 501, 502, 504, 599):
        case(
            f"standard-{st}",
            deliv("dlv_01JBF3QGGGGGGGGGGGGGGGGGGG"),
            [{"status": st}],
            "http_error, standard",
        )
    for st in (301, 302, 307, 308):
        case(
            f"redirect-{st}",
            deliv("dlv_01JBF3QGGGGGGGGGGGGGGGGGGG"),
            [{"status": st}],
            "redirects are never followed: redirect_refused, standard",
        )
    for e in sorted(R.NON_HTTP_OUTCOMES):
        case(
            f"error-{e}",
            deliv("dlv_01JBF3QGGGGGGGGGGGGGGGGGGG"),
            [{"error": e}],
            "no HTTP answer: standard",
        )
    for st in (200, 201, 204, 299):
        case(
            f"success-{st}",
            deliv("dlv_01JBF3QGGGGGGGGGGGGGGGGGGG"),
            [{"status": 500}, {"status": st}],
            "2xx: done",
        )
    case(
        "413",
        deliv("dlv_01JBF3QHHHHHHHHHHHHHHHHHHH"),
        [{"status": 413}],
        "never retried: not_retried_413",
    )
    case(
        "410",
        deliv("dlv_01JBF3QHHHHHHHHHHHHHHHHHHH"),
        [{"status": 500}, {"status": 410}],
        "410 turns the endpoint off at once; the delivery is paused",
    )
    case(
        "429-retry-after-7",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 429, "retry_after": "7"}],
        "max(jittered 5 s, 7 s); the endpoint's rate is halved for 5 min (WT-10)",
    )
    case(
        "429-retry-after-2",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 429, "retry_after": "2"}],
        "Retry-After below the schedule: the schedule wins",
    )
    case(
        "429-retry-after-7200",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 429, "retry_after": "7200"}],
        "Retry-After is capped at 1 h",
    )
    case(
        "429-no-retry-after",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 429}],
        "429 without Retry-After",
    )
    case(
        "429-retry-after-garbage",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 429, "retry_after": "soon"}],
        "an unusable Retry-After is ignored",
    )
    case(
        "503-retry-after-http-date",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [
            {
                "status": 503,
                "retry_after": "Thu, 22 Oct 2026 10:01:00 GMT",
                "settled_at_ms": T0 + 180,
            }
        ],
        "503 with an HTTP-date Retry-After 60 s ahead: rate_limited",
    )
    case(
        "503-no-retry-after",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 503}],
        "503 without Retry-After is http_error",
    )
    case(
        "500-retry-after-ignored",
        deliv("dlv_01JBF3QJJJJJJJJJJJJJJJJJJJ"),
        [{"status": 500, "retry_after": "900"}],
        "Retry-After counts only on 429 and 503",
    )
    case(
        "max-age-paused-4-days",
        deliv("dlv_01JBF3QKKKKKKKKKKKKKKKKKKK", created=T0 - 4 * 86400 * 1000),
        [{"status": 500, "settled_at_ms": T0}],
        "time in paused counts toward max age: created 4 days ago",
    )
    case(
        "max-age-1h-endpoint",
        deliv("dlv_01JBF3QMMMMMMMMMMMMMMMMMMM", max_age_s=3600),
        [{"status": 500}] * 6,
        "an endpoint with max_age_s 3600: attempt 7 would fall after 1 h",
    )
    case(
        "max-age-71h",
        deliv("dlv_01JBF3QNNNNNNNNNNNNNNNNNNN", created=T0 - 71 * 3600 * 1000),
        [{"status": 500, "settled_at_ms": T0}],
        "created 71 h ago: a 5 s retry still fits",
    )
    case(
        "max-age-71h59m58s",
        deliv("dlv_01JBF3QNNNNNNNNNNNNNNNNNNN", created=T0 - (72 * 3600 - 2) * 1000),
        [{"status": 500, "settled_at_ms": T0}],
        "2 s left: the jittered 5 s retry does not fit",
    )

    health = []

    def hcase(cid, attempts, now, note):
        c = {"attempts": attempts, "now_ms": now}
        health.append({"id": cid, **c, "expected": R.endpoint_health(c), "note": note})

    def fails(k, start, step_ms, outcome="http_error", **extra):
        return [
            {"outcome": outcome, "at_ms": start + i * step_ms, **extra}
            for i in range(k)
        ]

    H = 3600 * 1000
    hcase(
        "gone-410",
        [{"outcome": "success", "at_ms": T0}, {"outcome": "gone", "at_ms": T0 + 1000}],
        T0 + 2000,
        "410: off at once",
    )
    hcase(
        "ssrf-x3",
        fails(3, T0, 5000, "ssrf_blocked"),
        T0 + 20000,
        "3 consecutive ssrf_blocked: unsafe_address",
    )
    hcase(
        "ssrf-x2-timeout",
        fails(2, T0, 5000, "ssrf_blocked")
        + [{"outcome": "timeout", "at_ms": T0 + 20000}],
        T0 + 30000,
        "not 3 in a row",
    )
    hcase(
        "ssrf-success-ssrf-x2",
        fails(1, T0, 1, "ssrf_blocked")
        + [{"outcome": "success", "at_ms": T0 + 10}]
        + fails(2, T0 + 20, 10, "ssrf_blocked"),
        T0 + 100,
        "a 2xx resets the run",
    )
    hcase(
        "down-24h-10-attempts",
        fails(10, T0, 3 * H),
        T0 + 27 * H,
        "no 2xx for 24 h, >= 10 consecutive failures, the first >= 24 h old: failing",
    )
    hcase(
        "down-23h-10-attempts",
        fails(10, T0, 2 * H),
        T0 + 23 * H,
        "the first failure is only 23 h old",
    )
    hcase("down-30h-9-attempts", fails(9, T0, 3 * H), T0 + 30 * H, "only 9 failures")
    hcase(
        "down-6h",
        fails(9, T0, 40 * 60 * 1000),
        T0 + 6 * H,
        "WT-12: a receiver down for 6 h is not turned off",
    )
    hcase(
        "worker-lost-not-counted",
        fails(10, T0, 3 * H, "worker_lost"),
        T0 + 31 * H,
        "worker_lost is our failure, not the receiver's: it never counts",
    )
    hcase(
        "tests-not-counted",
        fails(10, T0, 3 * H, "http_error", test=True),
        T0 + 31 * H,
        "test deliveries do not change endpoint health",
    )
    hcase(
        "failing-3-over-10min",
        fails(3, T0, 5 * 60 * 1000),
        T0 + 11 * 60 * 1000,
        "3 consecutive failures spanning >= 10 min: Failing",
    )
    hcase(
        "failing-3-over-9min",
        fails(3, T0, 270 * 1000),
        T0 + 10 * 60 * 1000,
        "spanning 9 min: not yet",
    )
    slow20 = [
        {"outcome": "success", "at_ms": T0 + i * 1000, "duration_ms": 4000 + i * 200}
        for i in range(20)
    ]
    hcase(
        "slow-p50",
        slow20,
        T0 + 30000,
        "p50 of the last 20 latencies (nearest rank, the 10th smallest) > 5,000 ms",
    )
    hcase(
        "slow-19-samples",
        slow20[1:],
        T0 + 30000,
        "fewer than 20 samples: the p50 rule does not apply",
    )
    fast20 = [
        {"outcome": "success", "at_ms": T0 + i * 1000, "duration_ms": 4000 + i * 100}
        for i in range(20)
    ]
    hcase("not-slow-p50-4900", fast20, T0 + 30000, "p50 4,900 ms")
    hcase(
        "slow-3-timeouts-10min",
        fails(3, T0, 4 * 60 * 1000, "timeout"),
        T0 + 9 * 60 * 1000,
        ">= 3 timeouts in the last 10 min",
    )
    hcase(
        "not-slow-timeout-aged-out",
        fails(3, T0, 4 * 60 * 1000, "timeout"),
        T0 + 11 * 60 * 1000,
        "the first timeout is 11 min old",
    )

    rules = {
        "classes": {
            "standard_delays_s": R.STANDARD_DELAYS_S,
            "rejected_delays_s": R.REJECTED_DELAYS_S,
            "max_attempts": R.MAX_ATTEMPTS,
            "rejected_statuses": sorted(R.REJECTED_STATUSES),
        },
        "answer_classes": "2xx success; 410 gone (disable_endpoint gone_410, delivery paused); 413 give_up "
        "not_retried_413; 429, or 503 with a usable Retry-After: rate_limited, standard; "
        "400/401/403/404/405/415/422 rejected class; 3xx redirect_refused, standard; any other "
        "status http_error, standard; timeout/dns_error/connect_error/tls_error/ssrf_blocked/"
        "worker_lost standard. The class follows the LAST answer.",
        "jitter": "permille = 800 + floor(u32 * 401 / 2^32), u32 = first 4 bytes big-endian of "
        "sha256(utf8(jitter_seed + ':' + next_attempt_number)); delay_ms = base_delay_s * permille. "
        "Production seeds with the delivery id, so a delivery's schedule is reproducible and deliveries "
        "that failed together spread out.",
        "retry_after": "integer seconds or an HTTP-date, on 429 and 503 only; delay_ms = max(jittered delay, "
        "min(retry_after_s, 3600) * 1000); a 429 also sets rate_halved_until = now + 300 s",
        "max_attempts": "after attempt n fails, give up with max_attempts when n >= max_attempts[class] (checked "
        "before max age)",
        "max_age": "give up with max_age_exceeded when retry_at > created_at + max_age_s (time paused counts)",
        "time_base": "delays count from the attempt's settled_at_ms",
        "health": "attempts oldest first; worker_lost, superseded and test attempts are ignored; a success resets "
        "the consecutive-failure run. disable: last failure is 410 -> gone_410; the last 3 failures are "
        "ssrf_blocked -> unsafe_address; >= 10 consecutive failures whose first is >= 24 h before now -> "
        "failing. Failing state: >= 3 consecutive failures spanning >= 10 min (first to last). Slow lane: "
        "20 latency samples with the 10th smallest > 5,000 ms, or >= 3 timeouts with at_ms >= now - 10 min; "
        "slow_until = now + 30 min.",
    }
    return dump(
        {
            "conformance_version": CONFORMANCE_VERSION,
            "file": "schedule",
            "rules": rules,
            "next_action": cases,
            "endpoint_health": health,
        }
    )
