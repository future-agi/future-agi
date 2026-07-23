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
from typing import Any

import structlog

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.eval_logger_table import eval_logger_source

logger = structlog.get_logger(__name__)


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
        timeout_ms: int = 10000,
        outer_limit: int = 1000,
        include_counts: bool = False,
        order_by_count_desc: bool = False,
    ) -> list[dict]:
        """Get distinct span attribute keys with types for one or more projects."""
        if not project_ids:
            return []

        recent_filter = ""
        params: dict[str, Any] = {
            "project_ids": tuple(project_ids),
        }
        if recent_days is not None:
            params["recent_days"] = int(recent_days)
            recent_filter = "AND start_time >= now() - toIntervalDay(%(recent_days)s)"

        inner_order = "ORDER BY start_time DESC" if recent_days is not None else ""
        outer_select = "SELECT key, argMax(type, cnt) AS type"
        if include_counts:
            outer_select += ", sum(cnt) AS count"
        outer_order = (
            "ORDER BY count DESC, key" if order_by_count_desc else "ORDER BY key"
        )

        query = f"""
            {outer_select} FROM (
                SELECT key, 'string' AS type, count() AS cnt FROM (
                    SELECT attrs_string FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    {inner_order}
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(attrs_string) AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'number' AS type, count() AS cnt FROM (
                    SELECT attrs_number FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    {inner_order}
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(attrs_number) AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'boolean' AS type, count() AS cnt FROM (
                    SELECT attrs_bool FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND is_deleted = 0
                      {recent_filter}
                    {inner_order}
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(attrs_bool) AS key
                GROUP BY key
            )
            GROUP BY key
            {outer_order}
            LIMIT {int(outer_limit)}
        """
        result = self.execute_ch_query(query, params, timeout_ms=timeout_ms)
        if include_counts:
            return [
                {"key": row["key"], "type": row["type"], "count": row["count"]}
                for row in result.data
            ]
        return [{"key": row["key"], "type": row["type"]} for row in result.data]

    def get_span_attribute_keys_ch(self, project_id: str) -> list[dict]:
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
        return self.get_span_attribute_keys_ch_for_projects([project_id])

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
        timeout_ms: int = 5000,
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
            result = self.execute_ch_query(query, params, timeout_ms=timeout_ms)
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
            settings={"max_bytes_in_set": 500_000_000},
        )
        return [row["config_id"] for row in result.data]

    def get_eval_config_ids_for_traces_ch(
        self, trace_ids: list[str], timeout_ms: int = 3000
    ) -> list[str]:
        """Distinct eval config IDs recorded for an explicit set of trace IDs."""
        if not trace_ids:
            return []
        query = self._eval_config_ids_query("trace_id IN %(trace_ids)s")
        result = self.execute_ch_query(
            query, {"trace_ids": trace_ids}, timeout_ms=timeout_ms
        )
        return [row["config_id"] for row in result.data]

    def get_span_trace_map(
        self,
        trace_ids: list[str],
        project_id: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        timeout_ms: int = 10000,
    ) -> dict[str, str]:
        """Map span id -> trace id for spans in the given traces (CH-native).

        ``project_id`` prunes the scan to the partition/PK prefix; the
        ``start_date``/``end_date`` window (widened one day each side to cover a
        trace's full duration) prunes partitions. Without them the query is a
        full-table scan.
        """
        if not trace_ids:
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
        )
        return {r["span_id"]: r["trace_id"] for r in result.data}

    def get_children_eval_metrics_ch(
        self, span_ids: list[str], timeout_ms: int = 5000
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
            query, {"span_ids": span_ids}, timeout_ms=timeout_ms
        )
        return result.data

    def get_eval_detail_ch(
        self, span_id: str, config_id: str, timeout_ms: int = 5000
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
        )
        return result.data[0] if result.data else None

    def get_trace_eval_scores_ch(
        self, trace_ids: list[str], config_ids: list[str], timeout_ms: int = 5000
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
