"""Monitor declared ClickHouse tables, CDC lag and PG/CH consistency."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from django.conf import settings
from django.db import connection as pg_connection

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.schema import (
    get_all_schema_ddl,
    should_drop_legacy_chain,
)
from tracer.services.clickhouse.v2.query_service import get_v2_query_client
from tracer.services.clickhouse.v2.shadow import RoutingMode, get_routing_mode

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

    status: str  # "healthy", "degraded", "unhealthy", "disabled"
    clickhouse_connected: bool
    cdc_lag: dict[str, float]  # Only required CDC tables; -1 means no lag sample.
    last_consistency_check: dict | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Table:
    connection: str
    name: str
    pg_table: str
    cdc: bool = True

    @property
    def key(self) -> str:
        return f"{self.connection}.{self.name}"


class ConsistencyChecker:
    """Check declared requirements, never infer optionality from missing metadata.

    The schema registry declares the retained legacy mirrors. Native read routing
    additionally requires CH25 readiness, on its own connection. The eval source
    is independently selected: retiring trace CDC does not retire eval CDC.
    """

    MONITORED_TABLES = [
        ("tracer_trace", "tracer_trace"),
        ("trace_session", "trace_session"),
        ("tracer_eval_logger", "tracer_eval_logger"),
    ]

    def __init__(self):
        self._ch_client = get_clickhouse_client()

    def _tables(self) -> list[_Table]:
        declared = {name for name, _ in get_all_schema_ddl()}
        native = should_drop_legacy_chain() or any(
            get_routing_mode(query_type)
            in (RoutingMode.V2_ONLY, RoutingMode.V2_PRIMARY, RoutingMode.SHADOW)
            for query_type in (
                "TRACE_LIST",
                "SESSION_LIST",
                "SPAN_LIST",
                "EVAL_METRICS",
                "VOICE_CALL_LIST",
                "DASHBOARD",
                "MONITOR_METRICS",
                "FILTER_BUILDER",
                "TRACE_DETAIL",
            )
        )
        tables = [
            _Table("legacy", ch_table, pg_table)
            for pg_table, ch_table in self.MONITORED_TABLES
            if ch_table in declared
        ]
        if native:
            tables.extend(
                [
                    _Table("native", "traces", "tracer_trace", cdc=False),
                    _Table("native", "trace_sessions", "trace_session", cdc=False),
                    _Table("native", "spans", "tracer_observation_span", cdc=False),
                ]
            )
            eval_table, _ = eval_logger_source()
            # After retirement no monitored read needs the legacy connection.
            # During a mixed rollout both destinations must remain ready.
            if should_drop_legacy_chain():
                tables = [
                    table for table in tables if table.pg_table != "tracer_eval_logger"
                ]
            tables.append(
                _Table(
                    "native",
                    eval_table,
                    "tracer_eval_logger",
                    cdc=eval_table == "tracer_eval_logger",
                )
            )
        return tables

    def _inspect(
        self, tables: list[_Table], *, row_counts: bool = False
    ) -> tuple[dict[str, ClickHouseClient], dict[str, str], bool]:
        """Return clients, per-table readiness and aggregate connectivity.

        Query system metadata before issuing data probes. An empty or inaccessible
        inventory is a required-check failure, not evidence that CDC is optional.
        Reasons stay internal; the public endpoint retains its existing shape.
        """
        clients = {}
        checks = {}
        connected = True
        for role in dict.fromkeys(table.connection for table in tables):
            client = self._ch_client if role == "legacy" else get_v2_query_client()
            clients[role] = client
            selected = [table for table in tables if table.connection == role]
            try:
                if not client.ping():
                    connected = False
                    checks.update({table.key: "unavailable" for table in selected})
                    continue
                rows = client.execute(
                    "SELECT table, name FROM system.columns "
                    "WHERE database = currentDatabase() AND table IN %(tables)s",
                    {"tables": tuple(table.name for table in selected)},
                )
                columns: dict[str, set[str]] = {}
                for table_name, column in rows:
                    columns.setdefault(table_name, set()).add(column)
                for table in selected:
                    if table.cdc:
                        required = (
                            {"project_id", "created_at", "_peerdb_is_deleted"}
                            if row_counts
                            else {"_peerdb_synced_at"}
                        )
                    elif table.name == "trace_sessions":
                        required = {
                            "trace_session_id",
                            "project_id",
                            "is_deleted",
                            "version",
                        }
                    elif table.name == "tracer_eval_logger_v2":
                        # Native evals resolve their project through the target
                        # trace/span/session; the packaged DDL has no project_id.
                        required = {
                            "id",
                            "trace_id",
                            "observation_span_id",
                            "trace_session_id",
                            "custom_eval_config_id",
                            "is_deleted",
                            "_version",
                        }
                    else:
                        required = {"id", "project_id", "is_deleted", "_version"}
                    actual = columns.get(table.name)
                    if not actual:
                        checks[table.key] = "missing_required_table"
                    elif not required <= actual:
                        checks[table.key] = "missing_required_columns"
                    else:
                        checks[table.key] = "ready"
            except Exception as exc:
                logger.warning(
                    "ClickHouse schema check failed",
                    connection=role,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                checks.update({table.key: "unavailable" for table in selected})
        return clients, checks, connected

    def check_row_counts(
        self,
        project_id: str,
        start_date: datetime,
        end_date: datetime,
        threshold_pct: float = 1.0,
    ) -> list[ConsistencyResult]:
        """Compare only declared CDC mirrors; native writes are not PG mirrors."""
        tables = [table for table in self._tables() if table.cdc]
        clients, checks, _ = self._inspect(tables, row_counts=True)
        results = []
        for table in tables:
            pg_count = ch_count = diff = diff_pct = -1
            consistent = False
            if checks[table.key] == "ready":
                try:
                    with pg_connection.cursor() as cursor:
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {table.pg_table} WHERE project_id = %s AND created_at >= %s AND created_at <= %s",
                            [project_id, start_date, end_date],
                        )
                        pg_count = cursor.fetchone()[0]
                    ch_result = clients[table.connection].execute(
                        f"SELECT count() FROM {table.name} FINAL WHERE project_id = %(project_id)s AND created_at >= %(start)s AND created_at <= %(end)s AND _peerdb_is_deleted = 0",
                        {
                            "project_id": project_id,
                            "start": start_date,
                            "end": end_date,
                        },
                    )
                    ch_count = ch_result[0][0]
                    diff = abs(pg_count - ch_count)
                    diff_pct = (diff / max(pg_count, 1)) * 100
                    consistent = diff_pct <= threshold_pct
                except Exception as exc:
                    logger.warning(
                        "Consistency check failed",
                        table=table.key,
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    pg_count = ch_count = diff = diff_pct = -1
            results.append(
                ConsistencyResult(
                    table.pg_table, pg_count, ch_count, diff, diff_pct, consistent
                )
            )
        return results

    def _lag(
        self,
        tables: list[_Table],
        clients: dict[str, ClickHouseClient],
        checks: dict[str, str],
    ) -> dict[str, float]:
        lag = {}
        for table in tables:
            if not table.cdc:
                continue
            value = -1.0
            if checks[table.key] == "ready":
                try:
                    # max() alone returns an epoch default on an empty table.
                    rows = clients[table.connection].execute(
                        f"SELECT count(), max(_peerdb_synced_at) FROM {table.name}"
                    )
                    if rows and rows[0][0] == 0:
                        checks[table.key] = "empty"
                    elif rows and isinstance(rows[0][1], datetime):
                        last_sync = rows[0][1]
                        if last_sync.tzinfo is None:
                            last_sync = last_sync.replace(tzinfo=UTC)
                        value = max(
                            0.0, (datetime.now(UTC) - last_sync).total_seconds()
                        )
                        if value > 60:
                            checks[table.key] = "lagging"
                    else:
                        checks[table.key] = "unavailable"
                except Exception as exc:
                    logger.warning(
                        "CDC lag check failed",
                        table=table.key,
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
                    checks[table.key] = "unavailable"
            # In mixed deployments the public per-table value represents both
            # destinations: an unavailable sample wins over a successful one.
            previous = lag.get(table.name, value)
            lag[table.name] = -1.0 if min(previous, value) < 0 else max(previous, value)
        return lag

    def get_cdc_lag(self) -> dict[str, float]:
        """Lag for declared CDC tables; omit native/retired tables, use -1 if unknown."""
        tables = [table for table in self._tables() if table.cdc]
        clients, checks, _ = self._inspect(tables)
        return self._lag(tables, clients, checks)

    def get_health_status(self) -> HealthStatus:
        if not is_clickhouse_enabled():
            return HealthStatus(
                status="disabled", clickhouse_connected=False, cdc_lag={}
            )

        tables = self._tables()
        clients, checks, connected = self._inspect(tables)
        cdc_lag = self._lag(tables, clients, checks)
        if (
            not connected
            or not tables
            or any(
                value not in ("ready", "empty", "lagging") for value in checks.values()
            )
        ):
            status = "unhealthy"
        elif "lagging" in checks.values():
            status = "degraded"
        else:
            status = "healthy"

        return HealthStatus(
            status=status,
            clickhouse_connected=connected,
            cdc_lag=cdc_lag,
            details={
                "checks": checks,
                "routing": {
                    k: v
                    for k, v in settings.CLICKHOUSE.items()
                    if k.startswith("CH_ROUTE_")
                },
            },
        )
