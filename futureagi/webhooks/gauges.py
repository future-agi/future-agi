"""The SQL-derived gauges of DESIGN.md section 13.2, computed by the health pass every minute (and the inputs of the
ops alerts, section 13.3). Each gauge is one aggregate query; the attempt windows use the ``att_settled`` index.

``scale`` is FI_WEBHOOKS_TIME_SCALE as the adapter decided it; it scales the windows (1, 5 and 10 minutes) only.

- **Held by the endpoint's own limits.** WA1 means "sending is behind", so its age (``oldest_due_seconds``) leaves out
  the due deliveries of an endpoint the dispatcher kept at its own limit over the last minute: as many attempts as
  80 % of its ``rate_limit_per_s`` (halved after a 429) allows, or attempts busy for 80 % of ``max_in_flight`` x 60 s.
  A 1/s endpoint that receives a 500-event burst drains for 8 minutes at its own rate; that is the customer's limit,
  not a dispatcher that is behind. The window is real time (rates are), never scaled. The per-lane
  ``webhooks.backlog.due`` / ``oldest_due_seconds`` gauges still count every due delivery, and
  ``backlog_held_by_endpoint_limits`` counts the ones left out.
- **Fan-out lag** counts from when an event became due: its commit, or its deliberate ``not_before`` (B8 waits 5 s by
  contract, section 7.2), so WA3's 10 s are never half spent by design.
- **Dispatcher beats** are kept per loop: WA2 checks each of ``webhooks-dispatcher-1..WEBHOOKS_DISPATCHER_LOOPS``, so
  one of two hosted loops down is seen; beats of loops past the configured count are ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import connection

from webhooks import beats, config
from webhooks.core.ratelimit import MIN_RATE
from webhooks.core.schedule import _ms

NET_ERRORS = ("dns_error", "connect_error", "tls_error")

LANE_SQL = (
    "CASE WHEN s.lane = 'slow' AND s.slow_until > %(now)s THEN 'slow' ELSE 'normal' END"
)

HELD_WINDOW_S = 60
HELD_SHARE = 0.8

BACKLOG_SQL = f"""
WITH sent AS (
  SELECT d.endpoint_id, count(*) AS n, coalesce(sum(a.duration_ms), 0) AS busy_ms
  FROM webhooks_attempt a JOIN webhooks_delivery d ON d.id = a.delivery_id
  WHERE a.settled_at >= %(held_since)s AND a.outcome NOT IN ('superseded', 'worker_lost')
  GROUP BY 1
),
held AS (
  SELECT x.endpoint_id
  FROM sent x
  JOIN webhooks_endpoint e ON e.id = x.endpoint_id
  LEFT JOIN webhooks_endpoint_state s ON s.endpoint_id = x.endpoint_id
  WHERE x.n >= %(share)s * %(window_s)s * CASE WHEN s.rate_halved_until > %(now)s
          THEN GREATEST(e.rate_limit_per_s / 2.0, %(min_rate)s) ELSE e.rate_limit_per_s END
     OR x.busy_ms >= %(share)s * %(window_s)s * 1000 * e.max_in_flight
)
SELECT {LANE_SQL} AS lane, count(*),
       EXTRACT(EPOCH FROM (%(now)s - min(d.next_attempt_at))),
       count(*) FILTER (WHERE h.endpoint_id IS NOT NULL),
       EXTRACT(EPOCH FROM (%(now)s - min(d.next_attempt_at) FILTER (WHERE h.endpoint_id IS NULL)))
FROM webhooks_delivery d
JOIN webhooks_endpoint e ON e.id = d.endpoint_id
LEFT JOIN webhooks_endpoint_state s ON s.endpoint_id = d.endpoint_id
LEFT JOIN held h ON h.endpoint_id = d.endpoint_id
WHERE d.status IN ('pending', 'retrying') AND d.next_attempt_at <= %(now)s
  AND e.status = 'enabled' AND NOT e.deleted
GROUP BY 1
"""

# Postgres GREATEST ignores a null not_before.
OUTBOX_SQL = """
SELECT count(*), EXTRACT(EPOCH FROM (%(now)s - min(GREATEST(created_at, not_before))))
FROM webhooks_event
WHERE fanout_state = 'pending' AND (not_before IS NULL OR not_before <= %(now)s)
"""

FANOUT_LAG_SQL = """
SELECT percentile_cont(0.95) WITHIN GROUP (
  ORDER BY EXTRACT(EPOCH FROM (fanned_out_at - GREATEST(created_at, not_before))))
FROM webhooks_event WHERE fanned_out_at >= %(since)s AND fanout_state = 'done'
"""

# ``fanned_out_at`` is when fan-out ended, done or failed (``evt_fanned_out``, so neither window scans the table).
FANOUT_FAILED_SQL = """
SELECT count(*) FROM webhooks_event WHERE fanned_out_at >= %(since)s AND fanout_state = 'failed'
"""

ATTEMPTS_SQL = """
SELECT count(*),
       count(*) FILTER (WHERE a.outcome = ANY(%(net)s)),
       count(DISTINCT d.endpoint_id) FILTER (WHERE a.outcome = ANY(%(net)s))
