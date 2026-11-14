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
    """Rows of one table's parts, chosen by ``(partition, low, high)`` blocks.

    ``rows`` bounds the read. A prefix read takes only the first ``rows`` rows
    of its part, which ClickHouse serves from the granules that hold them.
    """

    ranges: tuple[tuple[str, int, int], ...]
    rows: int
    prefix: bool = False


class _BudgetSpent(TimeoutError):
    """The check's shared wall ran out before this statement was sent."""


def _block_range(part_name: str) -> tuple[str, int, int] | None:
    """``partition_minblock_maxblock_level[_mutation]`` to its blocks."""
    fields = part_name.split("_")
    if len(fields) < 4:
        return None
    try:
        return fields[0], int(fields[1]), int(fields[2])
    except ValueError:
        return None


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
    # handshake and normally three statements (schema, part inventory or table
    # sizes, lag). A statement
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
    # ClickHouse reads whole granules (the mirrors' index_granularity), so a
    # prefix read costs its rows rounded up to granules.
    LAG_SAMPLE_GRANULE_ROWS = 8192
    # system.parts columns the sample reads. Stock ClickHouse configs require a
    # grant for system tables. A role that cannot read all of them is never
    # sent a query that would be denied: it reads a table in full only when
    # ``system.tables`` shows it fits the budget, and otherwise reports the
    # table's lag as unknown (degraded) until it is granted
    # SELECT(<PART_COLUMNS>) ON system.parts.
    PART_COLUMNS = ("database", "table", "name", "rows", "active", "modification_time")

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

    def _sample_plans(
        self,
        tables: list[_Table],
        probes: dict[str, _Probe],
        checks: dict[str, str],
        part_metadata: set[str],
        *,
        deadline: float,
    ) -> tuple[dict[str, list[_SampleRead]], set[str]]:
        """Plan each ready CDC table's bounded lag sample from its newest parts.

        One ``system.parts`` statement per destination lists the newest active
        parts. Also returns the destinations whose inventory was read: there a
        ready table without a plan has no active row. A destination whose role
        cannot read ``system.parts`` (columns hidden, or the read denied) is
        not listed, so its tables are sized from ``system.tables`` instead.
        """
        selected: dict[str, list[_Table]] = {}
        for table in tables:
            if (
                table.cdc
                and checks[table.key] == "ready"
                and table.connection in part_metadata
            ):
                selected.setdefault(table.connection, []).append(table)
        plans = {}
        listed = set()
        for role, ready in selected.items():
            try:
                rows = self._read(
                    probes[role],
                    "SELECT table, name, rows, modification_time FROM system.parts "
                    "WHERE database = currentDatabase() AND active AND rows > 0 "
                    "AND table IN %(tables)s "
                    "ORDER BY table, modification_time DESC, rows "
                    f"LIMIT {int(self.LAG_SAMPLE_MAX_PARTS)} BY table",
                    {"tables": tuple(table.name for table in ready)},
                    deadline=deadline,
                )
            except Exception as exc:
                if is_access_denied(exc):
                    # SHOW COLUMNS alone lists the columns but grants no SELECT.
                    logger.warning("CDC lag part inventory denied", connection=role)
                    continue
                state = "timeout" if is_timeout(exc) else "unavailable"
                logger.warning(
                    "CDC lag check failed",
                    connection=role,
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                checks.update({table.key: state for table in ready})
                continue
            listed.add(role)
            parts: dict[str, list[tuple]] = {table.name: [] for table in ready}
            for table_name, *part in rows:
                if table_name in parts:
                    parts[table_name].append(tuple(part))
            for table in ready:
                if reads := self._plan(parts[table.name]):
                    plans[table.key] = reads
        return plans, listed

    def _within_budget(
        self,
        role: str,
        probe: _Probe,
        tables: list[_Table],
        checks: dict[str, str],
        *,
        listed: bool,
        deadline: float,
    ) -> list[_Table]:
        """Return the tables small enough to aggregate in full; mark the rest.

        ``system.tables`` lists every table the role may see, whatever its
        system grants, and MergeTree reports ``total_rows`` from part metadata
        without reading data. A table above ``LAG_SAMPLE_MAX_ROWS``, or whose
        size cannot be read, is marked ``unsampled``: its lag is unknown, which
        degrades the check instead of scanning the whole table on every probe.
        ``listed`` says the role read its part inventory: the sample found no
        visible row, rather than the role lacking part metadata.
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
        sizes = dict(rows)
        small = []
        for table in tables:
            size = sizes.get(table.name)
            if size is not None and size <= self.LAG_SAMPLE_MAX_ROWS:
                small.append(table)
            else:
                checks[table.key] = "unsampled"
        if unsampled := [table.name for table in tables if table not in small]:
            logger.warning(
                "CDC lag unknown",
                connection=role,
                tables=unsampled,
                max_rows=self.LAG_SAMPLE_MAX_ROWS,
                cause="masked_sample" if listed else "no_part_metadata",
                required_grant=(
                    None
                    if listed
                    else f"SELECT({', '.join(self.PART_COLUMNS)}) ON system.parts"
                ),
            )
        return small

    def _plan(self, parts: list[tuple]) -> list[_SampleRead]:
        """Choose one table's sample from its active parts, newest first.

        ``parts`` are ``(name, rows, modification_time)``. A row is only ever
        stored in parts written no earlier than its sync: inserts, merges and
        replica fetches each write a new part. While CDC is current the latest
        sync is therefore in a recently written part. Parts that fit the
        remaining ``LAG_SAMPLE_MAX_ROWS`` are read in full; a larger part is
        skipped, not a stop. A newest part too large to read in full (a bulk
        batch, or a merge that absorbed the newest small parts) contributes its
        first rows instead: a tenth of the budget at most, and only what the
        parts of the newest second already taken leave, so a current part is
        never dropped for it; none when not even one granule fits.
        ``modification_time`` has one-second resolution, so that is the
        smallest oversized part written in the newest second. The sample's max never exceeds the table's, so the
        sampled lag is never smaller than the true lag, and it is exact while
        the newest sync sits in parts that fit.

        Parts are selected by partition and block range, not by name. A merge
        replaces names but writes the union of its sources' block ranges in the
        same partition, so a merge that commits between the inventory and the
        sample still selects the merged part. The full reads are limited to the
        rest of the budget, which bounds even a merge that absorbed a large part
        that was not listed.
        """
        budget = self.LAG_SAMPLE_MAX_ROWS
        reads = []
        full: list[tuple[tuple[str, int, int], int]] = []
        remaining = budget
        prefix_cost = 0
        newest = parts[0][2] if parts else None
        for name, part_rows, modified in parts:
            blocks = _block_range(name)
            if blocks is None:
                continue
            if part_rows <= remaining:
                full.append((blocks, part_rows))
                remaining -= part_rows
            elif not reads and modified == newest:
                # The parts already taken are of the newest second and may
                # hold the latest sync: the prefix gets only what they leave.
                prefix = self._prefix_rows(remaining)
                if prefix:
                    reads.append(_SampleRead((blocks,), rows=prefix, prefix=True))
                    prefix_cost = self._prefix_cost(prefix)
                    remaining -= prefix_cost
        if full:
            reads.append(
                _SampleRead(
                    tuple(blocks for blocks, _ in full), rows=budget - prefix_cost
                )
            )
        return reads

    def _prefix_rows(self, remaining: int) -> int:
        """Rows of a prefix whose cost fits ``remaining``: a tenth of the
        budget at most, fewer when less is left, none when not even one row's
        granule fits."""
        rows = min(remaining, self.LAG_SAMPLE_MAX_ROWS // 10)
        if self.LAG_SAMPLE_GRANULE_ROWS <= self.LAG_SAMPLE_MAX_ROWS:
            rows = min(rows, remaining - (self.LAG_SAMPLE_GRANULE_ROWS - 1))
        return max(rows, 0)

    def _prefix_cost(self, rows: int) -> int:
        """Rows a prefix read may cost: ClickHouse reads whole granules, and
        with adaptive granularity the granule holding the last row may start
        anywhere before it, so at most ``LAG_SAMPLE_GRANULE_ROWS - 1`` rows
        past the prefix. A budget smaller than one granule cannot hold any
        prefix, so its planned rows are charged instead."""
        cost = rows + self.LAG_SAMPLE_GRANULE_ROWS - 1
        return cost if cost <= self.LAG_SAMPLE_MAX_ROWS else rows

    @staticmethod
    def _sample_source(
        table: str, reads: list[_SampleRead], index: int, params: dict[str, Any]
    ) -> str:
        selects = []
        for read_index, read in enumerate(reads):
            clauses = []
            for range_index, (partition, low, high) in enumerate(read.ranges):
                key = f"{index}_{read_index}_{range_index}"
                params.update(
                    {
                        f"partition{key}": partition,
                        f"low{key}": low,
                        f"high{key}": high,
                    }
                )
                # Virtual-column predicates select parts before any row is read.
                clauses.append(
                    f"(_partition_id = %(partition{key})s"
                    f" AND toInt64(splitByChar('_', _part)[2]) <= %(high{key})s"
                    f" AND toInt64(splitByChar('_', _part)[3]) >= %(low{key})s)"
                )
            where = " OR ".join(clauses)
            if read.prefix:
                where = f"({where}) AND _part_offset < {int(read.rows)}"
            selects.append(
                f"(SELECT _peerdb_synced_at FROM {table} WHERE {where} "
                f"LIMIT {int(read.rows)})"
            )
        return f"({' UNION ALL '.join(selects)})"

    def _latest_sync(
        self,
        probe: _Probe,
        tables: list[_Table],
        plans: dict[str, list[_SampleRead]],
        *,
        deadline: float,
    ) -> dict[str, tuple]:
        """Return ``(count, max(_peerdb_synced_at))`` per table, in one statement.

        Planned tables read their sample; the others the exact whole-table
        aggregate, which callers only request for tables known to be small.
        max() alone returns an epoch default on an empty table.
        """
        selects: list[str] = []
        params: dict[str, Any] = {}
        for index, table in enumerate(tables):
            params[f"table{index}"] = table.name
            reads = plans.get(table.key)
            source = (
                self._sample_source(table.name, reads, index, params)
                if reads
                else table.name
            )
            # One timestamp type for the UNION, whatever each mirror's zone.
            selects.append(
                f"SELECT %(table{index})s, count(), "
                f"toDateTime64(max(_peerdb_synced_at), 6, 'UTC') FROM {source}"
            )
        rows = self._read(probe, " UNION ALL ".join(selects), params, deadline=deadline)
        found = {name: (count, synced) for name, count, synced in rows}
        return {table.key: found[table.name] for table in tables if table.name in found}

    def _sample_destination(
        self,
        role: str,
        probe: _Probe,
        ready: list[_Table],
        plans: dict[str, list[_SampleRead]],
        checks: dict[str, str],
        *,
        listed: bool,
        deadline: float,
    ) -> dict[str, tuple]:
        """Read one destination's lag samples, never an unbounded table scan.

        Planned tables read their sample. A table is aggregated in full only
        when it is known to be small: its listed part inventory has no active
        row, or ``system.tables`` puts it within ``LAG_SAMPLE_MAX_ROWS``. A
        sample with no visible row (its parts hold only masked rows) is retried
        once under the same rule. A failure marks only the tables it covered.
        """
        samples: dict[str, tuple] = {}
        pending = ready
        try:
            if not listed:
                pending = self._within_budget(
                    role, probe, pending, checks, listed=False, deadline=deadline
                )
            if pending:
                samples = self._latest_sync(probe, pending, plans, deadline=deadline)
            masked = [
                table
                for table in pending
                if table.key in plans and samples.get(table.key, (0,))[0] == 0
            ]
            for table in masked:
                samples.pop(table.key, None)
            pending = masked
            if pending:
                pending = self._within_budget(
                    role, probe, pending, checks, listed=True, deadline=deadline
                )
            if pending:
                samples.update(self._latest_sync(probe, pending, {}, deadline=deadline))
        except Exception as exc:
            state = "timeout" if is_timeout(exc) else "unavailable"
            logger.warning(
                "CDC lag check failed",
                connection=role,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            checks.update({table.key: state for table in pending})
        return samples

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
        for table in cdc:
            value = -1.0
            if checks[table.key] == "ready":
                row = samples.get(table.key)
                if row and row[0] == 0:
                    checks[table.key] = "empty"
                elif row and isinstance(row[1], datetime):
                    last_sync = row[1]
                    if last_sync.tzinfo is None:
                        last_sync = last_sync.replace(tzinfo=UTC)
                    value = max(0.0, (datetime.now(UTC) - last_sync).total_seconds())
                    if value > 60:
                        checks[table.key] = "lagging"
                else:
                    checks[table.key] = "unavailable"
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
        # Unknown lag (slow, or a table too large to read without part
        # metadata) is degraded; a missing or unreadable required table is not.
        degraded = {"lagging", "timeout", "unsampled"}
        if not connected or not tables or states - {"ready", "empty", *degraded}:
            status = "unhealthy"
        elif states & degraded:
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
