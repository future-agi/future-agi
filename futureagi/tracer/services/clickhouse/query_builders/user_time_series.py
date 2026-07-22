"""
User Aggregate Time-Series Query Builder for ClickHouse.

Returns the same metric keys as the trace TimeSeriesQueryBuilder
(latency, tokens, cost, traffic, error_rate, etc.) but aggregated
at the user level:

1. Inner query: per-user per-trace aggregates.
2. Middle query: per-user per-time-bucket aggregates.
3. Outer query: across-user aggregates per time bucket.
"""

from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


class UserTimeSeriesQueryBuilder(BaseQueryBuilder):
    """Build time-series queries for user-level aggregate metrics.

    Returns all standard metric keys: latency, tokens, cost, traffic,
    error_rate, plus user-specific: active_users, avg_cost_per_user,
    avg_traces_per_user.
    """

    TABLE = "spans"

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
        extra_where, extra_params = filter_builder.translate(self.filters)
        self.params.update(extra_params)

        where_clause = extra_where if extra_where else "1 = 1"
        bucket_fn = self.time_bucket_expr(self.interval)

        # P3b step1.5 id-remap resolution (see id_remap_sql / DESIGN §3): resolve
        # a NEW-id span back to its OLD curated id BEFORE the per-(user, trace)
        # group, so a straddler's old + new spans aggregate as ONE user. Pre-flip
        # NO span matches a `new_id`, so this is a byte-identical no-op (gate B).
        remap_join = remap_left_join("rs.end_user_id", "end_user_id_remap")
        resolved_eu = resolved_id_expr("rs.end_user_id")

        query = f"""
        SELECT
            time_bucket,
            -- Standard trace-compatible metrics (aggregated at user level)
            avg(user_avg_latency) AS avg_latency,
            sum(user_total_tokens) AS total_tokens,
            avg(user_total_cost) AS avg_cost,
            count() AS traffic_count,
            sum(user_prompt_tokens) AS prompt_tokens,
            sum(user_completion_tokens) AS completion_tokens,
            countIf(user_has_error = 1) * 100.0
                / greatest(count(), 1) AS error_rate,
            -- User-specific metrics
            uniqExact(end_user_id) AS active_users,
            sum(user_total_cost) AS total_cost_sum,
            avg(user_total_cost) AS avg_cost_per_user,
            avg(user_traces) AS avg_traces_per_user,
            sum(user_total_tokens) AS total_tokens_sum
        FROM (
            SELECT
                {bucket_fn}(min_start) AS time_bucket,
                end_user_id,
                avg(span_avg_latency) AS user_avg_latency,
                sum(span_total_tokens) AS user_total_tokens,
                sum(span_total_cost) AS user_total_cost,
                sum(span_prompt_tokens) AS user_prompt_tokens,
                sum(span_completion_tokens) AS user_completion_tokens,
                max(span_has_error) AS user_has_error,
                count() AS user_traces
            FROM (
                SELECT
                    {resolved_eu} AS end_user_id,
                    rs.trace_id AS trace_id,
                    min(rs.start_time) AS min_start,
                    avg(rs.latency_ms) AS span_avg_latency,
                    sum(rs.total_tokens) AS span_total_tokens,
                    sum(rs.cost) AS span_total_cost,
                    sum(rs.prompt_tokens) AS span_prompt_tokens,
                    sum(rs.completion_tokens) AS span_completion_tokens,
                    max(if(rs.status = 'ERROR', 1, 0)) AS span_has_error
                FROM (
                    SELECT
                        end_user_id,
                        trace_id,
                        start_time,
                        latency_ms,
                        total_tokens,
                        cost,
                        prompt_tokens,
                        completion_tokens,
                        status
                    FROM {self.TABLE}
                    {self.project_where()}
                      AND start_time >= %(start_date)s
                      AND start_time < %(end_date)s
                      AND end_user_id IS NOT NULL
                      AND {where_clause}
                ) AS rs
                {remap_join}
                GROUP BY end_user_id, trace_id
            )
            GROUP BY time_bucket, end_user_id
        )
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def format_result(
        self,
        rows: list[tuple],
        columns: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Post-process ClickHouse rows into the standard response dict."""
        assert self.start_date is not None and self.end_date is not None

        def _get(r, key, idx, default=0):
            if isinstance(r, dict):
                return r.get(key, default)
            return r[idx] if len(r) > idx else default

        def _build(key, val_keys=None):
            if val_keys is None:
                val_keys = ["value"]
            return self.format_time_series(
                rows=[(_get(r, "time_bucket", 0), _get(r, key, 0)) for r in rows],
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

        # User-specific
        active_users_data = _build("active_users", ["value"])
        total_cost_data = _build("total_cost_sum", ["value"])
        avg_cost_data = _build("avg_cost_per_user", ["value"])
        avg_traces_data = _build("avg_traces_per_user", ["value"])

        return {
            # Standard
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
            # User-specific
            "active_users": active_users_data,
            "total_cost": total_cost_data,
            "avg_cost_per_user": avg_cost_data,
            "avg_traces_per_user": avg_traces_data,
        }