FROM webhooks_attempt a JOIN webhooks_delivery d ON d.id = a.delivery_id
WHERE a.settled_at >= %(since)s AND a.outcome NOT IN ('superseded', 'worker_lost') AND a.trigger = 'auto'
"""

SSRF_SQL = """
SELECT organization_id::text, count(*) FROM webhooks_attempt
WHERE settled_at >= %(since)s AND outcome = 'ssrf_blocked'
GROUP BY 1
"""

SUPERSEDED_SQL = """
SELECT count(*) FROM webhooks_attempt WHERE settled_at >= %(since)s AND outcome = 'superseded'
"""

ENDPOINTS_SQL = """
SELECT status, kind, count(*) FROM webhooks_endpoint WHERE NOT deleted GROUP BY 1, 2
"""

OVERLAP_SQL = """
SELECT count(*) FROM webhooks_secret WHERE state = 'previous' AND expires_at > %(now)s
"""


def _window(now: datetime, seconds: int, scale: float) -> datetime:
    return now - timedelta(milliseconds=_ms(seconds, scale))


def snapshot(now: datetime, scale: float = 1.0) -> dict:
    """Every gauge, as plain JSON values."""
    from webhooks.models import DispatcherBeat

    out: dict = {"computed_at": now.isoformat()}
    with connection.cursor() as cur:
        cur.execute(
            BACKLOG_SQL,
            {
                "now": now,
                "held_since": now - timedelta(seconds=HELD_WINDOW_S),
                "share": HELD_SHARE,
                "window_s": HELD_WINDOW_S,
                "min_rate": MIN_RATE,
            },
        )
        due = {"normal": 0, "slow": 0}
        oldest = {"normal": None, "slow": None}
        oldest_ours: list[float] = []
        held = 0
        for lane, n, age, n_held, age_ours in cur.fetchall():
            due[lane], oldest[lane] = int(n), float(age) if age is not None else None
            held += int(n_held)
            if age_ours is not None:
                oldest_ours.append(float(age_ours))
        out["backlog_due"] = due
        out["backlog_oldest_due_seconds"] = oldest
        out["backlog_held_by_endpoint_limits"] = held
        # Events not fanned out yet are due work too: with every loop down nothing is fanned out (WA1, WA2).
        cur.execute(OUTBOX_SQL, {"now": now})
        pending, pending_age = cur.fetchone()
        out["outbox_pending"] = int(pending)
        out["outbox_oldest_seconds"] = (
            None if pending_age is None else float(pending_age)
        )
        out["due"] = sum(due.values()) + out["outbox_pending"]
        # WA1's input: due work the dispatcher could send now (not held by an endpoint's own limits), and the outbox.
        outbox_age = out["outbox_oldest_seconds"]
        ages = oldest_ours + ([outbox_age] if outbox_age is not None else [])
        out["oldest_due_seconds"] = max(ages) if ages else None

        cur.execute(FANOUT_LAG_SQL, {"since": _window(now, 600, scale)})
        p95 = cur.fetchone()[0]
        out["fanout_lag_p95_seconds"] = None if p95 is None else float(p95)
        cur.execute(FANOUT_FAILED_SQL, {"since": _window(now, 600, scale)})
        out["fanout_failed_10m"] = cur.fetchone()[0]

        cur.execute(
            ATTEMPTS_SQL, {"since": _window(now, 300, scale), "net": list(NET_ERRORS)}
        )
        total, net, eps = cur.fetchone()
        out.update(attempts_5m=total, net_errors_5m=net, net_error_endpoints_5m=eps)

        cur.execute(SSRF_SQL, {"since": _window(now, 60, scale)})
        out["ssrf_blocked_1m_by_org"] = dict(cur.fetchall())

        cur.execute(SUPERSEDED_SQL, {"since": _window(now, 600, scale)})
        out["superseded_10m"] = cur.fetchone()[0]

        cur.execute(ENDPOINTS_SQL)
        out["endpoints"] = [
            {"status": s, "kind": k, "count": n} for s, k, n in cur.fetchall()
        ]

        cur.execute(OVERLAP_SQL, {"now": now})
        out["secrets_in_overlap"] = cur.fetchone()[0]

    configured = beats.loop_ids(config.dispatcher_loops())
    rows = {
        b.loop: b
        for b in DispatcherBeat.objects.filter(loop__in=configured).order_by("loop")
    }
    beat_ages = {
        loop: None
        if loop not in rows
        else max(0.0, (now - rows[loop].beat_at).total_seconds())
        for loop in configured
    }
    out["loop_beat_age_seconds"] = beat_ages
    # The stalest configured loop (None while one has never beaten).
    out["last_beat_age_seconds"] = (
        None if None in beat_ages.values() else max(beat_ages.values(), default=None)
    )
    live = _window(now, 120, scale)
    out["slots_busy"] = [
        {"loop": b.loop, "normal": b.busy_normal, "slow": b.busy_slow}
        for b in rows.values()
        if b.beat_at >= live
    ]
    return out
