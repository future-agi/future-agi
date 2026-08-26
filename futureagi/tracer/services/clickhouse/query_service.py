"""
Analytics Query Service.

ClickHouse is the single source of truth for the analytics paths in this
module; the per-query-type routing toggle (`CH_ROUTE_*`) and PG fallback
were removed in the CH25 migration close-out (2026-05-26). The CH25 read
endpoints assume CH is reachable; if it's down, the request fails loudly.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

import structlog
from django.conf import settings

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyInventory,
    AttributeReadSelector,
)
from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

logger = structlog.get_logger(__name__)

APPLICATION_READ_TIMEOUT_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
APPLICATION_READ_MAX_MEMORY_USAGE = (
    settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES
)
APPLICATION_READ_MAX_BYTES_TO_READ = settings.CLICKHOUSE_APPLICATION_READ_MAX_BYTES
REVIEWED_READ_TIMEOUT_CEILING_MS = settings.CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS


class SpanTraceMapIntegrityError(ReadDeadlineExceeded):
    """A finite scored span did not resolve to one unambiguous live trace."""


_PAGE_EVAL_READ_SETTINGS = {
    "max_threads": 2,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": APPLICATION_READ_MAX_BYTES_TO_READ,
    "max_memory_usage": APPLICATION_READ_MAX_MEMORY_USAGE,
    "timeout_overflow_mode": "throw",
}

# Kept as a code-owned constant (rather than a request-controlled identifier) so
# CH25 integration tests can exercise tenant-collision handling against an
# isolated table without weakening production SQL identifier safety.
_SPANS_TABLE = "spans"


def _eval_live_projection(table: str, alias: str = "eval_scan") -> str:
    return ", ".join(
        f"{alias}.{column}" for column in eval_logger_live_state_columns(table)
    )


def _eval_lifecycle_projection(table: str, alias: str = "eval_scan") -> str:
    """Project lifecycle fields without assuming optional v2 columns exist."""

    if table.endswith("_v2"):
        return "'completed' AS status, CAST(NULL, 'Nullable(String)') AS skipped_reason"
    return f"{alias}.status, {alias}.skipped_reason"


class QueryType(StrEnum):
    """Supported query types with per-type routing."""

    TIME_SERIES = "TIME_SERIES"
    TRACE_LIST = "TRACE_LIST"
    SESSION_LIST = "SESSION_LIST"
    EVAL_METRICS = "EVAL_METRICS"
    ERROR_ANALYSIS = "ERROR_ANALYSIS"
    SPAN_LIST = "SPAN_LIST"
    TRACE_OF_SESSION_LIST = "TRACE_OF_SESSION_LIST"
    SPAN_GRAPH = "SPAN_GRAPH"
    VOICE_CALL_LIST = "VOICE_CALL_LIST"
    SESSION_ANALYTICS = "SESSION_ANALYTICS"
    ANNOTATION_GRAPH = "ANNOTATION_GRAPH"
    TRACE_DETAIL = "TRACE_DETAIL"
    MONITOR_METRICS = "MONITOR_METRICS"
    ANNOTATION_DETAIL = "ANNOTATION_DETAIL"
    VOICE_CALL_DETAIL = "VOICE_CALL_DETAIL"


@dataclass
class QueryResult:
    """Container for query results with metadata."""

    data: Any  # Can be list, dict, or any serializable structure
    row_count: int
    backend_used: str  # "clickhouse" or "postgres"
    query_time_ms: float
    columns: list[str] | None = None

    @classmethod
    def from_clickhouse_rows(cls, rows, columns, query_time_ms):
        """Create from ClickHouse result rows."""
        col_names = [c[0] if isinstance(c, tuple) else c for c in columns]
        data = [dict(zip(col_names, row, strict=False)) for row in rows]
        return cls(
            data=data,
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=query_time_ms,
            columns=col_names,
        )


class QueryExecutor(Protocol):
    """Minimal read-query boundary shared by ClickHouse selectors."""

    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> QueryResult: ...


class AnalyticsQueryService:
    """ClickHouse query dispatcher for the analytics endpoints."""

    def __init__(
        self,
        *,
        ch_client: ClickHouseClient | None = None,
        read_timeout_ceiling_ms: int | None = None,
    ) -> None:
        self._ch_client = ch_client
        self._read_timeout_ceiling_ms = self._validated_read_timeout_ceiling_ms(
            read_timeout_ceiling_ms
        )

    @staticmethod
    def _validated_read_timeout_ceiling_ms(value: int | None) -> int:
        ceiling_ms = APPLICATION_READ_TIMEOUT_MS if value is None else value
        if type(ceiling_ms) is not int or not (
            1 <= ceiling_ms <= REVIEWED_READ_TIMEOUT_CEILING_MS
        ):
            raise ValueError(
                "ClickHouse service read timeout ceiling is outside [1, "
                f"{REVIEWED_READ_TIMEOUT_CEILING_MS}] ms"
            )
        return ceiling_ms

    @property
    def read_timeout_ceiling_ms(self) -> int:
        # V2AnalyticsQueryService predates this constructor and intentionally
        # owns its process-wide client. Preserve its interactive default until
        # that shared lane is explicitly instantiated through this base class.
        return getattr(
            self,
            "_read_timeout_ceiling_ms",
            APPLICATION_READ_TIMEOUT_MS,
        )

    @property
    def ch_client(self) -> ClickHouseClient:
        if self._ch_client is None:
            self._ch_client = get_clickhouse_client()
        return self._ch_client

    @property
    def supports_per_query_read_settings(self) -> bool:
        """Whether query-local resource ceilings reach ClickHouse.

        A server profile locked at ``readonly=1`` rejects every query-local
        setting.  Optional reads whose safety depends on a tighter timeout or
        byte ceiling must therefore be skipped on that lane instead of merely
        assuming the requested settings were enforced.
        """

        client = self.ch_client
        return not bool(
            getattr(client, "server_enforced_readonly", False)
            or getattr(client, "server_profile_locked", False)
        )

    def should_use_clickhouse(self, query_type: QueryType | str) -> bool:
        """Compatibility shim for legacy route-toggle callers/tests."""
        return is_clickhouse_enabled()

    def execute_ch_query(
        self,
        query: str,
        params: dict = None,
        timeout_ms: int | None = APPLICATION_READ_TIMEOUT_MS,
        settings: dict | None = None,
    ) -> QueryResult:
        """Execute a query on ClickHouse and return QueryResult."""
        # Normalize the ordinary application read lane even for older callers
        # that omit settings. Row-count ceilings reject healthy high-volume
        # reads before their finite byte/time/result budgets are reached.
        requested_timeout_ms = (
            self.read_timeout_ceiling_ms if timeout_ms is None else int(timeout_ms)
        )
        timeout_ms = min(
            self.read_timeout_ceiling_ms,
            max(1, requested_timeout_ms),
        )
        if self.supports_per_query_read_settings:
            settings = dict(settings or {})
            settings.pop("max_rows_to_read", None)

            def finite_ceiling(name: str, ceiling: int) -> int:
                requested = int(settings.get(name, 0) or 0)
                return ceiling if requested <= 0 else min(requested, ceiling)

            settings["max_memory_usage"] = finite_ceiling(
                "max_memory_usage", APPLICATION_READ_MAX_MEMORY_USAGE
            )
            settings["max_bytes_to_read"] = finite_ceiling(
                "max_bytes_to_read", APPLICATION_READ_MAX_BYTES_TO_READ
            )
        start = time.monotonic()
        try:
            rows, columns, qt = self.ch_client.execute_read(
                query, params or {}, timeout_ms=timeout_ms, settings=settings
            )
        except TimeoutError as exc:
            # Some native-driver wrappers surface socket/read deadlines as the
            # built-in timeout type. Normalize only at this ClickHouse boundary;
            # the shared classifier deliberately rejects arbitrary application
            # TimeoutError instances.
            if isinstance(exc, ReadDeadlineExceeded):
                raise
            raise ReadDeadlineExceeded("ClickHouse query timed out") from exc
        elapsed = (time.monotonic() - start) * 1000

        col_names = [c[0] if isinstance(c, tuple) else c for c in columns]
        data = [dict(zip(col_names, row, strict=False)) for row in rows]

        logger.info(
            "ch_query_executed",
            query_time_ms=round(elapsed, 2),
            rows=len(rows),
            backend="clickhouse",
        )

        return QueryResult(
            data=data,
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=round(elapsed, 2),
            columns=col_names,
        )

    def get_span_attribute_keys_ch_for_projects(
        self,
        project_ids: list[str],
        *,
        recent_days: int | None = None,
        timeout_ms: int = 1500,
        outer_limit: int = 1000,
        include_counts: bool = False,
        order_by_count_desc: bool = False,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        exact_key: str | None = None,
    ) -> AttributeKeyInventory:
        """Return the bounded CH25 key inventory for one or more projects.

        This compatibility facade intentionally does *not* call
        :meth:`execute_ch_query`: that method is bound to the legacy ClickHouse
        connection. Attribute reads construct an explicit CH25 executor from
        ``CLICKHOUSE_V2`` so a split ``CH_DATABASE``/``CH25_DATABASE``
        deployment cannot query the wrong ``spans`` table.
        """

        horizon_days = 365 if recent_days is None else int(recent_days)
        selector = AttributeReadSelector(
            wall_timeout_ms=timeout_ms,
            typed_only=True,
        )
        read = selector.discover_keys(
            project_ids,
            exact_key=exact_key,
            horizon_days=horizon_days,
            max_keys=outer_limit,
            order_by_count_desc=order_by_count_desc,
            window_start=window_start,
            window_end=window_end,
        )
        return AttributeKeyInventory(read, include_counts=include_counts)

    def get_span_attribute_keys_ch(
        self, project_id: str, *, exact_key: str | None = None
    ) -> AttributeKeyInventory:
        """Get distinct span attribute keys with types from ClickHouse.

        Reads from the v2 ``spans`` table's typed attribute maps
        (``attrs_string``, ``attrs_number``, ``attrs_bool``). These are
        populated at ingest time by fi-collector, so they are the canonical
        attribute inventory — no CDC fallback needed post-CH25 close-out.
        """
        # This is a discovery query (populate a filter dropdown), not an
        # accounting one, so an approximate sample is semantically fine.
        # Two bounds keep it bounded even on very large projects:
        #   * 7-day window on `start_time` (the partition key is
        #     `toDate(start_time)`) so CH can skip partitions and granules.
        #   * `LIMIT 10000` inside each per-map subquery before the
        #     ARRAY JOIN — without this, projects with millions of spans
        #     and wide `attrs_*` maps hit Code: 307 (max_bytes_to_read)
        #     because every row's Map gets exploded.
        return self.get_span_attribute_keys_ch_for_projects(
            [project_id], exact_key=exact_key
        )

    @staticmethod
    def _eval_config_ids_query(
        scope_sql: str,
        extra_where: str = "",
        *,
        eval_logger_table: str | None = None,
    ) -> str:
        """Build the shared "distinct eval-config IDs that have data" query.

        One body for every eval-config discovery read: the table and its
        not-deleted predicate come from ``eval_logger_source()`` (so a ``_v2``
        stack uses ``is_deleted = 0``), and callers supply only the
        trace-scoping clause (plus an optional ``extra_where`` such as a
        ``created_at`` window that prunes the eval table's monthly partitions).

        PERF: no ``FINAL``. This read only needs the *distinct set* of config
        ids that appear — a superseded or tombstoned row still carries the same
        ``custom_eval_config_id``, and the not-deleted predicate already drops
        delete markers, so collapsing ReplacingMergeTree versions adds nothing.
        FINAL, by contrast, forced a full-table merge before the scope filter
        and was a primary OOM/crash source on the span-list hot path.
        """
        eval_table, eval_nd = eval_logger_source(table=eval_logger_table)
        return (
            "SELECT DISTINCT toString(custom_eval_config_id) AS config_id "
            f"FROM {eval_table} "
            f"WHERE {eval_nd} "
            f"{extra_where} "
            f"AND {scope_sql}"
        )

    def get_eval_config_ids_with_data_ch(
        self,
        project_id: str,
        timeout_ms: int = 5000,
        window_days: int | None = 30,
        candidate_config_ids: list[str] | None = None,
        *,
        eval_logger_table: str | None = None,
    ) -> list[str]:
        """Distinct eval config IDs that have data for a project.

        Two scoping strategies:

        * FAST PATH (``candidate_config_ids`` given): the caller has already
          resolved this project's configs from Postgres (``CustomEvalConfig`` is
          project-scoped via its ``project`` FK), so we only need to know which
          of them have *recent* eval rows. The scope becomes
          ``custom_eval_config_id IN (…)`` — the LEADING column of the eval
          table's sort key ``(custom_eval_config_id, created_at, id)`` — so CH
          prunes straight to those configs' granules. This turns the old
          full-table trace join (tens of seconds, ~1 GB, OOM-prone at scale)
          into a sub-second, tens-of-MB read. This is the span-list hot path.

        * TRACE-JOIN PATH (no ``candidate_config_ids``): kept for callers that
          cannot pre-resolve the project's configs. Bounded to ``window_days``
          (default 30) so it prunes span/eval partitions instead of scanning all
          history, and ``max_bytes_in_set`` fails loud (catchable) rather than
          OOM-killing the server. The previous version was unbounded + used
          ``FINAL`` — the primary OOM source. Pass ``window_days=None`` to
          restore the unbounded window.
        """
        eval_table, eval_nd = eval_logger_source(table=eval_logger_table)
        params: dict[str, Any] = {}
        window_sql = ""
        if window_days is not None:
            params["window_days"] = int(window_days)
            window_sql = "AND created_at >= now() - toIntervalDay(%(window_days)s)"

        if candidate_config_ids is not None:
            return AnalyticsQueryService.get_eval_config_ids_for_candidates_ch(
                self,
                candidate_config_ids,
                timeout_ms=timeout_ms,
                window_days=window_days,
                eval_logger_table=eval_logger_table,
            )

        params["project_id"] = project_id
        span_window = (
            " AND start_time >= now() - toIntervalDay(%(window_days)s)"
            if window_days is not None
            else ""
        )
        query = self._eval_config_ids_query(
            "trace_id IN ("
            "SELECT trace_id FROM spans "
            f"WHERE project_id = %(project_id)s AND is_deleted = 0{span_window} "
            "GROUP BY trace_id"
            ")",
            extra_where=window_sql,
            eval_logger_table=eval_logger_table,
        )
        result = self.execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings={"max_bytes_in_set": 500_000_000},
        )
        return [row["config_id"] for row in result.data]

    def get_eval_config_ids_for_candidates_ch(
        self,
        candidate_config_ids: list[str],
        timeout_ms: int = 5000,
        window_days: int | None = 30,
        *,
        eval_logger_table: str | None = None,
    ) -> list[str]:
        """Return candidate config ids that have direct eval rows.

        Candidate config ids are globally unique and already carry project
        scope from ``CustomEvalConfig``.  Keeping this operation explicitly
        project-free allows one leading-key lookup for a multi-project metrics
        catalog instead of serial scans or an incorrect first-project scope.
        """

        if not candidate_config_ids:
            return []
        eval_table, eval_nd = eval_logger_source(table=eval_logger_table)
        params: dict[str, Any] = {"config_ids": tuple(candidate_config_ids)}
        window_sql = ""
        if window_days is not None:
            params["window_days"] = int(window_days)
            window_sql = "AND created_at >= now() - toIntervalDay(%(window_days)s)"
        query = (
            "SELECT DISTINCT toString(custom_eval_config_id) AS config_id "
            f"FROM {eval_table} "
            f"WHERE {eval_nd} {window_sql} "
            "AND custom_eval_config_id IN %(config_ids)s"
        )
        result = self.execute_ch_query(query, params, timeout_ms=timeout_ms)
        return [row["config_id"] for row in result.data]

    def get_eval_config_ids_for_traces_ch(
        self,
        trace_ids: list[str],
        candidate_config_ids: list[str],
        timeout_ms: int = 3000,
        *,
        eval_logger_table: str | None = None,
    ) -> list[str]:
        """Project-owned eval configs recorded for an explicit trace set.

        ``tracer_eval_logger`` has no project column and trace IDs are supplied
        by customers, so a bare trace-id predicate is not a tenant boundary.
        Callers must first resolve the requesting project's config IDs from
        Postgres; the globally unique config IDs then provide the project scope
        for this ClickHouse discovery read.
        """
        if not (trace_ids and candidate_config_ids):
            return []
        query = self._eval_config_ids_query(
            "trace_id IN %(trace_ids)s "
            "AND custom_eval_config_id IN %(candidate_config_ids)s",
            eval_logger_table=eval_logger_table,
        )
        result = self.execute_ch_query(
            query,
            {
                "trace_ids": trace_ids,
                "candidate_config_ids": tuple(candidate_config_ids),
            },
            timeout_ms=timeout_ms,
        )
        return [row["config_id"] for row in result.data]

    def get_span_trace_map(
        self,
        trace_ids: list[str],
        project_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        timeout_ms: int = 10000,
        settings: dict | None = None,
        scored_span_ids: list[str] | tuple[str, ...] | None = None,
        trace_identities: list[tuple[str, str]]
        | tuple[tuple[str, str], ...]
        | None = None,
        scored_span_identities: list[tuple[str, str]]
        | tuple[tuple[str, str], ...]
        | None = None,
    ) -> dict[Any, Any]:
        """Map scored span id -> trace id for spans in the given traces.

        ``project_id`` prunes the scan to the partition/PK prefix; the
        ``start_date``/``end_date`` window (widened one day each side to cover a
        trace's full duration) prunes partitions.  Annotation callers also pass
        the finite set of ``Score.observation_span_id`` values discovered in
        PostgreSQL.  That predicate is load-bearing: a trace may contain an
        arbitrary number of unannotated spans, and materializing all of them
        merely to find the handful with scores can exhaust ClickHouse memory.

        ``scored_span_ids=None`` retains the generic compatibility shape for
        non-annotation callers.  An explicitly empty collection is a proven
        no-op and skips ClickHouse entirely.  Matching ids are packed per trace
        and expanded here, preserving every scored physical identity without a
        result row per span.
        """
        pair_scoped = trace_identities is not None or scored_span_identities is not None
        normalized_trace_identities: tuple[tuple[str, str], ...] | None = None
        normalized_scored_span_identities: tuple[tuple[str, str], ...] | None = None
        if pair_scoped:
            normalized_trace_identities = tuple(
                dict.fromkeys(
                    (str(candidate_project_id), str(candidate_trace_id))
                    for candidate_project_id, candidate_trace_id in (
                        trace_identities or ()
                    )
                    if candidate_project_id and candidate_trace_id
                )
            )
            normalized_scored_span_identities = tuple(
                dict.fromkeys(
                    (str(candidate_project_id), str(candidate_span_id))
                    for candidate_project_id, candidate_span_id in (
                        scored_span_identities or ()
                    )
                    if candidate_project_id and candidate_span_id
                )
            )
            if not normalized_trace_identities or not normalized_scored_span_identities:
                return {}
        elif not trace_ids:
            return {}
        normalized_scored_span_ids: tuple[str, ...] | None = None
        if scored_span_ids is not None:
            normalized_scored_span_ids = tuple(
                dict.fromkeys(str(span_id) for span_id in scored_span_ids if span_id)
            )
            if not normalized_scored_span_ids:
                return {}
        params: dict[str, Any] = {}
        if pair_scoped:
            params["trace_identities"] = normalized_trace_identities
            params["scored_span_identities"] = normalized_scored_span_identities
            where = [
                "(toString(project_id), toString(id)) IN %(scored_span_identities)s"
            ]
        else:
            params["trace_ids"] = trace_ids
            where = ["trace_id IN %(trace_ids)s", "is_deleted = 0"]
        if normalized_scored_span_ids is not None and not pair_scoped:
            params["scored_span_ids"] = normalized_scored_span_ids
            where.append("id IN %(scored_span_ids)s")
        if project_id is not None and not pair_scoped:
            params["project_id"] = project_id
            where.append("project_id = %(project_id)s")
        # A scored child may legitimately start more than one day after its
        # root. Once both the page trace ids and finite scored-span ids are
        # known, those identities are a stronger exact bound than a heuristic
        # date window. Keep the historical window only for generic callers
        # that did not provide scored identities.
        if (
            not pair_scoped
            and normalized_scored_span_ids is None
            and start_date is not None
            and end_date is not None
        ):
            params["start_date"] = start_date
            params["end_date"] = end_date
            where.append(
                "start_time >= %(start_date)s - INTERVAL 1 DAY "
                "AND start_time < %(end_date)s + INTERVAL 1 DAY"
            )
        # Resolve ReplacingMergeTree state before checking live membership.
        # Filtering ``is_deleted = 0`` in the physical scan resurrects an older
        # version after a tombstone. A span id can also be reused/reassigned;
        # retain every live candidate trace so Python can reject ambiguity.
        physical_where = (
            list(where)
            if pair_scoped
            else [
                predicate
                for predicate in where
                if predicate != "trace_id IN %(trace_ids)s"
                and predicate != "is_deleted = 0"
                and not predicate.startswith("start_time >=")
            ]
        )
        window_fragment = ""
        if (
            not pair_scoped
            and normalized_scored_span_ids is None
            and start_date is not None
            and end_date is not None
        ):
            window_fragment = (
                "AND start_time >= %(start_date)s - INTERVAL 1 DAY "
                "AND start_time < %(end_date)s + INTERVAL 1 DAY"
            )
        mapping_columns = (
            "project_id_string, span_id, live_trace_ids"
            if pair_scoped
            else "span_id, live_trace_ids"
        )
        candidate_project_projection = "project_id_string," if pair_scoped else ""
        candidate_group_by = "project_id_string, span_id" if pair_scoped else "span_id"
        membership_predicate = (
            "(project_id_string, latest_trace_id) IN %(trace_identities)s"
            if pair_scoped
            else "latest_trace_id IN %(trace_ids)s"
        )
        query = f"""
        SELECT groupArray(tuple({mapping_columns})) AS span_mappings
        FROM (
            SELECT
                {candidate_project_projection}
                span_id,
                groupUniqArray(latest_trace_id) AS live_trace_ids
            FROM (
                SELECT
                    project_id,
                    toString(project_id) AS project_id_string,
                    toString(id) AS span_id,
                    start_time,
                    toString(argMax(trace_id, _version)) AS latest_trace_id,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM spans
                WHERE {" AND ".join(physical_where) or "1"}
                  {window_fragment}
                GROUP BY project_id, id, start_time
            ) AS latest_physical_spans
            WHERE latest_is_deleted = 0
              AND {membership_predicate}
            GROUP BY {candidate_group_by}
        ) AS scored_span_candidates
        """
        result = self.execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings=settings,
        )
        span_trace_map: dict[Any, Any] = {}
        for row in result.data:
            mappings = row.get("span_mappings")
            if mappings is not None:
                for mapping in mappings or ():
                    expected_mapping_size = 3 if pair_scoped else 2
                    if (
                        not isinstance(mapping, (list, tuple))
                        or len(mapping) != expected_mapping_size
                    ):
                        raise SpanTraceMapIntegrityError(
                            "invalid scored span mapping payload"
                        )
                    if pair_scoped:
                        mapping_project_id, span_id, trace_candidates = mapping
                    else:
                        span_id, trace_candidates = mapping
                    candidates = tuple(
                        dict.fromkeys(
                            str(trace_id)
                            for trace_id in (trace_candidates or ())
                            if trace_id
                        )
                    )
                    if len(candidates) != 1:
                        raise SpanTraceMapIntegrityError(
                            "scored span resolves to ambiguous live traces"
                        )
                    if span_id:
                        if pair_scoped:
                            if not mapping_project_id:
                                raise SpanTraceMapIntegrityError(
                                    "scored span mapping omitted project identity"
                                )
                            project_key = str(mapping_project_id)
                            if (
                                project_key,
                                str(span_id),
                            ) not in normalized_scored_span_identities or (
                                project_key,
                                candidates[0],
                            ) not in normalized_trace_identities:
                                raise SpanTraceMapIntegrityError(
                                    "scored span mapping escaped candidate identities"
                                )
                            span_trace_map[(project_key, str(span_id))] = (
                                project_key,
                                candidates[0],
                            )
                        else:
                            span_trace_map[str(span_id)] = candidates[0]
                continue
            trace_id = str(row.get("trace_id") or "")
            row_project_id = str(row.get("project_id") or "")
            packed_span_ids = row.get("span_ids")
            if packed_span_ids is None:
                # Compatibility for alternative services and focused mocks
                # that still emit the historical expanded row shape.
                packed_span_ids = [row.get("span_id")]
            for span_id in packed_span_ids or ():
                if span_id and trace_id:
                    if pair_scoped:
                        if not row_project_id:
                            raise SpanTraceMapIntegrityError(
                                "scored span mapping omitted project identity"
                            )
                        if (
                            row_project_id,
                            str(span_id),
                        ) not in normalized_scored_span_identities or (
                            row_project_id,
                            trace_id,
                        ) not in normalized_trace_identities:
                            raise SpanTraceMapIntegrityError(
                                "scored span mapping escaped candidate identities"
                            )
                        span_trace_map[(row_project_id, str(span_id))] = (
                            row_project_id,
                            trace_id,
                        )
                    else:
                        span_trace_map[str(span_id)] = trace_id
        return span_trace_map

    def get_children_eval_metrics_ch(
        self,
        span_ids: list[str],
        timeout_ms: int = 5000,
        *,
        eval_logger_table: str | None = None,
    ) -> list[dict]:
        """Per-span eval rows for a set of child observation spans."""
        if not span_ids:
            return []
        eval_table, _ = eval_logger_source(table=eval_logger_table)
        _, eval_nd = eval_logger_source(
            "latest_eval",
            include_cdc_tombstone_guard=True,
            table=eval_logger_table,
        )
        eval_version = eval_logger_version_column(eval_table)
        live_projection = _eval_live_projection(eval_table)
        lifecycle_projection = _eval_lifecycle_projection(eval_table)
        query = f"""
            SELECT
                toString(observation_span_id) AS span_id,
                toString(custom_eval_config_id) AS config_id,
                output_float,
                output_bool,
                output_str_list,
                eval_explanation,
                error,
                error_message,
                output_str,
                status,
                skipped_reason
            FROM (
                SELECT
                    eval_scan.id,
                    eval_scan.observation_span_id,
                    eval_scan.custom_eval_config_id,
                    eval_scan.output_float,
                    eval_scan.output_bool,
                    eval_scan.output_str_list,
                    eval_scan.eval_explanation,
                    eval_scan.error,
                    eval_scan.error_message,
                    eval_scan.output_str,
                    {lifecycle_projection},
                    {live_projection}
                FROM {eval_table} AS eval_scan
                WHERE eval_scan.observation_span_id IN %(span_ids)s
                ORDER BY eval_scan.{eval_version} DESC
                LIMIT 1 BY eval_scan.id
            ) AS latest_eval
            WHERE {eval_nd}
        """
        result = self.execute_ch_query(
            query,
            {"span_ids": span_ids},
            timeout_ms=timeout_ms,
            settings=_PAGE_EVAL_READ_SETTINGS,
        )
        return result.data

    def get_eval_detail_ch(
        self,
        span_id: str,
        config_id: str,
        *,
        project_id: str,
        timeout_ms: int = 5000,
        eval_logger_table: str | None = None,
    ) -> dict | None:
        """Return one tenant-anchored span/trace eval detail row.

        ``observation_span_id`` is not globally unique in the CH25 ``spans``
        table and the eval table does not carry ``project_id``.  Reading the
        eval row by span/config alone therefore allows an ID collision to cross
        a project boundary.  Resolve exactly one *live* physical span identity
        inside the already-authorized project first, then bind its trace id into
        the eval read.  Zero or multiple live anchors fail closed.

        Both reads share one wall budget.  There is intentionally no Postgres
        telemetry fallback.
        """
        started = time.monotonic()

        def remaining_timeout_ms() -> int:
            remaining = int(timeout_ms - ((time.monotonic() - started) * 1000))
            if remaining <= 0:
                raise ReadDeadlineExceeded("evaluation detail read deadline exceeded")
            return remaining

        span_anchor_query = f"""
            SELECT toString(trace_id) AS trace_id
            FROM {_SPANS_TABLE}
            PREWHERE project_id = toUUID(%(project_id)s)
            WHERE id = %(span_id)s
            GROUP BY trace_id, id
            HAVING argMax(is_deleted, _version) = 0
            LIMIT 2
        """
        span_anchor = self.execute_ch_query(
            span_anchor_query,
            {"project_id": str(project_id), "span_id": str(span_id)},
            timeout_ms=remaining_timeout_ms(),
            settings={
                **_PAGE_EVAL_READ_SETTINGS,
                "max_result_rows": 2,
                "max_result_bytes": 64 * 1024,
                "result_overflow_mode": "throw",
            },
        )
        if len(span_anchor.data) != 1:
            return None
        trace_id = str(span_anchor.data[0]["trace_id"])

        eval_table, _ = eval_logger_source(table=eval_logger_table)
        _, eval_nd = eval_logger_source(
            "latest_eval",
            include_cdc_tombstone_guard=True,
            table=eval_logger_table,
        )
        eval_version = eval_logger_version_column(eval_table)
        live_projection = _eval_live_projection(eval_table)
        query = f"""
            SELECT
                output_float,
                output_bool,
                output_str_list,
                output_str,
                eval_explanation,
                error,
                error_message,
                output_metadata
            FROM (
                SELECT
                    eval_scan.id,
                    eval_scan.observation_span_id,
                    eval_scan.custom_eval_config_id,
                    eval_scan.target_type,
                    eval_scan.output_float,
                    eval_scan.output_bool,
                    eval_scan.output_str_list,
                    eval_scan.output_str,
                    eval_scan.eval_explanation,
                    eval_scan.error,
                    eval_scan.error_message,
                    eval_scan.output_metadata,
                    eval_scan.created_at,
                    eval_scan.updated_at,
                    {live_projection}
                FROM {eval_table} AS eval_scan
                WHERE eval_scan.observation_span_id = %(span_id)s
                  AND eval_scan.custom_eval_config_id = %(config_id)s
                  AND eval_scan.trace_id = toUUID(%(trace_id)s)
                  AND eval_scan.target_type IN ('span', 'trace')
                ORDER BY eval_scan.{eval_version} DESC
                LIMIT 1 BY eval_scan.id
            ) AS latest_eval
            WHERE {eval_nd}
            ORDER BY created_at DESC, updated_at DESC, id DESC
            LIMIT 1
        """
        result = self.execute_ch_query(
            query,
            {
                "span_id": str(span_id),
                "config_id": str(config_id),
                "trace_id": trace_id,
            },
            timeout_ms=remaining_timeout_ms(),
            settings={
                **_PAGE_EVAL_READ_SETTINGS,
                "max_result_rows": 1,
                "max_result_bytes": 1024 * 1024,
                "result_overflow_mode": "throw",
            },
        )
        return result.data[0] if result.data else None

    def get_trace_eval_scores_ch(
        self,
        trace_ids: list[str],
        config_ids: list[str],
        timeout_ms: int = 5000,
        *,
        eval_logger_table: str | None = None,
    ) -> list[dict]:
        """Per-(trace, config) aggregated eval scores for a session's traces."""
        if not (trace_ids and config_ids):
            return []
        eval_table, _ = eval_logger_source(table=eval_logger_table)
        _, eval_nd = eval_logger_source(
            "latest_eval",
            include_cdc_tombstone_guard=True,
            table=eval_logger_table,
        )
        eval_version = eval_logger_version_column(eval_table)
        live_projection = _eval_live_projection(eval_table)
        lifecycle_projection = _eval_lifecycle_projection(eval_table)
        query = f"""
            SELECT
                toString(trace_id) AS trace_id,
                toString(custom_eval_config_id) AS config_id,
                -- Score aggregates count *terminal* rows only: a non-terminal
                -- row can carry stale/coerced output (the CH mirror stores 0
                -- for a NULL bool), which would otherwise fabricate a score for
                -- a queued/running eval. The per-status counts below still see
                -- those rows so the caller can render the lifecycle state.
                round(avgIf(output_float,
                    error = 0 AND ifNull(output_str, '') != 'ERROR'
                    AND status NOT IN ('pending', 'running', 'skipped', 'errored')) * 100, 2) AS float_score,
                round(avgIf(CASE WHEN output_bool = 1 THEN 100.0
                                 WHEN output_bool = 0 THEN 0.0
                                 ELSE NULL END,
                    error = 0 AND ifNull(output_str, '') != 'ERROR'
                    AND status NOT IN ('pending', 'running', 'skipped', 'errored')), 2) AS bool_score,
                countIf(output_float IS NOT NULL AND error = 0 AND ifNull(output_str, '') != 'ERROR'
                    AND status NOT IN ('pending', 'running', 'skipped', 'errored')) AS float_count,
                countIf(output_bool IS NOT NULL AND error = 0 AND ifNull(output_str, '') != 'ERROR'
                    AND status NOT IN ('pending', 'running', 'skipped', 'errored')) AS bool_count,
                countIf(error = 1 OR ifNull(output_str, '') = 'ERROR' OR status = 'errored') AS error_count,
                countIf(status = 'skipped') AS skipped_count,
                countIf(status = 'running') AS running_count,
                countIf(status = 'pending') AS pending_count,
                anyIf(skipped_reason, status = 'skipped') AS skipped_reason
            FROM (
                SELECT
                    eval_scan.id,
                    eval_scan.trace_id,
                    eval_scan.custom_eval_config_id,
                    eval_scan.output_float,
                    eval_scan.output_bool,
                    eval_scan.output_str,
                    eval_scan.error,
                    {lifecycle_projection},
                    {live_projection}
                FROM {eval_table} AS eval_scan
                WHERE eval_scan.trace_id IN %(trace_ids)s
                  AND eval_scan.custom_eval_config_id IN %(config_ids)s
                ORDER BY eval_scan.{eval_version} DESC
                LIMIT 1 BY eval_scan.id
            ) AS latest_eval
            WHERE {eval_nd}
            GROUP BY trace_id, custom_eval_config_id
        """
        result = self.execute_ch_query(
            query,
            {"trace_ids": trace_ids, "config_ids": config_ids},
            timeout_ms=timeout_ms,
            settings={
                **_PAGE_EVAL_READ_SETTINGS,
                "max_result_rows": max(len(trace_ids) * len(config_ids), 1),
                "max_result_bytes": 8 * 1024 * 1024,
                "result_overflow_mode": "throw",
            },
        )
        return result.data

    def get_backend_status(self) -> dict[str, Any]:
        """Get the ClickHouse connectivity status."""
        status = {
            "clickhouse": {
                "enabled": is_clickhouse_enabled(),
                "connected": False,
            },
        }

        try:
            if is_clickhouse_enabled():
                status["clickhouse"]["connected"] = self.ch_client.ping()
        except Exception as e:
            status["clickhouse"]["error"] = str(e)

        return status
