"""Exact direct-write session/user analytics over CH25 ``spans``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    resolved_id_expr,
    survivor_map_subquery,
)


class SessionAnalyticsQueryBuilderV2(BaseQueryBuilder):
    """Build bounded, latest-live analytics without legacy routing or PG spans."""

    TABLE = "spans"

    def __init__(
        self,
        project_id: str,
        *,
        filters: list[dict] | None = None,
        end_user_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, **kwargs)
        self.filters = filters
        self.end_user_ids = tuple(str(value) for value in (end_user_ids or []))
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None
        if filters is not None:
            self.start_date, self.end_date = self.parse_time_range(
                filters,
                strict=True,
            )

    def build(self) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError(
            "Use a specific session/user analytics query method instead."
        )

    def _time_scope(self, params: dict[str, Any]) -> str:
        if self.start_date is None or self.end_date is None:
            return ""
        params["window_start"] = self.start_date
        params["window_end"] = self.end_date
        return (
            "\n              AND start_time >= %(window_start)s"
            "\n              AND start_time < %(window_end)s"
        )

    def build_session_metrics_query(
        self,
        session_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(value) for value in session_ids if value))
        if not ids:
            return "SELECT NULL WHERE 0", dict(self.params)

        params = {**self.params, "session_ids": ids}
        time_scope = self._time_scope(params)
        ts_map = survivor_map_subquery("trace_session_id_remap")
        resolved_session = resolved_id_expr("latest_trace_session_id", "ts_remap")
        query = f"""
        WITH
        ts_survivor_map AS ({ts_map}),
        candidate_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND (
                  trace_session_id IN %(session_ids)s
                  OR trace_session_id IN (
                      SELECT any_id FROM ts_survivor_map
                      WHERE survivor_id IN %(session_ids)s
                  )
              )
        ),
        latest_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(trace_session_id), _version).1
                    AS latest_trace_session_id,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(total_tokens, _version) AS latest_total_tokens,
                argMax(cost, _version) AS latest_cost,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_spans AS (
            SELECT
                {resolved_session} AS trace_session_id,
                trace_id,
                start_time,
                latest_end_time AS end_time,
                latest_total_tokens AS total_tokens,
                latest_cost AS cost
            FROM latest_spans
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_session} IN %(session_ids)s
        )
        SELECT
            trace_session_id,
            min(start_time) AS first_trace_time,
            max(start_time) AS last_trace_time,
            uniqExact(trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS started_at,
            max(coalesce(end_time, start_time)) AS ended_at
        FROM resolved_spans
        GROUP BY trace_session_id
        """
        return query, params

    def build_user_stats_query(self, user_id: str) -> tuple[str, dict[str, Any]]:
        params = {**self.params, "user_id": str(user_id)}
        time_scope = self._time_scope(params)
        eu_map = survivor_map_subquery("end_user_id_remap")
        ts_map = survivor_map_subquery("trace_session_id_remap")
        resolved_user = resolved_id_expr("latest_end_user_id", "eu_remap")
        resolved_session = resolved_id_expr("latest_trace_session_id", "ts_remap")
        query = f"""
        WITH
        eu_survivor_map AS ({eu_map}),
        ts_survivor_map AS ({ts_map}),
        candidate_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND (
                  end_user_id = %(user_id)s
                  OR end_user_id IN (
                      SELECT any_id FROM eu_survivor_map
                      WHERE survivor_id = %(user_id)s
                  )
              )
        ),
        latest_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(tuple(trace_session_id), _version).1
                    AS latest_trace_session_id,
                argMax(total_tokens, _version) AS latest_total_tokens,
                argMax(cost, _version) AS latest_cost,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_spans AS (
            SELECT
                {resolved_user} AS end_user_id,
                {resolved_session} AS trace_session_id,
                start_time,
                latest_total_tokens AS total_tokens,
                latest_cost AS cost
            FROM latest_spans
            LEFT JOIN eu_survivor_map AS eu_remap
                ON latest_end_user_id = eu_remap.any_id
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            WHERE latest_is_deleted = 0
        )
        SELECT
            uniqExactIf(
                trace_session_id,
                isNotNull(trace_session_id)
                AND trace_session_id != toUUID('{NIL_UUID}')
            ) AS session_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            min(start_time) AS first_seen,
            max(start_time) AS last_seen
        FROM resolved_spans
        WHERE end_user_id = %(user_id)s
        """
        return query, params

    def build_session_navigation_query(self) -> tuple[str, dict[str, Any]]:
        """Return latest-live session aggregates and root messages in one read."""

        params = dict(self.params)
        time_scope = self._time_scope(params)
        ts_map = survivor_map_subquery("trace_session_id_remap")
        eu_map = survivor_map_subquery("end_user_id_remap")
        resolved_session = resolved_id_expr("latest_trace_session_id", "ts_remap")
        resolved_user = resolved_id_expr("latest_end_user_id", "eu_remap")
        user_predicate = ""
        if self.end_user_ids:
            params["end_user_ids"] = self.end_user_ids
            user_predicate = f"AND {resolved_user} IN %(end_user_ids)s"

        query = f"""
        WITH
        ts_survivor_map AS ({ts_map}),
        eu_survivor_map AS ({eu_map}),
        candidate_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND isNotNull(trace_session_id)
              AND trace_session_id != toUUID('{NIL_UUID}')
        ),
        latest_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(trace_session_id), _version).1
                    AS latest_trace_session_id,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(tuple(parent_span_id), _version).1 AS latest_parent_span_id,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(tuple(input), _version).1 AS latest_input,
                argMax(total_tokens, _version) AS latest_total_tokens,
                argMax(cost, _version) AS latest_cost,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}{time_scope}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_spans AS (
            SELECT
                {resolved_session} AS trace_session_id,
                {resolved_user} AS end_user_id,
                trace_id,
                start_time,
                latest_end_time AS end_time,
                latest_parent_span_id AS parent_span_id,
                latest_input AS input,
                latest_total_tokens AS total_tokens,
                latest_cost AS cost
            FROM latest_spans
            LEFT JOIN ts_survivor_map AS ts_remap
                ON latest_trace_session_id = ts_remap.any_id
            LEFT JOIN eu_survivor_map AS eu_remap
                ON latest_end_user_id = eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND isNotNull({resolved_session})
              AND {resolved_session} != toUUID('{NIL_UUID}')
              {user_predicate}
        )
        SELECT
            trace_session_id,
            min(start_time) AS started_at,
            max(coalesce(end_time, start_time)) AS ended_at,
            uniqExact(trace_id) AS trace_count,
            sum(total_tokens) AS total_tokens,
            sum(cost) AS total_cost,
            argMinIf(input, start_time, parent_span_id = '') AS first_message,
            argMaxIf(input, start_time, parent_span_id = '') AS last_message
        FROM resolved_spans
        GROUP BY trace_session_id
        ORDER BY started_at DESC, trace_session_id DESC
        """
        return query, params


__all__ = ["SessionAnalyticsQueryBuilderV2"]
