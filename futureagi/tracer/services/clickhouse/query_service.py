"""
Analytics Query Service.

ClickHouse is the single source of truth for the analytics paths in this
module; the per-query-type routing toggle (`CH_ROUTE_*`) and PG fallback
were removed in the CH25 migration close-out (2026-05-26). The CH25 read
endpoints assume CH is reachable; if it's down, the request fails loudly.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.read_budget import is_read_budget_error

logger = structlog.get_logger(__name__)

# This attribute is part of the verified trace-root contract and is also
# maintained by ``dashboard_attr_rollup``. A bounded key sample is intentionally
# incomplete, so keep the small, schema-supported set visible even when no
# sampled row happens to carry it.  Values remain ClickHouse-only; this is only
# picker metadata.
GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES: dict[str, str] = {
    "final_status": "string",
}


class SpanAttributeKeyInventory(list):
    """List-compatible bounded attribute discovery result.

    Existing callers consume this helper as ``list[dict]``.  Keeping the list
    interface avoids a breaking response change while allowing picker callers
    to distinguish an intentional sample from a failed ClickHouse read.
    """

    def __init__(
        self,
        rows,
        *,
        query_status: str,
        query_error_code: str | None,
        query_sampled: bool,
    ):
        super().__init__(rows)
        self.query_complete = query_status == "complete"
        self.query_status = query_status
        self.query_error_code = query_error_code
        self.query_sampled = query_sampled


def merge_guaranteed_span_attribute_keys(
    rows: list[dict | str] | None, *, include_counts: bool = False
) -> list[dict]:
    """Append guaranteed root attributes without inventing occurrence counts."""
    merged = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("key"):
            merged.append(dict(row))
        elif isinstance(row, str) and row:
            # Legacy/test callers can still supply the pre-typed bare-key
            # shape. Preserve the key without allowing it to be stringified
            # into an eval mapping path; production typed discovery returns
            # dictionaries.
            merged.append({"key": row, "type": "string"})
    seen = {str(row["key"]) for row in merged}
    for key, attribute_type in GUARANTEED_ROOT_SPAN_ATTRIBUTE_TYPES.items():
        if key in seen:
            continue
        row = {"key": key, "type": attribute_type}
        merged.append(row)
    return merged


_BOUNDED_READ_SETTINGS = {
    "max_threads": 2,
    "max_memory_usage": 256 * 1024 * 1024,
    "max_bytes_to_read": 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_result_rows": 10_000,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

# Attribute inventories are suggestions, not an exhaustive schema read.  A
# 10k-row sample exceeded the 256 MiB read budget on wide-map projects before
# ClickHouse could return any keys.  Exact-key lookup remains available for a
# known rare key, and guaranteed/saved picker paths are merged by callers, so a
# smaller honest sample improves availability without claiming completeness.
_SPAN_ATTRIBUTE_DISCOVERY_SAMPLE_ROWS = 1000


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


class AnalyticsQueryService:
    """ClickHouse query dispatcher for the analytics endpoints."""

    def __init__(self):
        self._ch_client: ClickHouseClient | None = None

    @property
    def ch_client(self) -> ClickHouseClient:
        if self._ch_client is None:
            self._ch_client = get_clickhouse_client()
        return self._ch_client

    def should_use_clickhouse(self, query_type: QueryType | str) -> bool:
        """Compatibility shim for legacy route-toggle callers/tests."""
        return is_clickhouse_enabled()

    def execute_ch_query(
        self,
        query: str,
        params: dict = None,
        timeout_ms: int = 10000,
        settings: dict | None = None,
    ) -> QueryResult:
        """Execute a query on ClickHouse and return QueryResult."""
        start = time.monotonic()
        rows, columns, qt = self.ch_client.execute_read(
            query, params or {}, timeout_ms=timeout_ms, settings=settings
        )
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
        recent_days: int | None = 7,
        timeout_ms: int = 750,
        outer_limit: int = 1000,
        include_counts: bool = False,
        order_by_count_desc: bool = False,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[dict]:
        """Get distinct span attribute keys with types for one or more projects."""
        if not project_ids:
            return []

        recent_filter = ""
        params: dict[str, Any] = {
            "project_ids": tuple(project_ids),
        }
        if (window_start is None) != (window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if window_start is not None and window_end is not None:
            params["window_start"] = window_start
            params["window_end"] = window_end
            recent_filter = (
                "AND start_time >= %(window_start)s AND start_time < %(window_end)s"
            )
        elif recent_days is not None:
            params["recent_days"] = int(recent_days)
            recent_filter = "AND start_time >= now() - toIntervalDay(%(recent_days)s)"

        outer_select = "SELECT key, argMax(type, cnt) AS type"
        if include_counts:
            outer_select += ", sum(cnt) AS count"
        outer_order = (
            "ORDER BY count DESC, key" if order_by_count_desc else "ORDER BY key"
        )

        query = f"""
            {outer_select} FROM (
                SELECT key, 'string' AS type, count() AS cnt FROM (
                    SELECT attrs_string.keys AS ks FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    LIMIT {_SPAN_ATTRIBUTE_DISCOVERY_SAMPLE_ROWS}
                ) ARRAY JOIN ks AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'number' AS type, count() AS cnt FROM (
                    SELECT attrs_number.keys AS ks FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    LIMIT {_SPAN_ATTRIBUTE_DISCOVERY_SAMPLE_ROWS}
                ) ARRAY JOIN ks AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'boolean' AS type, count() AS cnt FROM (
                    SELECT attrs_bool.keys AS ks FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    LIMIT {_SPAN_ATTRIBUTE_DISCOVERY_SAMPLE_ROWS}
                ) ARRAY JOIN ks AS key
                GROUP BY key
            )
            GROUP BY key
            {outer_order}
            LIMIT {int(outer_limit)}
        """
        result = self.execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings=_BOUNDED_READ_SETTINGS,
        )
        if include_counts:
            rows = [
                {"key": row["key"], "type": row["type"], "count": row["count"]}
                for row in result.data
            ]
        else:
            rows = [{"key": row["key"], "type": row["type"]} for row in result.data]
        return merge_guaranteed_span_attribute_keys(rows, include_counts=include_counts)

    def find_span_attribute_key_ch_for_project(
        self,
        project_id: str,
        key: str,
        *,
        window_start: datetime,
        window_end: datetime,
        timeout_ms: int = 750,
    ) -> dict | None:
        """Return the type of one exact attribute key in a bounded project slice.

        A seven-day ``FINAL`` Map probe can exceed the per-query memory budget
        even after ClickHouse streams a matching row. Instead, first collect a
        tiny non-FINAL candidate set using the Map-key bloom indexes, then
        verify latest-state semantics only in each candidate's five-minute
        slice. Every read shares one wall-clock deadline. Exceptions always
        propagate, so a driver's partial rows can never become a false success.
        """
        project_id = str(project_id or "").strip()
        key = str(key or "").strip()
        if not project_id or not key:
            return None
        if window_start is None or window_end is None:
            raise ValueError("window_start and window_end are required")
        if window_start >= window_end:
            raise ValueError("window_start must be before window_end")

        deadline = time.monotonic() + (timeout_ms / 1000)

        def _remaining_timeout_ms(*, cap_ms: int | None = None) -> int:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining < 25:
                raise TimeoutError("Exact attribute probe exceeded its read deadline")
            return min(remaining, cap_ms) if cap_ms is not None else remaining

        # Fetch one more than the verification cap. If every capped candidate
        # turns out to be an obsolete version, absence is not proven and the
        # endpoint must report an incomplete read rather than "not found".
        candidate_cap = 16
        candidate_limit = candidate_cap + 1
        candidate_query = """
            SELECT toString(id) AS id, start_time
            FROM spans
            PREWHERE project_id = %(project_id)s
              AND start_time >= %(window_start)s
              AND start_time < %(window_end)s
            WHERE is_deleted = 0
              AND (
                mapContains(attrs_string, %(key)s)
                OR mapContains(attrs_number, %(key)s)
                OR mapContains(attrs_bool, %(key)s)
              )
            LIMIT %(candidate_limit)s
        """
        candidate_settings = {
            **_BOUNDED_READ_SETTINGS,
            "max_result_rows": candidate_limit,
        }
        candidate_result = self.execute_ch_query(
            candidate_query,
            {
                "project_id": project_id,
                "key": key,
                "window_start": window_start,
                "window_end": window_end,
                "candidate_limit": candidate_limit,
            },
            timeout_ms=_remaining_timeout_ms(cap_ms=250),
            settings=candidate_settings,
        )
        if not candidate_result.data:
            return None

        # Group candidates into narrow latest-state reads. ``start_time`` is
        # immutable across versions, so the physical candidate identifies the
        # complete ReplacingMergeTree slice that needs FINAL verification.
        candidate_slices: dict[datetime, set[str]] = {}
        for row in candidate_result.data[:candidate_cap]:
            candidate_id = str(row.get("id") or "").strip()
            candidate_start = row.get("start_time")
            if not candidate_id or not isinstance(candidate_start, datetime):
                raise TimeoutError(
                    "Exact attribute probe returned unverifiable candidates"
                )
            if candidate_start.tzinfo is None and window_start.tzinfo is not None:
                candidate_start = candidate_start.replace(tzinfo=window_start.tzinfo)
            slice_start = candidate_start.replace(
                minute=(candidate_start.minute // 5) * 5,
                second=0,
                microsecond=0,
            )
            candidate_slices.setdefault(slice_start, set()).add(candidate_id)

        verify_query = """
            SELECT multiIf(
                mapContains(attrs_string, %(key)s), 'string',
                mapContains(attrs_number, %(key)s), 'number',
                'boolean'
            ) AS type
            FROM spans FINAL
            PREWHERE project_id = %(project_id)s
              AND start_time >= %(slice_start)s
              AND start_time < %(slice_end)s
            WHERE id IN %(candidate_ids)s
              AND is_deleted = 0
              AND (
                mapContains(attrs_string, %(key)s)
                OR mapContains(attrs_number, %(key)s)
                OR mapContains(attrs_bool, %(key)s)
              )
            LIMIT 1
        """
        verify_settings = {
            **_BOUNDED_READ_SETTINGS,
            "max_result_rows": 1,
            # Map-key skip indexes are evaluated on physical versions.  When
            # merges are stopped, enabling them with FINAL can prune the newer
            # version that removed a key and resurrect the obsolete value.
            # Exact discovery must prefer latest-state correctness over that
            # unsafe optimisation; the five-minute/id candidate scope keeps
            # this verification bounded without it.
            "use_skip_indexes_if_final": 0,
        }
        for slice_start in sorted(candidate_slices, reverse=True):
            verify_result = self.execute_ch_query(
                verify_query,
                {
                    "project_id": project_id,
                    "key": key,
                    "slice_start": slice_start,
                    "slice_end": slice_start + timedelta(minutes=5),
                    "candidate_ids": tuple(sorted(candidate_slices[slice_start])),
                },
                timeout_ms=_remaining_timeout_ms(cap_ms=250),
                settings=verify_settings,
            )
            if not verify_result.data:
                continue
            attribute_type = verify_result.data[0].get("type")
            if attribute_type not in {"string", "number", "boolean"}:
                raise TimeoutError(
                    "Exact attribute probe returned an invalid verified type"
                )
            return {"key": key, "type": attribute_type}

        if len(candidate_result.data) >= candidate_limit:
            raise TimeoutError(
                "Exact attribute probe candidate cap reached before proof"
            )
        return None

    def get_span_attribute_keys_ch(self, project_id: str) -> SpanAttributeKeyInventory:
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
        #   * a small LIMIT inside each per-map subquery before the
        #     ARRAY JOIN — without this, projects with millions of spans
        #     and wide `attrs_*` maps hit Code: 307 (max_bytes_to_read)
        #     because every row's Map gets exploded.
        try:
            rows = self.get_span_attribute_keys_ch_for_projects([project_id])
            return SpanAttributeKeyInventory(
                rows,
                query_status="sampled",
                query_error_code="sample_limit",
                query_sampled=True,
            )
        except Exception as exc:
            # Eval-task mapping must keep the supported root attributes usable
            # during a transient/budgeted discovery failure.  Do not invent
            # arbitrary keys: this fallback is limited to the rollup-backed
            # contract above.
            logger.warning(
                "span_attribute_key_discovery_degraded",
                project_id=str(project_id),
                error=str(exc)[:200],
            )
            return SpanAttributeKeyInventory(
                merge_guaranteed_span_attribute_keys([]),
                query_status="degraded",
                query_error_code=(
                    "read_budget_exceeded"
                    if is_read_budget_error(exc)
                    else "query_failed"
                ),
                query_sampled=False,
            )

    @staticmethod
    def _eval_config_ids_query(scope_sql: str, extra_where: str = "") -> str:
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
        eval_table, eval_nd = eval_logger_source()
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
        timeout_ms: int = 750,
        window_days: int | None = 30,
        candidate_config_ids: list[str] | None = None,
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
        eval_table, eval_nd = eval_logger_source()
        params: dict[str, Any] = {}
        window_sql = ""
        if window_days is not None:
            params["window_days"] = int(window_days)
            window_sql = "AND created_at >= now() - toIntervalDay(%(window_days)s)"

        if candidate_config_ids is not None:
            if not candidate_config_ids:
                return []
            params["config_ids"] = tuple(candidate_config_ids)
            query = (
                "SELECT DISTINCT toString(custom_eval_config_id) AS config_id "
                f"FROM {eval_table} "
                f"WHERE {eval_nd} {window_sql} "
                "AND custom_eval_config_id IN %(config_ids)s"
            )
            result = self.execute_ch_query(
                query,
                params,
                timeout_ms=timeout_ms,
                settings=_BOUNDED_READ_SETTINGS,
            )
            return [row["config_id"] for row in result.data]

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
        )
        result = self.execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings={
                **_BOUNDED_READ_SETTINGS,
                "max_bytes_in_set": 256 * 1024 * 1024,
            },
        )
        return [row["config_id"] for row in result.data]

    def get_eval_config_ids_for_traces_ch(
        self, trace_ids: list[str], timeout_ms: int = 750
    ) -> list[str]:
        """Distinct eval config IDs recorded for an explicit set of trace IDs."""
        if not trace_ids:
            return []
        query = self._eval_config_ids_query("trace_id IN %(trace_ids)s")
        result = self.execute_ch_query(
            query,
            {"trace_ids": trace_ids},
            timeout_ms=timeout_ms,
            settings=_BOUNDED_READ_SETTINGS,
        )
        return [row["config_id"] for row in result.data]

    def get_span_trace_map(
        self,
        trace_ids: list[str],
        project_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        timeout_ms: int = 750,
    ) -> dict[str, str]:
        """Map span id -> trace id for spans in the given traces (CH-native).

        ``project_id`` prunes the scan to the partition/PK prefix; the
        ``start_date``/``end_date`` window (widened one day each side to cover a
        trace's full duration) prunes partitions. Refuse a call that supplies
        neither a project nor a complete time window: that exact shape was the
        most frequent slow-success query in the US 14-day audit and repeatedly
        scanned the entire spans table.
        """
        if not trace_ids:
            return {}
        if project_id is None and (start_date is None or end_date is None):
            logger.warning(
                "span_trace_map_missing_read_scope",
                trace_count=len(trace_ids),
            )
            return {}
        params: dict[str, Any] = {"trace_ids": trace_ids}
        where = ["trace_id IN %(trace_ids)s", "is_deleted = 0"]
        if project_id is not None:
            params["project_id"] = project_id
            where.append("project_id = %(project_id)s")
        if start_date is not None and end_date is not None:
            params["start_date"] = start_date
            params["end_date"] = end_date
            where.append(
                "start_time >= %(start_date)s - INTERVAL 1 DAY "
                "AND start_time < %(end_date)s + INTERVAL 1 DAY"
            )
        result = self.execute_ch_query(
            "SELECT toString(id) AS span_id, toString(trace_id) AS trace_id "
            f"FROM spans WHERE {' AND '.join(where)}",
            params,
            timeout_ms=timeout_ms,
            settings={"max_threads": 2, "max_result_rows": 10_000},
        )
        return {r["span_id"]: r["trace_id"] for r in result.data}

    def get_children_eval_metrics_ch(
        self, span_ids: list[str], timeout_ms: int = 750
    ) -> list[dict]:
        """Per-span eval rows for a set of child observation spans."""
        if not span_ids:
            return []
        eval_table, eval_nd = eval_logger_source()
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
            FROM {eval_table} FINAL
            WHERE observation_span_id IN %(span_ids)s
              AND {eval_nd}
        """
        result = self.execute_ch_query(
            query,
            {"span_ids": span_ids},
            timeout_ms=timeout_ms,
            settings={"max_threads": 2, "max_result_rows": 10_000},
        )
        return result.data

    def get_eval_detail_ch(
        self, span_id: str, config_id: str, timeout_ms: int = 750
    ) -> dict | None:
        """Single span/trace-target eval detail row, or ``None`` if absent."""
        eval_table, eval_nd = eval_logger_source()
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
            FROM {eval_table} FINAL
            WHERE observation_span_id = %(span_id)s
              AND custom_eval_config_id = %(config_id)s
              AND target_type IN ('span', 'trace')
              AND {eval_nd}
            LIMIT 1
        """
        result = self.execute_ch_query(
            query,
            {"span_id": str(span_id), "config_id": str(config_id)},
            timeout_ms=timeout_ms,
            settings=_BOUNDED_READ_SETTINGS,
        )
        return result.data[0] if result.data else None

    def get_trace_eval_scores_ch(
        self, trace_ids: list[str], config_ids: list[str], timeout_ms: int = 750
    ) -> list[dict]:
        """Per-(trace, config) aggregated eval scores for a session's traces."""
        if not (trace_ids and config_ids):
            return []
        eval_table, eval_nd = eval_logger_source()
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
            FROM {eval_table} FINAL
            WHERE trace_id IN %(trace_ids)s
              AND custom_eval_config_id IN %(config_ids)s
              AND {eval_nd}
            GROUP BY trace_id, custom_eval_config_id
        """
        result = self.execute_ch_query(
            query,
            {"trace_ids": trace_ids, "config_ids": config_ids},
            timeout_ms=timeout_ms,
            settings=_BOUNDED_READ_SETTINGS,
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
