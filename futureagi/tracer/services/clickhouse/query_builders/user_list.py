"""ClickHouse query builder for Observe end-user list and detail metrics."""

from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class UserListQueryBuilder(BaseQueryBuilder):
    """Build the Observe Users table query from ClickHouse.

    The output shape intentionally mirrors ``SQLQueryHandler.get_spans_by_end_users``
    so the existing frontend contract does not need a translation layer.
    """

    TABLE = "spans"

    OUTPUT_FILTER_MAP: Dict[str, str] = {
        "user_id": "user_id",
        "user_id_type": "user_id_type",
        "user_id_hash": "user_id_hash",
        "activated_at": "activated_at",
        "created_at": "activated_at",
        "last_active": "last_active",
        "num_active_days": "num_active_days",
        "active_days": "num_active_days",
        "total_cost": "total_cost",
        "cost": "total_cost",
        "avg_cost": "total_cost",
        "total_tokens": "total_tokens",
        "tokens": "total_tokens",
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "num_traces": "num_traces",
        "traffic": "num_traces",
        "num_sessions": "num_sessions",
        "avg_session_duration": "avg_session_duration",
        "avg_trace_latency": "avg_trace_latency",
        "avg_latency": "avg_trace_latency",
        "latency": "avg_trace_latency",
        "latency_ms": "avg_trace_latency",
        "num_llm_calls": "num_llm_calls",
        "num_guardrails_triggered": "num_guardrails_triggered",
        "num_traces_with_errors": "num_traces_with_errors",
        "bool_eval_pass_rate": "bool_eval_pass_rate",
        "avg_output_float": "avg_output_float",
        "project_id": "project_id",
        "end_user_id": "end_user_id",
    }

    def __init__(
        self,
        *,
        organization_id: str,
        workspace_id: Optional[str],
        project_id: Optional[str] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        search: Optional[str] = None,
        sort_params: Optional[List[Dict[str, Any]]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        end_user_id: Optional[str] = None,
        include_null_workspace: bool = False,
    ) -> None:
        super().__init__(project_id=project_id)
        self.organization_id = str(organization_id)
        self.workspace_id = str(workspace_id) if workspace_id else None
        self.filters = filters or []
        self.search = search.strip() if search else None
        self.sort_params = sort_params or []
        self.limit = limit
        self.offset = offset
        self.end_user_id = str(end_user_id) if end_user_id else None
        self.include_null_workspace = include_null_workspace

    def build(self) -> Tuple[str, Dict[str, Any]]:
        start_date, end_date = self.parse_time_range(self.filters)
        self.params.update(
            {
                "org_id": self.organization_id,
                "start_date": start_date,
                "end_date": end_date,
            }
        )

        if self.workspace_id:
            self.params["workspace_id"] = self.workspace_id
        if self.search:
            self.params["search"] = self.search
        if self.end_user_id:
            self.params["end_user_id"] = self.end_user_id
        if self.limit is not None and self.offset is not None:
            self.params["limit"] = int(self.limit)
            self.params["offset"] = int(self.offset)

        span_filters = self._span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            project_id=self.project_id,
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_TRACE,
        )
        span_where, span_params = fb.translate(span_filters)
        self.params.update(span_params)

        output_where, output_params = self._output_where()
        self.params.update(output_params)

        project_filter = ""
        if self.project_id:
            project_filter = "AND project_id = toUUID(%(project_id)s)"

        workspace_filter = ""
        if self.workspace_id:
            if self.include_null_workspace:
                workspace_filter = (
                    "AND (workspace_id = toUUID(%(workspace_id)s) "
                    "OR isNull(workspace_id))"
                )
            else:
                workspace_filter = "AND workspace_id = toUUID(%(workspace_id)s)"

        search_filter = ""
        if self.search:
            search_filter = "AND positionCaseInsensitive(user_id, %(search)s) > 0"

        end_user_filter = ""
        if self.end_user_id:
            end_user_filter = "AND id = toUUID(%(end_user_id)s)"

        span_extra = f"AND {span_where}" if span_where else ""
        final_filter = f"WHERE {output_where}" if output_where else ""
        order_by = self._order_by()
        pagination = (
            "LIMIT %(limit)s OFFSET %(offset)s"
            if self.limit is not None and self.offset is not None
            else ""
        )

        query = f"""
        WITH
        filtered_end_users AS (
            SELECT
                id,
                user_id,
                user_id_type,
                user_id_hash,
                created_at,
                project_id
            FROM tracer_enduser FINAL
            WHERE organization_id = toUUID(%(org_id)s)
              AND _peerdb_is_deleted = 0
              AND deleted = 0
              AND notEmpty(user_id)
              {workspace_filter}
              {project_filter}
              {search_filter}
              {end_user_filter}
        ),
        filtered_spans AS (
            SELECT
                id,
                trace_id,
                project_id,
                end_user_id,
                trace_session_id,
                observation_type,
                status,
                start_time,
                end_time,
                latency_ms,
                cost,
                total_tokens,
                prompt_tokens,
                completion_tokens,
                span_attributes_raw,
                span_attr_str,
                span_attr_num
            FROM spans
            WHERE _peerdb_is_deleted = 0
              AND end_user_id IN (SELECT id FROM filtered_end_users)
              AND isNotNull(end_user_id)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {project_filter}
              {span_extra}
        ),
        aggregated_usage AS (
            SELECT
                end_user_id,
                sum(ifNull(cost, 0)) AS total_cost,
                sum(ifNull(total_tokens, 0)) AS total_tokens,
                sum(ifNull(prompt_tokens, 0)) AS input_tokens,
                sum(ifNull(completion_tokens, 0)) AS output_tokens,
                uniqExact(trace_id) AS num_traces,
                countIf(observation_type = 'llm') AS num_llm_calls,
                uniqExactIf(trace_id, observation_type = 'guardrail') AS num_guardrails_triggered,
                round(avgIf(latency_ms, isNotNull(latency_ms)), 2) AS avg_trace_latency,
                max(end_time) AS last_active,
                uniqExactIf(toDate(start_time), isNotNull(start_time)) AS num_active_days,
                uniqExactIf(trace_id, status = 'ERROR') AS num_traces_with_errors
            FROM filtered_spans
            GROUP BY end_user_id
        ),
        session_durations AS (
            SELECT
                end_user_id,
                trace_session_id,
                dateDiff('millisecond', min(start_time), max(end_time)) / 1000.0 AS duration_seconds
            FROM filtered_spans
            WHERE isNotNull(trace_session_id)
              AND isNotNull(start_time)
              AND isNotNull(end_time)
            GROUP BY end_user_id, trace_session_id
        ),
        session_aggregates AS (
            SELECT
                end_user_id,
                count() AS num_sessions,
                round(avg(duration_seconds), 2) AS avg_session_duration
            FROM session_durations
            GROUP BY end_user_id
        ),
        user_traces AS (
            SELECT DISTINCT end_user_id, trace_id
            FROM filtered_spans
            WHERE notEmpty(trace_id)
        ),
        eval_pass_rate AS (
            SELECT
                ut.end_user_id,
                round(
                    100.0 * countIf(e.output_bool = 1)
                    / nullIf(countIf(isNotNull(e.output_bool)), 0),
                    2
                ) AS bool_eval_pass_rate,
                round(avg(e.output_float), 2) AS avg_output_float
            FROM tracer_eval_logger AS e FINAL
            INNER JOIN user_traces AS ut ON toString(e.trace_id) = ut.trace_id
            WHERE e._peerdb_is_deleted = 0
              AND e.deleted = 0
            GROUP BY ut.end_user_id
        ),
        final_rows AS (
            SELECT
                eu.user_id AS user_id,
                coalesce(au.total_cost, 0) AS total_cost,
                coalesce(au.total_tokens, 0) AS total_tokens,
                coalesce(au.input_tokens, 0) AS input_tokens,
                coalesce(au.output_tokens, 0) AS output_tokens,
                coalesce(au.num_traces, 0) AS num_traces,
                coalesce(sa.num_sessions, 0) AS num_sessions,
                coalesce(sa.avg_session_duration, 0) AS avg_session_duration,
                coalesce(au.avg_trace_latency, 0) AS avg_trace_latency,
                coalesce(au.num_llm_calls, 0) AS num_llm_calls,
                coalesce(au.num_guardrails_triggered, 0) AS num_guardrails_triggered,
                eu.created_at AS activated_at,
                au.last_active AS last_active,
                coalesce(au.num_active_days, 0) AS num_active_days,
                coalesce(au.num_traces_with_errors, 0) AS num_traces_with_errors,
                coalesce(epr.bool_eval_pass_rate, 0) AS bool_eval_pass_rate,
                coalesce(epr.avg_output_float, 0) AS avg_output_float,
                eu.project_id AS project_id,
                eu.user_id_type AS user_id_type,
                eu.user_id_hash AS user_id_hash,
                eu.id AS end_user_id
            FROM filtered_end_users AS eu
            INNER JOIN (SELECT DISTINCT end_user_id FROM filtered_spans) AS visible
                ON visible.end_user_id = eu.id
            LEFT JOIN aggregated_usage AS au ON au.end_user_id = eu.id
            LEFT JOIN session_aggregates AS sa ON sa.end_user_id = eu.id
            LEFT JOIN eval_pass_rate AS epr ON epr.end_user_id = eu.id
        ),
        counted_rows AS (
            SELECT
                *,
                count() OVER() AS total_count
            FROM final_rows
            {final_filter}
        )
        SELECT *
        FROM counted_rows
        {order_by}
        {pagination}
        """
        return query, self.params

    def _span_filters(self) -> List[Dict[str, Any]]:
        return [
            f
            for f in self.filters
            if not self._is_date_filter(f) and not self._is_output_filter(f)
        ]

    def _output_where(self) -> Tuple[str, Dict[str, Any]]:
        clauses: List[str] = []
        params: Dict[str, Any] = {}
        for index, item in enumerate(self.filters):
            if self._is_date_filter(item) or not self._is_output_filter(item):
                continue
            config = item.get("filter_config") or {}
            column = self.OUTPUT_FILTER_MAP[item.get("column_id")]
            clause, clause_params = self._condition(
                column=column,
                op=config.get("filter_op"),
                value=config.get("filter_value"),
                prefix=f"user_filter_{index}",
            )
            if clause:
                clauses.append(clause)
                params.update(clause_params)
        return " AND ".join(clauses), params

    def _order_by(self) -> str:
        if not self.sort_params:
            return "ORDER BY last_active DESC NULLS LAST"
        parts: List[str] = []
        for sort in self.sort_params:
            column_id = sort.get("column_id")
            column = self.OUTPUT_FILTER_MAP.get(column_id)
            if not column:
                continue
            direction = str(sort.get("direction") or "desc").upper()
            if direction not in ("ASC", "DESC"):
                direction = "DESC"
            parts.append(f"{column} {direction} NULLS LAST")
        return f"ORDER BY {', '.join(parts)}" if parts else "ORDER BY last_active DESC NULLS LAST"

    @staticmethod
    def _is_date_filter(item: Dict[str, Any]) -> bool:
        config = item.get("filter_config") or {}
        return item.get("column_id") in ("created_at", "start_time") and config.get(
            "filter_type"
        ) in ("datetime", "date")

    def _is_output_filter(self, item: Dict[str, Any]) -> bool:
        return item.get("column_id") in self.OUTPUT_FILTER_MAP

    @staticmethod
    def _condition(
        *,
        column: str,
        op: Optional[str],
        value: Any,
        prefix: str,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if op == "is_null":
            return f"isNull({column})", params
        if op == "is_not_null":
            return f"isNotNull({column})", params
        if op in ("between", "not_between"):
            if not isinstance(value, list) or len(value) != 2:
                return None, params
            params[f"{prefix}_start"] = value[0]
            params[f"{prefix}_end"] = value[1]
            operator = "NOT BETWEEN" if op == "not_between" else "BETWEEN"
            return (
                f"{column} {operator} %({prefix}_start)s AND %({prefix}_end)s",
                params,
            )
        if op in ("in", "not_in"):
            values = value if isinstance(value, list) else [value]
            values = [v for v in values if v not in (None, "")]
            if not values:
                return ("1 = 1" if op == "not_in" else "0 = 1"), params
            params[prefix] = tuple(values)
            operator = "NOT IN" if op == "not_in" else "IN"
            return f"{column} {operator} %({prefix})s", params
        if value is None:
            return None, params

        params[prefix] = value
        if op == "contains":
            return f"positionCaseInsensitive(toString({column}), toString(%({prefix})s)) > 0", params
        if op == "not_contains":
            return f"positionCaseInsensitive(toString({column}), toString(%({prefix})s)) = 0", params
        if op == "starts_with":
            return f"startsWith(lower(toString({column})), lower(toString(%({prefix})s)))", params
        if op == "ends_with":
            return f"endsWith(lower(toString({column})), lower(toString(%({prefix})s)))", params

        operator_map = {
            "equals": "=",
            "not_equals": "!=",
            "greater_than": ">",
            "greater_than_or_equal": ">=",
            "less_than": "<",
            "less_than_or_equal": "<=",
        }
        operator = operator_map.get(op)
        if not operator:
            return None, params
        return f"{column} {operator} %({prefix})s", params

    @staticmethod
    def format_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        table: List[Dict[str, Any]] = []
        total_count = 0
        for row in rows:
            total_count = int(row.get("total_count") or total_count or 0)
            table.append(
                {
                    "user_id": row.get("user_id"),
                    "total_cost": round(row.get("total_cost") or 0, 6),
                    "total_tokens": row.get("total_tokens") or 0,
                    "input_tokens": row.get("input_tokens") or 0,
                    "output_tokens": row.get("output_tokens") or 0,
                    "num_traces": row.get("num_traces") or 0,
                    "num_sessions": row.get("num_sessions") or 0,
                    "avg_session_duration": row.get("avg_session_duration") or 0,
                    "avg_trace_latency": row.get("avg_trace_latency") or 0,
                    "num_llm_calls": row.get("num_llm_calls") or 0,
                    "num_guardrails_triggered": row.get("num_guardrails_triggered")
                    or 0,
                    "activated_at": UserListQueryBuilder._json_value(
                        row.get("activated_at")
                    ),
                    "last_active": UserListQueryBuilder._json_value(
                        row.get("last_active")
                    ),
                    "num_active_days": row.get("num_active_days") or 0,
                    "num_traces_with_errors": row.get("num_traces_with_errors") or 0,
                    "bool_eval_pass_rate": row.get("bool_eval_pass_rate") or 0,
                    "avg_output_float": row.get("avg_output_float") or 0,
                    "project_id": UserListQueryBuilder._json_value(
                        row.get("project_id")
                    ),
                    "user_id_type": row.get("user_id_type"),
                    "user_id_hash": row.get("user_id_hash"),
                    "end_user_id": UserListQueryBuilder._json_value(
                        row.get("end_user_id")
                    ),
                }
            )
        return {"table": table, "total_count": total_count}

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
