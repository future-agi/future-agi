"""
ClickHouse Consistency Monitoring

Monitors PG-CH data consistency, CDC replication lag, and query performance.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from django.conf import settings
from django.db import connection as pg_connection

from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)

logger = structlog.get_logger(__name__)


@dataclass
class ConsistencyResult:
    """Result of a consistency check between PG and CH."""

    table: str
    pg_count: int
    ch_count: int
    difference: int
    difference_pct: float
    is_consistent: bool  # True if difference < threshold
    checked_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class HealthStatus:
    """Overall health status of the ClickHouse analytics backend."""

    status: str  # "healthy", "degraded", "unhealthy"
    clickhouse_connected: bool
    cdc_lag: dict[str, float]  # table -> lag_seconds
    last_consistency_check: dict | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ConsistencyChecker:
    """Checks data consistency between PostgreSQL and ClickHouse."""

    # CH25 close-out (2026-05-28): `tracer_observation_span` removed —
    # spans live in the v2 typed-JSON `spans` table populated by
    # fi-collector via OTLP. No CDC mirror means no PG↔CH consistency
    # check is meaningful for spans.
    MONITORED_TABLES = [
        ("tracer_trace", "tracer_trace"),
        ("trace_session", "trace_session"),
        ("tracer_eval_logger", "tracer_eval_logger"),
    ]

    def __init__(self):
        self._ch_client = get_clickhouse_client()

    def check_row_counts(
        self,
        project_id: str,
        start_date: datetime,
        end_date: datetime,
        threshold_pct: float = 1.0,
    ) -> list[ConsistencyResult]:
        """Compare row counts between PG and CH for each table."""
        results = []
        for pg_table, ch_table in self.MONITORED_TABLES:
            try:
                # PG count
                with pg_connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {pg_table} WHERE project_id = %s AND created_at >= %s AND created_at <= %s",
                        [project_id, start_date, end_date],
                    )
                    pg_count = cursor.fetchone()[0]

                # CH count (with FINAL for deduplication)
                ch_result = self._ch_client.execute(
                    f"SELECT count() FROM {ch_table} FINAL WHERE project_id = %(project_id)s AND created_at >= %(start)s AND created_at <= %(end)s AND _peerdb_is_deleted = 0",
                    {"project_id": project_id, "start": start_date, "end": end_date},
                )
                ch_count = ch_result[0][0]

                diff = abs(pg_count - ch_count)
                diff_pct = (diff / max(pg_count, 1)) * 100

                results.append(
                    ConsistencyResult(
                        table=pg_table,
                        pg_count=pg_count,
                        ch_count=ch_count,
                        difference=diff,
                        difference_pct=diff_pct,
                        is_consistent=diff_pct <= threshold_pct,
                    )
                )
            except Exception as e:
                logger.error("Consistency check failed", table=pg_table, error=str(e))
                results.append(
                    ConsistencyResult(
                        table=pg_table,
                        pg_count=-1,
                        ch_count=-1,
                        difference=-1,
                        difference_pct=-1,
                        is_consistent=False,
                    )
                )
        return results

    def get_cdc_lag(self) -> dict[str, float]:
        """Get CDC replication lag per table in seconds."""
        lag = {}
        tables = [
            "tracer_trace",
            "trace_session",
            "tracer_eval_logger",
        ]
        for table in tables:
            try:
                result = self._ch_client.execute(
                    f"SELECT max(_peerdb_synced_at) FROM {table}"
                )
                if result and result[0][0]:
                    last_sync = result[0][0]
                    if isinstance(last_sync, datetime):
                        lag[table] = (datetime.utcnow() - last_sync).total_seconds()
                    else:
                        lag[table] = -1
                else:
                    lag[table] = -1  # No data
            except Exception as e:
                logger.warning("CDC lag check failed", table=table, error=str(e))
                lag[table] = -1
        return lag

    def get_health_status(self) -> HealthStatus:
        """Get overall health status."""
        if not is_clickhouse_enabled():
            return HealthStatus(
                status="disabled",
                clickhouse_connected=False,
                cdc_lag={},
            )

        connected = self._ch_client.ping()
        cdc_lag = self.get_cdc_lag() if connected else {}

        # Determine status
        if not connected:
            status = "unhealthy"
        elif any(v > 60 for v in cdc_lag.values() if v > 0):
            status = "degraded"
        else:
            status = "healthy"

        return HealthStatus(
            status=status,
            clickhouse_connected=connected,
            cdc_lag=cdc_lag,
            details={
                "routing": {
                    k: v
                    for k, v in settings.CLICKHOUSE.items()
                    if k.startswith("CH_ROUTE_")
                }
            },
        )
