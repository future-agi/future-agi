"""
Replica lag metric exporter for the Postgres read replica.

LIMITATION: `pg_last_xact_replay_timestamp()` returns NULL on a standby that
hasn't replayed any transaction recently — this function returns 0 in that
case, which can LOOK healthy when the standby is actually behind. The metric
is a smoke test, not a freshness guarantee.

For higher fidelity:
  - Aurora: use the CloudWatch `ReplicaLag` metric
  - Self-hosted: scrape `pg_stat_replication.replay_lag` from the PRIMARY
    (requires a separate connection / metric)

Alert if the metric is unhealthy (>30s) OR if it stays at exactly 0 for an
unexpectedly long window (potential stalled standby).
"""

import logging

from django.db import connections
from prometheus_client import REGISTRY, Gauge

logger = logging.getLogger(__name__)


def _get_or_create_gauge(name: str, description: str) -> Gauge:
    """Idempotent Gauge registration.

    `prometheus_client.Gauge(...)` registers the metric in the default
    registry at construction time. A second import (test reload, duplicate
    module path, `importlib.reload()`) raises `ValueError: Duplicated
    timeseries`. Return the existing instance if already registered.
    """
    existing = REGISTRY._names_to_collectors.get(name)  # noqa: SLF001
    if existing is not None:
        return existing  # type: ignore[return-value]
    return Gauge(name, description)


pg_replication_lag_seconds = _get_or_create_gauge(
    "pg_replication_lag_seconds",
    "Replication lag on the PG read replica, in seconds. -1 if no replica or query failed.",
)


def sample_replica_lag() -> float:
    """
    Sample the replica's reported lag and update the Prometheus gauge.

    Returns the lag in seconds (>=0), or -1.0 on error / no replica configured.

    Schedule this every ~30s from your existing metrics worker (Temporal
    cron workflow or Celery beat task). Do NOT call from a request hot path.
    """
    if "replica" not in connections.databases:
        pg_replication_lag_seconds.set(-1)
        return -1.0
    try:
        with connections["replica"].cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))"
            )
            row = cur.fetchone()
            # NULL replay timestamp → no recently-replayed txn. We return 0
            # to keep the gauge numeric, but this is NOT a freshness
            # guarantee — see module docstring.
            lag = float(row[0]) if row and row[0] is not None else 0.0
        pg_replication_lag_seconds.set(lag)
        return lag
    except Exception:
        logger.exception("Failed to sample replica lag")
        pg_replication_lag_seconds.set(-1)
        return -1.0
