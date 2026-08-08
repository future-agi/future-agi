"""Direct-write CH25 user time-series builders with exact latest-row replay."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.user_time_series import (
    UserTimeSeriesQueryBuilder,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


def _latest_start_time_spans_cte(*, table: str, project_predicate: str) -> str:
    """Replay one latest physical row per bounded direct-write span identity."""

    return f"""
    latest_spans AS (
        SELECT *
        FROM (
            SELECT *
            FROM {table}
            PREWHERE {project_predicate}
              AND toDate(start_time) BETWEEN
                  toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
            ORDER BY project_id, trace_id, id, start_time, _version DESC
            LIMIT 1 BY project_id, trace_id, id, start_time
        )
        WHERE is_deleted = 0
    )
    """


def _entity_safe_latest_spans_ctes(*, table: str, project_predicate: str) -> str:
    """Hydrate complete traces whose earliest span belongs to this partition."""

    return f"""
    candidate_trace_ids AS (
        SELECT trace_id
        FROM {table} FINAL
        PREWHERE {project_predicate}
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
          AND trace_id IN (
              SELECT DISTINCT trace_id
              FROM {table} FINAL
              PREWHERE {project_predicate}
                AND start_time >= %(start_date)s
                AND start_time < %(end_date)s
              WHERE is_deleted = 0
          )
        WHERE is_deleted = 0
        GROUP BY trace_id
        HAVING min(start_time) >= %(start_date)s
           AND min(start_time) < %(end_date)s
    ),
    latest_spans AS (
        SELECT *
        FROM {table} FINAL
        PREWHERE {project_predicate}
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
          AND trace_id IN (SELECT trace_id FROM candidate_trace_ids)
        WHERE is_deleted = 0
    )
    """


class UserTimeSeriesQueryBuilderV2(V2RewriteMixin, UserTimeSeriesQueryBuilder):
    """Aggregate user graphs from each direct-write span's latest live row."""

    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2
    END_USER_REMAP_TABLE = "end_user_id_remap"

    def __init__(
        self,
        *args: Any,
        user_membership_sql: str | None = None,
        user_membership_params: dict[str, Any] | None = None,
        exact_snapshot_start: datetime | None = None,
        exact_snapshot_end: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        self.user_membership_sql = user_membership_sql
        self.user_membership_params = dict(user_membership_params or {})
        if (exact_snapshot_start is None) != (exact_snapshot_end is None):
            raise ValueError("both exact snapshot bounds are required")
        self.exact_snapshot_start = exact_snapshot_start
        self.exact_snapshot_end = exact_snapshot_end
        super().__init__(*args, **kwargs)

    def build(self) -> tuple[str, dict[str, Any]]:
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date
        if (
            self.exact_snapshot_start is not None
            and self.exact_snapshot_end is not None
        ):
            self.params["snapshot_start_date"] = self.exact_snapshot_start
            self.params["snapshot_end_date"] = self.exact_snapshot_end

        if self.user_membership_sql:
            # User-list fields (num_traces, num_sessions, total_cost, etc.) are
            # full-window entity aggregates. They must be compiled once by the
            # shared exact user selector, never reinterpreted here as raw span
            # columns/attributes. The selector returns canonical remap-resolved
            # user IDs and this query only reduces contributions from them.
            self.params.update(self.user_membership_params)
            where_clause = (
                f"{resolved_id_expr('rs.end_user_id')} IN ({self.user_membership_sql})"
            )
        else:
            # Compile every outer and relational filter against the replayed
            # CTE. In particular, trace-membership subqueries now read
            # latest_spans too, so an older matching physical version cannot
            # revive a corrected or tombstoned span.
            filter_builder = self._FILTER_BUILDER_CLS(
                table="latest_spans",
                project_id=self.project_id,
                span_date_scope=True,
                strict_trace_project_correlation=True,
            )
            extra_where, extra_params = filter_builder.translate(self.filters)
            self.params.update(extra_params)
            where_clause = extra_where if extra_where else "1 = 1"
        bucket_fn = self.time_bucket_expr(self.interval)

        latest_spans_cte = (
            _entity_safe_latest_spans_ctes(
                table=self.TABLE,
                project_predicate=self.project_filter_sql(),
            )
            if self.exact_snapshot_start is not None
            else _latest_start_time_spans_cte(
                table=self.TABLE,
                project_predicate=self.project_filter_sql(),
            )
        )
        remap_join = remap_left_join(
            "rs.end_user_id",
            self.END_USER_REMAP_TABLE,
        )
        resolved_eu = resolved_id_expr("rs.end_user_id")

        query = f"""
        WITH {latest_spans_cte}
        SELECT
            time_bucket,
            avg(user_avg_latency) AS avg_latency,
            sum(user_total_tokens) AS total_tokens,
            avg(user_total_cost) AS avg_cost,
            count() AS traffic_count,
            sum(user_prompt_tokens) AS prompt_tokens,
            sum(user_completion_tokens) AS completion_tokens,
            countIf(user_has_error = 1) * 100.0
                / greatest(count(), 1) AS error_rate,
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
                FROM latest_spans AS rs
                {remap_join}
                WHERE rs.end_user_id IS NOT NULL
                  AND {where_clause}
                GROUP BY end_user_id, trace_id
            )
            GROUP BY time_bucket, end_user_id
        )
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params


class UserDetailTimeSeriesQueryBuilderV2(V2RewriteMixin, BaseQueryBuilder):
    """Build one curated user's exact direct-write usage graph."""

    TABLE = "spans"
    END_USERS_TABLE = "end_users"
    END_USER_REMAP_TABLE = "end_user_id_remap"
    TRACE_SESSION_REMAP_TABLE = "trace_session_id_remap"
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    def __init__(
        self,
        *,
        project_id: str,
        organization_id: str,
        end_user_id: str,
        filters: list[dict] | None = None,
        interval: str = "day",
    ) -> None:
        super().__init__(project_id)
        self.organization_id = organization_id
        self.end_user_id = end_user_id
        self.filters = filters or []
        self.interval = interval
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None

    def build(self) -> tuple[str, dict[str, Any]]:
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params.update(
            {
                "org_id": self.organization_id,
                "end_user_id": self.end_user_id,
                "start_date": self.start_date,
                "end_date": self.end_date,
            }
        )
        bucket_fn = self.time_bucket_expr(self.interval)

        filter_builder = self._FILTER_BUILDER_CLS(
            table="latest_spans",
            project_id=self.project_id,
            query_mode=self._FILTER_BUILDER_CLS.QUERY_MODE_SPAN,
            span_date_scope=True,
        )
        extra_where, extra_params = filter_builder.translate(self.filters)
        self.params.update(extra_params)
        where_clause = extra_where if extra_where else "1 = 1"

        latest_spans_ctes = _latest_start_time_spans_cte(
            table=self.TABLE,
            project_predicate=self.project_filter_sql(),
        )
        eu_remap_join = remap_left_join(
            "rs.end_user_id",
            self.END_USER_REMAP_TABLE,
            "eu_remap",
        )
        ts_remap_join = remap_left_join(
            "rs.trace_session_id",
            self.TRACE_SESSION_REMAP_TABLE,
            "ts_remap",
        )
        eu_resolved = resolved_id_expr("rs.end_user_id", "eu_remap")
        ts_resolved = resolved_id_expr("rs.trace_session_id", "ts_remap")

        query = f"""
        WITH
        {latest_spans_ctes},
        target_end_user AS (
            SELECT end_user_id
            FROM (
                SELECT
                    end_user_id,
                    argMax(organization_id, version) AS latest_organization_id,
                    argMax(project_id, version) AS latest_project_id,
                    argMax(is_deleted, version) AS latest_is_deleted
                FROM {self.END_USERS_TABLE}
                PREWHERE project_id = %(project_id)s
                  AND end_user_id = toUUID(%(end_user_id)s)
                GROUP BY end_user_id
            )
            WHERE latest_organization_id = toUUID(%(org_id)s)
              AND latest_project_id = toUUID(%(project_id)s)
              AND latest_is_deleted = 0
        )
        SELECT
            {bucket_fn}(start_time) AS time_bucket,
            uniqExactIf(
                toString(trace_session_id),
                isNotNull(trace_session_id)
            ) AS session_count,
            uniqExact(trace_id) AS trace_count,
            sum(ifNull(cost, 0)) AS cost,
            sum(ifNull(prompt_tokens, 0)) AS input_tokens,
            sum(ifNull(completion_tokens, 0)) AS output_tokens
        FROM (
            SELECT
                {eu_resolved} AS end_user_id,
                rs.trace_id AS trace_id,
                {ts_resolved} AS trace_session_id,
                rs.start_time AS start_time,
                rs.cost AS cost,
                rs.prompt_tokens AS prompt_tokens,
                rs.completion_tokens AS completion_tokens
            FROM latest_spans AS rs
            {eu_remap_join}
            {ts_remap_join}
            WHERE {where_clause}
        )
        WHERE end_user_id IN (SELECT end_user_id FROM target_end_user)
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params


__all__ = [
    "UserDetailTimeSeriesQueryBuilderV2",
    "UserTimeSeriesQueryBuilderV2",
]
