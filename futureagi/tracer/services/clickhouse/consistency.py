"""Monitor declared ClickHouse tables, CDC lag and PG/CH consistency."""

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import structlog
from django.conf import settings
from django.db import connection as pg_connection

from tracer.services.clickhouse.application_read_policy import application_read_context
from tracer.services.clickhouse.client import (
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.health_client import (
    HealthCheckClient,
    is_access_denied,
    is_timeout,
)
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
    cdc_lag: dict[str, float]  # Required CDC tables; -1 means unverified, even empty.
    last_consistency_check: dict | None = None
    details: dict[str, Any] = field(default_factory=dict)
    cdc_lag_verified: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class _Table:
    connection: str
    name: str
    pg_table: str
    cdc: bool = True

    @property
    def key(self) -> str:
        return f"{self.connection}.{self.name}"


@dataclass
class _Probe:
    """One destination's diagnostic connection, shared by a check's statements."""

    client: Any
    answered: bool = False

    @property
    def reachable(self) -> bool:
        # A completed native handshake or any answered statement proves the
        # server is up, however slow a later statement turns out to be.
        return self.answered or (
            getattr(self.client, "handshake_completed", False) is True
        )


@dataclass(frozen=True)
class _SampleRead:
    """A complete inventory of immutable MergeTree parts that fits the budget.

    Exact names exclude replacement merges/mutations and concurrent inserts.
    ``rows`` is the sum of physical part rows, not a SQL LIMIT. An inventory
    recheck and matching row count are required before reporting exact lag.
    """

    parts: tuple[tuple[str, int], ...]

    @property
    def rows(self) -> int:
        return sum(rows for _, rows in self.parts)


class _BudgetSpent(TimeoutError):
    """The check's shared wall ran out before this statement was sent."""


class _PartInventoryDenied(PermissionError):
    """The lag read succeeded but its metadata coverage check was denied."""


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

    # Diagnostics must not inherit the application's long-running read policy.
    # One wall bounds the whole check, so sequential statements cannot each
    # restart the endpoint's timeout. Each destination uses one connection: a
    # handshake and at most four statements (schema, inventory, lag, inventory
    # recheck), or schema and sizes without part grants. A statement
    # may use part of the wall: room for several round trips on a slow network
    # plus bounded server work, while a stalled one leaves time for the next
    # destination. A server that answered the handshake and then runs out of
    # time is slow (degraded), not disconnected.
    QUERY_TIMEOUT_MS = 2500
    CHECK_TIMEOUT_SECONDS = 5

    # The most rows the lag probe reads from one table. ``_peerdb_synced_at`` is
    # in no sorting or partition key, so a whole-table max() grows with the
    # table and cannot fit a short statement cap on large mirrors. A few million
    # rows can. A readonly=1 profile accepts no per-query limits, so this bound
    # is enforced by what the probe chooses to read, never by settings.
    LAG_SAMPLE_MAX_ROWS = 10_000_000
    LAG_SAMPLE_MAX_PARTS = 64
    # Without readable immutable part identities, every table has an actionable
    # metadata failure. Sizing is diagnostic only, never a safe data-read plan:
    # a table can grow immediately after sizing.
    PART_COLUMNS = ("database", "table", "name", "rows", "active")
    # Seconds since the latest sync above which a CDC mirror reads lagging.
    LAG_THRESHOLD_SECONDS = 60

    def __init__(self):
        self._ch_client = get_clickhouse_client()

    def _read(
        self,
        probe: _Probe,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        deadline: float,
    ) -> list[tuple]:
        remaining_ms = min(self.QUERY_TIMEOUT_MS, int((deadline - monotonic()) * 1000))
        if remaining_ms <= 0:
            raise _BudgetSpent("ClickHouse health check deadline exhausted")
        with application_read_context(enabled=False):
            rows, _, _ = probe.client.execute_read(
                query, params, timeout_ms=remaining_ms
            )
        probe.answered = True
        return rows

    def _open(self, role: str, probes: dict[str, _Probe]) -> _Probe:
        source = self._ch_client if role == "legacy" else get_v2_query_client()
        probe = probes[role] = _Probe(
            HealthCheckClient.from_client(
                source, timeout_ms=self.CHECK_TIMEOUT_SECONDS * 1000
            )
        )
        return probe

    @staticmethod
    def _close(probes: dict[str, _Probe]) -> None:
        # Never return a partial health response/socket to a production pool:
        # each probe owns its connection and closes it here.
        for probe in probes.values():
            with suppress(Exception):
                probe.client.close()

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
            # Retirement removes legacy table probes, not connection readiness:
            # monitor and dataset/dashboard readers still use that destination.
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
        self,
        tables: list[_Table],
        probes: dict[str, _Probe],
        *,
        deadline: float,
        row_counts: bool = False,
    ) -> tuple[dict[str, str], bool, set[str]]:
        """Return per-table readiness, connectivity and part visibility.

        Query system metadata before issuing data probes. An empty or inaccessible
        inventory is a required-check failure, not evidence that CDC is optional.
        Reasons stay internal; the public endpoint retains its existing shape.
        The last value names the connections whose role can read the
        ``system.parts`` columns the lag sample needs. ``probes`` receives each
        destination's connection; the caller closes them.
        """
        checks = {}
        connected = True
        part_metadata: set[str] = set()
        # Connection dependencies outlive the legacy trace-table inventory.
        for role in dict.fromkeys(["legacy", *(table.connection for table in tables)]):
            selected = [table for table in tables if table.connection == role]
            probe = None
            try:
                probe = self._open(role, probes)
                if not selected:
                    self._read(probe, "SELECT 1", deadline=deadline)
                    continue
                # system.columns lists only what this role may read, so the
                # same statement reports whether system.parts is usable. It is
                # also the destination's first round trip: no separate ping.
                rows = self._read(
                    probe,
                    "SELECT if(database = 'system', 'system.parts', table), name "
                    "FROM system.columns "
                    "WHERE (database = currentDatabase() AND table IN %(tables)s) "
                    "OR (database = 'system' AND table = 'parts' "
                    "AND name IN %(part_columns)s)",
                    {
                        "tables": tuple(table.name for table in selected),
                        "part_columns": self.PART_COLUMNS,
                    },
                    deadline=deadline,
                )
                columns: dict[str, set[str]] = {}
                for table_name, column in rows:
                    columns.setdefault(table_name, set()).add(column)
                if set(self.PART_COLUMNS) <= columns.get("system.parts", set()):
                    part_metadata.add(role)
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
                # Reachable, or never contacted because earlier destinations
                # spent the shared budget: slow, not down.
                slow = (probe is not None and probe.reachable) or isinstance(
                    exc, _BudgetSpent
                )
                if not slow:
                    connected = False
                state = "timeout" if slow and is_timeout(exc) else "unavailable"
                logger.warning(
                    "ClickHouse schema check failed",
                    connection=role,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                checks.update({table.key: state for table in selected})
                if not selected and slow:
                    checks[f"{role}.connection"] = state
        return checks, connected, part_metadata

    def check_row_counts(
        self,
        project_id: str,
        start_date: datetime,
        end_date: datetime,
        threshold_pct: float = 1.0,
    ) -> list[ConsistencyResult]:
        """Compare only declared CDC mirrors; native writes are not PG mirrors."""
        tables = [table for table in self._tables() if table.cdc]
        deadline = monotonic() + self.CHECK_TIMEOUT_SECONDS
        probes: dict[str, _Probe] = {}
        try:
            checks, _, _ = self._inspect(
                tables, probes, deadline=deadline, row_counts=True
            )
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
                        ch_result = self._read(
                            probes[table.connection],
                            f"SELECT count() FROM {table.name} FINAL WHERE project_id = %(project_id)s AND created_at >= %(start)s AND created_at <= %(end)s AND _peerdb_is_deleted = 0",
                            {
                                "project_id": project_id,
                                "start": start_date,
                                "end": end_date,
                            },
                            deadline=deadline,
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
        finally:
            self._close(probes)

    def _part_inventory(
        self, probe: _Probe, tables: list[_Table], *, deadline: float
    ) -> dict[str, list[tuple[str, int]]]:
        """Read a capped inventory; reaching the cap never proves completeness."""
        rows = self._read(
            probe,
            "SELECT table, name, rows FROM system.parts "
            "WHERE database = currentDatabase() AND active AND rows > 0 "
            "AND table IN %(tables)s ORDER BY table, name "
            "LIMIT %(parts)s BY table",
            {
                "tables": tuple(table.name for table in tables),
                "parts": int(self.LAG_SAMPLE_MAX_PARTS),
            },
            deadline=deadline,
        )
        parts: dict[str, list[tuple[str, int]]] = {table.key: [] for table in tables}
        by_name = {table.name: table.key for table in tables}
        for name, part, count in rows:
            if name in by_name:
                parts[by_name[name]].append((part, count))
        return parts

    def _plan(self, parts: list[tuple[str, int]]) -> _SampleRead | None:
        """Read all known parts or none; partial maxima are not exact lag."""
        if (
            len(parts) >= self.LAG_SAMPLE_MAX_PARTS
            or any(rows < 0 for _, rows in parts)
            or sum(rows for _, rows in parts) > self.LAG_SAMPLE_MAX_ROWS
        ):
            return None
        return _SampleRead(tuple(sorted(parts)))

    def _sample_plans(
        self,
        tables: list[_Table],
        probes: dict[str, _Probe],
        checks: dict[str, str],
        part_metadata: set[str],
        *,
        deadline: float,
    ) -> tuple[dict[str, _SampleRead], set[str]]:
        """Plan complete reads only, never infer sync freshness from part age."""
        selected: dict[str, list[_Table]] = {}
        for table in tables:
            if checks[table.key] == "ready" and table.connection in part_metadata:
                selected.setdefault(table.connection, []).append(table)
        plans = {}
        listed = set()
        for role, ready in selected.items():
            try:
                inventory = self._part_inventory(probes[role], ready, deadline=deadline)
            except Exception as exc:
                if is_access_denied(exc):
                    logger.warning("CDC lag part inventory denied", connection=role)
                    continue
                logger.warning(
                    "CDC lag check failed",
                    connection=role,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                checks.update(
                    {
                        table.key: "timeout" if is_timeout(exc) else "unavailable"
                        for table in ready
                    }
                )
                continue
            listed.add(role)
            for table in ready:
                plan = self._plan(inventory[table.key])
                if plan is None:
                    checks[table.key] = "unverified"
                else:
                    plans[table.key] = plan
        return plans, listed

    def _without_part_metadata(
        self,
        role: str,
        probe: _Probe,
        tables: list[_Table],
        checks: dict[str, str],
        *,
        deadline: float,
    ) -> None:
        """Preserve permission-related degradation without an unsafe fallback.

        Every size requires an actionable system.parts grant, including small
        or empty tables. Sizing is retained for diagnostics, not a data-read plan:
        total_rows cannot bound a later scan, and readonly=1 forbids compensating
        settings.
        """
        try:
            rows = self._read(
                probe,
                "SELECT name, total_rows FROM system.tables "
                "WHERE database = currentDatabase() AND name IN %(tables)s",
                {"tables": tuple(table.name for table in tables)},
                deadline=deadline,
            )
        except Exception as exc:
            if not is_access_denied(exc):
                raise
            rows = []
        for table in tables:
            checks[table.key] = "unsampled"
        if unsampled := [t.name for t in tables if checks[t.key] == "unsampled"]:
            logger.warning(
                "CDC lag unknown",
                connection=role,
                tables=unsampled,
                table_rows=dict(rows),
                max_rows=self.LAG_SAMPLE_MAX_ROWS,
                cause="no_part_metadata",
                required_grant=f"SELECT({', '.join(self.PART_COLUMNS)}) ON system.parts",
            )

    @staticmethod
    def _sample_source(
        table: str, plan: _SampleRead, index: int, params: dict[str, Any]
    ) -> str:
        """Select only immutable known parts, without output-row limits."""
        names = tuple(name for name, _ in plan.parts)
        if names:
            params[f"parts{index}"] = names
            where = f"_part IN %(parts{index})s"
        else:
            where = "0"
        return f"(SELECT _peerdb_synced_at FROM {table} WHERE {where})"

    def _latest_sync(
        self,
        probe: _Probe,
        tables: list[_Table],
        plans: dict[str, _SampleRead],
        *,
        deadline: float,
    ) -> dict[str, tuple]:
        """Read each inventoried part at most once, then validate coverage.

        MergeTree's _part predicate prunes whole parts before reading granules.
        Unlike block-range overlap or LIMIT, exact names cannot admit larger
        replacements or new parts. No prefixes, retries or whole-table fallback
        are used. Parallel reads and lightweight-delete filtering are confined
        to the same inventoried physical rows. Post-read inventory equality plus
        count equality is deliberately conservative: changes, masked rows or
        partial results produce unverified lag, never a sample maximum.
        """
        selects: list[str] = []
        params: dict[str, Any] = {}
        for index, table in enumerate(tables):
            params[f"table{index}"] = table.name
            source = self._sample_source(table.name, plans[table.key], index, params)
            selects.append(
                f"SELECT %(table{index})s, count(), "
                f"toDateTime64(max(_peerdb_synced_at), 6, 'UTC') FROM {source}"
            )
        rows = self._read(probe, " UNION ALL ".join(selects), params, deadline=deadline)
        try:
            after = self._part_inventory(probe, tables, deadline=deadline)
        except Exception as exc:
            if is_access_denied(exc):
                raise _PartInventoryDenied("CDC lag coverage inventory denied") from exc
            raise
        found = {name: (count, synced) for name, count, synced in rows}
        return {
            table.key: (
                *found[table.name],
                self._plan(after[table.key]) == plans[table.key]
                and found[table.name][0] == plans[table.key].rows,
            )
            for table in tables
            if table.name in found
        }

    def _sample_destination(
        self,
        role: str,
        probe: _Probe,
        ready: list[_Table],
        plans: dict[str, _SampleRead],
        checks: dict[str, str],
        *,
        listed: bool,
        deadline: float,
    ) -> dict[str, tuple]:
        """One bounded data attempt per destination, under the shared deadline."""
        try:
            if not listed:
                self._without_part_metadata(
                    role, probe, ready, checks, deadline=deadline
                )
                return {}
            return self._latest_sync(probe, ready, plans, deadline=deadline)
        except Exception as exc:
            if is_timeout(exc):
                state = "timeout"
            elif isinstance(exc, _PartInventoryDenied):
                state = "unsampled"
            else:
                state = "unavailable"
            logger.warning(
                "CDC lag check failed",
                connection=role,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            checks.update({table.key: state for table in ready})
            return {}

    def _lag(
        self,
        tables: list[_Table],
        probes: dict[str, _Probe],
        checks: dict[str, str],
        part_metadata: set[str],
        *,
        deadline: float,
    ) -> dict[str, float]:
        cdc = [table for table in tables if table.cdc]
        plans, listed = self._sample_plans(
            cdc, probes, checks, part_metadata, deadline=deadline
        )
        by_role: dict[str, list[_Table]] = {}
        for table in cdc:
            if checks[table.key] == "ready":
                by_role.setdefault(table.connection, []).append(table)
        samples: dict[str, tuple] = {}
        for role, ready in by_role.items():
            samples.update(
                self._sample_destination(
                    role,
                    probes[role],
                    ready,
                    plans,
                    checks,
                    listed=role in listed,
                    deadline=deadline,
                )
            )
        lag = {}
        values: dict[str, float] = {}
        for table in cdc:
            if checks[table.key] != "ready":
                continue
            row = samples.get(table.key)
            if row and not row[2]:
                checks[table.key] = "unverified"
            elif row and row[0] == 0:
                checks[table.key] = "empty"
            elif row and isinstance(row[1], datetime):
                last_sync = row[1]
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=UTC)
                value = max(0.0, (datetime.now(UTC) - last_sync).total_seconds())
                values[table.key] = value
                if value > self.LAG_THRESHOLD_SECONDS:
                    checks[table.key] = "lagging"
            else:
                checks[table.key] = "unavailable"
        for table in cdc:
            value = values.get(table.key, -1.0)
            if checks[table.key] not in ("ready", "lagging"):
                value = -1.0
            # In mixed deployments the public per-table value represents both
            # destinations: an unavailable sample wins over a successful one.
            previous = lag.get(table.name, value)
            lag[table.name] = -1.0 if min(previous, value) < 0 else max(previous, value)
        return lag

    def get_cdc_lag(self) -> dict[str, float]:
        """Lag for declared CDC tables; omit native/retired tables, use -1 if unknown."""
        tables = [table for table in self._tables() if table.cdc]
        deadline = monotonic() + self.CHECK_TIMEOUT_SECONDS
        probes: dict[str, _Probe] = {}
        try:
            checks, _, part_metadata = self._inspect(tables, probes, deadline=deadline)
            return self._lag(tables, probes, checks, part_metadata, deadline=deadline)
        finally:
            self._close(probes)

    def get_health_status(self) -> HealthStatus:
        if not is_clickhouse_enabled():
            return HealthStatus(
                status="disabled", clickhouse_connected=False, cdc_lag={}
            )

        tables = self._tables()
        deadline = monotonic() + self.CHECK_TIMEOUT_SECONDS
        probes: dict[str, _Probe] = {}
        try:
            checks, connected, part_metadata = self._inspect(
                tables, probes, deadline=deadline
            )
            cdc_lag = self._lag(
                tables, probes, checks, part_metadata, deadline=deadline
            )
        finally:
            self._close(probes)
        states = set(checks.values())
        # Incomplete coverage alone is non-degraded but explicitly unverified.
        # Preserve actionable metadata-denial and timeout degradation.
        degraded = {"lagging", "timeout", "unsampled"}
        if (
            not connected
            or not tables
            or states
            - {
                "ready",
                "empty",
                "unverified",
                *degraded,
            }
        ):
            status = "unhealthy"
        elif states & degraded:
            status = "degraded"
        else:
            status = "healthy"

        return HealthStatus(
            status=status,
            clickhouse_connected=connected,
            cdc_lag=cdc_lag,
            cdc_lag_verified={table: lag >= 0 for table, lag in cdc_lag.items()},
            details={
                "checks": checks,
                "routing": {
                    k: v
                    for k, v in settings.CLICKHOUSE.items()
                    if k.startswith("CH_ROUTE_")
                },
            },
        )
