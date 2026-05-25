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
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class SessionTimeSeriesQueryBuilder(BaseQueryBuilder):
    """Build time-series queries for session-level metrics.

    Groups the ``spans`` table by ``trace_session_id`` into sessions,
    then re-aggregates sessions into time buckets.

    Returns all standard metric keys: latency, tokens, cost, traffic,
    error_rate, prompt_tokens, completion_tokens, plus session-specific:
    session_count, avg_duration, avg_traces_per_session.
    """

    TABLE = "spans"

    def __init__(
        self,
        project_id: str,
        filters: Optional[List[Dict]] = None,
        interval: str = "day",
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.filters = filters or []
        self.interval = interval
        self.start_date: Optional[datetime] = None
        self.end_date: Optional[datetime] = None

    def build(self) -> Tuple[str, Dict[str, Any]]:
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        filter_builder = ClickHouseFilterBuilder(table=self.TABLE)
        extra_where, extra_params = filter_builder.translate(self.filters)
        self.params.update(extra_params)

        where_clause = extra_where if extra_where else "1 = 1"
        bucket_fn = self.time_bucket_expr(self.interval)

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
                trace_session_id AS session_id,
                min(start_time) AS session_start,
                dateDiff('second', min(start_time), max(end_time))
                    AS session_duration,
                avg(latency_ms) AS session_avg_latency,
                sum(cost) AS session_total_cost,
                sum(total_tokens) AS session_total_tokens,
                sum(prompt_tokens) AS session_prompt_tokens,
                sum(completion_tokens) AS session_completion_tokens,
                uniqExact(trace_id) AS session_traces,
                max(if(status = 'ERROR', 1, 0)) AS session_has_error
            FROM {self.TABLE}
            {self.project_where()}
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND trace_session_id IS NOT NULL
              AND trace_session_id != toUUID('{NIL_UUID}')
              AND {where_clause}
            GROUP BY trace_session_id
        )
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def format_result(
        self,
        rows: List[Tuple],
        columns: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
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
