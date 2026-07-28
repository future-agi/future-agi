"""
Session Time-Series Query Builder for ClickHouse.

Returns the same metric keys as the trace TimeSeriesQueryBuilder
(latency, tokens, cost, traffic, error_rate, etc.) but aggregated
at the session level:

1. Inner query: per-session aggregates (avg latency, total tokens,
   total cost, has_error, traces count, duration).
2. Outer query: per-time-bucket aggregates across sessions.

This ensures the PrimaryGraph metric dropdown works identically
for sessions as it does for traces — same metric IDs, same response
shape — but the numbers reflect session-level aggregation.
"""

from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.session_filters import (
    SESSION_ID_FILTER_COLS,
    build_session_id_filter_clause,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


class SessionTimeSeriesQueryBuilder(BaseQueryBuilder):
    """Build time-series queries for session-level metrics.

    Groups the ``spans`` table by ``trace_session_id`` into sessions,
    then re-aggregates sessions into time buckets.

    Returns all standard metric keys: latency, tokens, cost, traffic,
    error_rate, prompt_tokens, completion_tokens, plus session-specific:
    session_count, avg_duration, avg_traces_per_session.
    """

    TABLE = "spans"
    SESSION_FILTER_MAP: dict[str, str] = {
        "duration": "session_duration",
        "total_cost": "session_total_cost",
        "total_tokens": "session_total_tokens",
        "traces_count": "session_traces",
        "total_traces_count": "session_traces",
    }
    SESSION_ID_FILTER_COLS = SESSION_ID_FILTER_COLS

    def __init__(
        self,
        project_id: str,
        filters: list[dict] | None = None,
        interval: str = "day",
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.filters = filters or []
        self.interval = interval
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None

    def build(self) -> tuple[str, dict[str, Any]]:
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        filter_builder = ClickHouseFilterBuilder(table=self.TABLE)
        span_filters = self._extract_span_filters()
        extra_where, extra_params = filter_builder.translate(span_filters)
        self.params.update(extra_params)
        having_clauses = self._build_having_clauses()

        where_clause = extra_where if extra_where else "1 = 1"
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        bucket_fn = self.time_bucket_expr(self.interval)

        # P3b step1.5 (DESIGN §3 / id_remap_sql): this builder GROUPs the spans
        # into sessions by `trace_session_id`, then re-buckets sessions by time.
        # A cross-cutover straddler (old-id spans + new deterministic-id spans for
        # ONE session) would split into TWO inner session rows — double-counting
        # `session_count`/`avg_*` and halving each session's metrics. Resolve each
        # span's `trace_session_id` new→old through `trace_session_id_remap` and
        # GROUP the inner per-session aggregate on the RESOLVED id, so old + new
        # spans form ONE session. The time/project/null predicates + the span
        # `{where_clause}` stay on the bare inner scan; the grouping key and
        # session-ID filters bind the resolved column. `resolved_id_expr` is the
        # zero-uuid-guarded map (NOT a COALESCE). Pre-flip NO span matches a
        # `new_id` → resolved id == own id → byte-identical no-op (gate B).
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")
        session_id_clause = self._build_session_id_clause(resolved_ts)
        session_id_fragment = (
            f"WHERE {session_id_clause}" if session_id_clause else ""
        )

        # Two-level aggregation:
        # Inner: per-session aggregates from ALL spans in the session
        # Outer: per-time-bucket aggregates across sessions
        query = f"""
        SELECT
            {bucket_fn}(session_start) AS time_bucket,
            -- Standard trace-compatible metrics (aggregated at session level)
            avg(session_avg_latency) AS avg_latency,
            sum(session_total_tokens) AS total_tokens,
            avg(session_total_cost) AS avg_cost,
            count() AS traffic_count,
            sum(session_prompt_tokens) AS prompt_tokens,
            sum(session_completion_tokens) AS completion_tokens,
            countIf(session_has_error = 1) * 100.0
                / greatest(count(), 1) AS error_rate,
            -- Session-specific metrics
            uniqExact(session_id) AS session_count,
            avg(session_duration) AS avg_duration,
            avg(session_traces) AS avg_traces_per_session,
            sum(session_total_cost) AS total_cost_sum
        FROM (
            SELECT
                {resolved_ts} AS session_id,
                min(rs.start_time) AS session_start,
                dateDiff('second', min(rs.start_time), max(rs.end_time))
                    AS session_duration,
                avg(rs.latency_ms) AS session_avg_latency,
                sum(rs.cost) AS session_total_cost,
                sum(rs.total_tokens) AS session_total_tokens,
                sum(rs.prompt_tokens) AS session_prompt_tokens,
                sum(rs.completion_tokens) AS session_completion_tokens,
                uniqExact(rs.trace_id) AS session_traces,
                max(if(rs.status = 'ERROR', 1, 0)) AS session_has_error
            FROM (
                SELECT
                    trace_session_id, start_time, end_time, latency_ms, cost,
                    total_tokens, prompt_tokens, completion_tokens, trace_id, status
                FROM {self.TABLE}
                {self.project_where()}
                  AND start_time >= %(start_date)s
                  AND start_time < %(end_date)s
                  AND trace_session_id IS NOT NULL
                  AND trace_session_id != toUUID('{NIL_UUID}')
                  AND {where_clause}
            ) AS rs
            {ts_join}
            {session_id_fragment}
            GROUP BY session_id
            {having_fragment}
        )
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _extract_span_filters(self) -> list[dict]:
        """Return filters that apply before the session GROUP BY."""
        span_filters: list[dict] = []
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if (
                col_id not in self.SESSION_FILTER_MAP
                and col_id not in self.SESSION_ID_FILTER_COLS
            ):
                span_filters.append(f)
        return span_filters

    def _build_session_id_clause(self, resolved_session_id: str) -> str:
        """Filter the remap-resolved session ID before session aggregation.

        Applied inline (pre-GROUP BY), so it binds against the full
        ``resolved_id_expr(...)`` rather than a projected column alias.
        """
        return build_session_id_filter_clause(
            self.filters,
            self.params,
            session_col=resolved_session_id,
            param_prefix="session_id_",
        )

    def _build_having_clauses(self) -> str:
        """Build predicates for session aggregate filters."""
        conditions: list[str] = []
        param_counter = 900

        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in self.SESSION_FILTER_MAP:
                continue

            config = f.get("filter_config") or f.get("filterConfig") or {}
            filter_op = config.get("filter_op") or config.get("filterOp")
            filter_value = config.get("filter_value", config.get("filterValue"))
            ch_col = self.SESSION_FILTER_MAP[col_id]

            op_map = {
                "equals": "=",
                "not_equals": "!=",
                "greater_than": ">",
                "less_than": "<",
                "greater_than_or_equal": ">=",
                "less_than_or_equal": "<=",
            }
            op = op_map.get(filter_op)
            if op is None:
                conditions.append("0 = 1")
                continue

            param_counter += 1
            param_name = f"having_{param_counter}"
            self.params[param_name] = filter_value
            conditions.append(f"{ch_col} {op} %({param_name})s")

        return " AND ".join(conditions)

    def format_result(
        self,
        rows: list[tuple],
        columns: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Post-process ClickHouse rows into the standard response dict.

        Returns the same keys as TimeSeriesQueryBuilder (latency, tokens,
        cost, traffic, error_rate, etc.) plus session-specific keys.
        """
        assert self.start_date is not None and self.end_date is not None

        def _get(r, key, idx, default=0):
            if isinstance(r, dict):
                return r.get(key, default)
            return r[idx] if len(r) > idx else default

        def _build(key_or_idx, val_keys=None):
            """Helper to build a time-series for a given column."""
            if val_keys is None:
                val_keys = ["value"]
            idx = key_or_idx if isinstance(key_or_idx, int) else 0
            key = key_or_idx if isinstance(key_or_idx, str) else None
            return self.format_time_series(
                rows=[
                    (_get(r, "time_bucket", 0), _get(r, key or "", idx)) for r in rows
                ],
                columns=["time_bucket"] + val_keys,
                interval=self.interval,
                start_date=self.start_date,
                end_date=self.end_date,
                value_keys=val_keys,
            )

        # Standard trace-compatible metrics
        latency_data = _build("avg_latency", ["value", "latency"])
        tokens_data = _build("total_tokens", ["value", "tokens"])
        cost_data = _build("avg_cost", ["value", "cost"])
        traffic_data = _build("traffic_count", ["traffic"])
        prompt_tokens_data = _build("prompt_tokens", ["value"])
        completion_tokens_data = _build("completion_tokens", ["value"])
        error_rate_data = _build("error_rate", ["value"])

        # Session-specific metrics
        session_count_data = _build("session_count", ["value"])
        avg_duration_data = _build("avg_duration", ["value"])
        avg_traces_data = _build("avg_traces_per_session", ["value"])
        total_cost_sum_data = _build("total_cost_sum", ["value"])

        return {
            # Standard (same keys as TimeSeriesQueryBuilder)
            "latency": latency_data,
            "tokens": tokens_data,
            "cost": cost_data,
            "traffic": traffic_data,
            "prompt_tokens": prompt_tokens_data,
            "completion_tokens": completion_tokens_data,
            "input_tokens": prompt_tokens_data,
            "output_tokens": completion_tokens_data,
            "total_tokens": tokens_data,
            "error_rate": error_rate_data,
            # Session-specific
            "session_count": session_count_data,
            "avg_duration": avg_duration_data,
            "avg_traces_per_session": avg_traces_data,
            "total_cost": total_cost_sum_data,
        }
