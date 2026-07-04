"""ClickHouse query builder for Observe end-user list and detail metrics."""

from typing import Any

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class UserListQueryBuilder(BaseQueryBuilder):
    """Build the Observe Users table query from ClickHouse.

    The output shape intentionally mirrors ``SQLQueryHandler.get_spans_by_end_users``
    so the existing frontend contract does not need a translation layer.
    """

    TABLE = "spans"

    OUTPUT_FILTER_MAP: dict[str, str] = {
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
        workspace_id: str | None = None,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        search: str | None = None,
        sort_params: list[dict[str, Any]] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        max_rows: int | None = None,
        end_user_id: str | None = None,
        include_null_workspace: bool = False,
        empty_scope: bool = False,
    ) -> None:
        # CH25 EndUser cutover (DESIGN §4.3): the curated source is now the v2
        # `end_users` RMT, which — unlike the legacy CDC `tracer_enduser` — has
        # NO `workspace_id` column (schema 017). Workspace isolation therefore
        # can no longer key on the entity's own workspace; the caller resolves
        # the workspace's projects and passes them as `project_ids`, scoping the
        # enduser set by `project_id IN (...)`. `workspace_id` /
        # `include_null_workspace` are retained for signature compatibility but
        # no longer drive any SQL (the project set already encodes the
        # is_default / null-workspace fan-out the legacy filter expressed).
        super().__init__(project_id=project_id, project_ids=project_ids)
        self.organization_id = str(organization_id)
        self.workspace_id = str(workspace_id) if workspace_id else None
        self.filters = filters or []
        self.search = search.strip() if search else None
        self.sort_params = sort_params or []
        self.limit = limit
        self.offset = offset
        # Export-only hard row cap (applied as a LIMIT *without* the window
        # count), so an unpaginated export can't `.all()` an unbounded result
        # into worker memory. Independent of `limit`/`offset` paging.
        self.max_rows = max_rows
        self.end_user_id = str(end_user_id) if end_user_id else None
        self.include_null_workspace = include_null_workspace
        # When the caller resolved an EMPTY workspace-project set, the read must
        # return nothing — NOT fall through to an org-wide scan. (BaseQueryBuilder
        # treats `project_ids=[]` as falsy and would otherwise drop project
        # scoping entirely, re-introducing a cross-workspace leak.)
        self.empty_scope = empty_scope

    def build(self) -> tuple[str, dict[str, Any]]:
        start_date, end_date = self.parse_time_range(self.filters)
        self.params.update(
            {
                "org_id": self.organization_id,
                "start_date": start_date,
                "end_date": end_date,
            }
        )

        if self.search:
            self.params["search"] = self.search
        if self.end_user_id:
            self.params["end_user_id"] = self.end_user_id
        if self.limit is not None and self.offset is not None:
            self.params["limit"] = int(self.limit)
            self.params["offset"] = int(self.offset)
        if self.max_rows is not None:
            self.params["max_rows"] = int(self.max_rows)

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

        # Project scoping carries workspace isolation now that `end_users` has no
        # `workspace_id` (the list view passes the workspace's projects as
        # `project_ids`; the detail views pass a single validated `project_id`).
        # `project_filter_sql()` (BaseQueryBuilder) emits `project_id = …` or
        # `project_id IN …` depending on which mode the builder was built with,
        # and is applied to BOTH the enduser set and the span set (spans also
        # inherit isolation via `end_user_id IN (SELECT … FROM
        # filtered_end_users)`).
        project_filter = ""
        if self.project_ids is not None or self.project_id:
            project_filter = f"AND {self.project_filter_sql()}"

        # Empty resolved workspace-project set ⇒ return nothing (a contradiction
        # the planner prunes), rather than scanning the whole org.
        empty_scope_filter = "AND 0 = 1" if self.empty_scope else ""

        search_filter = ""
        if self.search:
            search_filter = "AND positionCaseInsensitive(user_id, %(search)s) > 0"

        end_user_filter = ""
        if self.end_user_id:
            end_user_filter = "AND end_user_id = toUUID(%(end_user_id)s)"

        span_extra = f"AND {span_where}" if span_where else ""
        final_filter = f"WHERE {output_where}" if output_where else ""
        order_by = self._order_by()
        paginated = self.limit is not None and self.offset is not None
        if paginated:
            pagination = "LIMIT %(limit)s OFFSET %(offset)s"
        elif self.max_rows is not None:
            # Export path: cap rows WITHOUT the window count, so CH streams the
            # ordered scan up to the cap instead of materializing a worktable
            # to count the full (unbounded) result.
            pagination = "LIMIT %(max_rows)s"
        else:
            pagination = ""
        # Skip the window count for unpaginated exports — avoids materializing a worktable.
        total_count_select = (
            "count() OVER() AS total_count" if paginated else "0 AS total_count"
        )

        eval_table, eval_nd_e = eval_logger_source("e")

        query = f"""
        WITH
        filtered_end_users AS (
            SELECT
                end_user_id,
                user_id,
                user_id_type,
                user_id_hash,
                first_seen,
                project_id
            FROM end_users FINAL
            WHERE organization_id = toUUID(%(org_id)s)
              AND is_deleted = 0
              AND notEmpty(user_id)
              {empty_scope_filter}
              {project_filter}
              {search_filter}
              {end_user_filter}
        ),
        rollup_usage AS (
            SELECT
                end_user_id,
                sumMerge(cost_sum) AS total_cost,
                sumMerge(total_tokens_sum) AS total_tokens,
                sumMerge(prompt_tokens_sum) AS input_tokens,
                sumMerge(completion_tokens_sum) AS output_tokens,
                uniqMerge(trace_count) AS num_traces,
                maxMerge(last_seen) AS last_active
            FROM span_user_rollup
            WHERE end_user_id IN (SELECT end_user_id FROM filtered_end_users)
              AND hour_first_seen >= %(start_date)s
              AND hour_first_seen < %(end_date)s
              {project_filter}
            GROUP BY end_user_id
        ),
        raw_spans_light AS (
            SELECT
                end_user_id,
                trace_session_id,
                observation_type,
                status,
                start_time,
                end_time,
                latency_ms,
                trace_id
            FROM spans
            WHERE is_deleted = 0
              AND isNotNull(end_user_id)
              AND end_user_id IN (SELECT end_user_id FROM filtered_end_users)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {project_filter}
              {span_extra}
        ),
        extra_metrics AS (
            SELECT
                end_user_id,
                countIf(observation_type = 'llm') AS num_llm_calls,
                uniqIf(trace_id, observation_type = 'guardrail') AS num_guardrails_triggered,
                round(avgIf(latency_ms, isNotNull(latency_ms)), 2) AS avg_trace_latency,
                uniqExactIf(toDate(start_time), isNotNull(start_time)) AS num_active_days,
                uniqIf(trace_id, status = 'ERROR') AS num_traces_with_errors
            FROM raw_spans_light
            GROUP BY end_user_id
        ),
        session_durations AS (
            SELECT
                end_user_id,
                trace_session_id,
                dateDiff('millisecond', min(start_time), max(end_time)) / 1000.0 AS duration_seconds
            FROM raw_spans_light
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
        final_rows AS (
            SELECT
                eu.user_id AS user_id,
                coalesce(ru.total_cost, 0) AS total_cost,
                coalesce(ru.total_tokens, 0) AS total_tokens,
                coalesce(ru.input_tokens, 0) AS input_tokens,
                coalesce(ru.output_tokens, 0) AS output_tokens,
                coalesce(ru.num_traces, 0) AS num_traces,
                coalesce(sa.num_sessions, 0) AS num_sessions,
                coalesce(sa.avg_session_duration, 0) AS avg_session_duration,
                coalesce(em.avg_trace_latency, 0) AS avg_trace_latency,
                coalesce(em.num_llm_calls, 0) AS num_llm_calls,
                coalesce(em.num_guardrails_triggered, 0) AS num_guardrails_triggered,
                eu.first_seen AS activated_at,
                ru.last_active AS last_active,
                coalesce(em.num_active_days, 0) AS num_active_days,
                coalesce(em.num_traces_with_errors, 0) AS num_traces_with_errors,
                0 AS bool_eval_pass_rate,
                0 AS avg_output_float,
                eu.project_id AS project_id,
                eu.user_id_type AS user_id_type,
                eu.user_id_hash AS user_id_hash,
                eu.end_user_id AS end_user_id
            FROM filtered_end_users AS eu
            INNER JOIN rollup_usage AS ru ON ru.end_user_id = eu.end_user_id
            LEFT JOIN session_aggregates AS sa ON sa.end_user_id = eu.end_user_id
            LEFT JOIN extra_metrics AS em ON em.end_user_id = eu.end_user_id
        ),
        counted_rows AS (
            SELECT
                *,
                {total_count_select}
            FROM final_rows
            {final_filter}
        )
        SELECT *
        FROM counted_rows
        {order_by}
        {pagination}
        """
        return query, self.params

    def build_eval_query(
        self, end_user_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Build a lightweight eval-pass-rate query for a page of user IDs.

        Runs AFTER the main ``build()`` query returns the paginated page.
        Only joins eval_logger against the page's users (not all users),
        avoiding the expensive full-table FINAL scan in the hot path.
        """
        if not end_user_ids:
            return "", {}
        start_date, end_date = self.parse_time_range(self.filters)
        eval_table, eval_nd = eval_logger_source("e")
        params: dict[str, Any] = {
            "eval_eu_ids": tuple(end_user_ids),
            "start_date": start_date,
            "end_date": end_date,
        }
        if self.project_ids:
            params["project_ids"] = tuple(self.project_ids)
            project_filter = "AND project_id IN %(project_ids)s"
        elif self.project_id:
            params["project_id"] = self.project_id
            project_filter = "AND project_id = %(project_id)s"
        else:
            project_filter = ""
        query = f"""
        SELECT
            end_user_id,
            round(
                100.0 * countIf(e.output_bool = 1)
                / nullIf(countIf(isNotNull(e.output_bool)), 0),
                2
            ) AS bool_eval_pass_rate,
            round(avg(e.output_float), 2) AS avg_output_float
        FROM {eval_table} AS e FINAL
        INNER JOIN (
            SELECT DISTINCT end_user_id, trace_id
            FROM spans
            WHERE is_deleted = 0
              AND end_user_id IN %(eval_eu_ids)s
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {project_filter}
        ) AS ut ON e.trace_id = toUUIDOrNull(ut.trace_id)
        WHERE {eval_nd}
        GROUP BY end_user_id
        """
        return query, params

    def _span_filters(self) -> list[dict[str, Any]]:
        return [
            f
            for f in self.filters
            if not self._is_date_filter(f) and not self._is_output_filter(f)
        ]

    def _output_where(self) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
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
        parts: list[str] = []
        for sort in self.sort_params:
            column_id = sort.get("column_id")
            column = self.OUTPUT_FILTER_MAP.get(column_id)
            if not column:
                continue
            direction = str(sort.get("direction") or "desc").upper()
            if direction not in ("ASC", "DESC"):
                direction = "DESC"
            parts.append(f"{column} {direction} NULLS LAST")
        return (
            f"ORDER BY {', '.join(parts)}"
            if parts
            else "ORDER BY last_active DESC NULLS LAST"
        )

    @staticmethod
    def _is_date_filter(item: dict[str, Any]) -> bool:
        config = item.get("filter_config") or {}
        return item.get("column_id") in ("created_at", "start_time") and config.get(
            "filter_type"
        ) in ("datetime", "date")

    def _is_output_filter(self, item: dict[str, Any]) -> bool:
        return item.get("column_id") in self.OUTPUT_FILTER_MAP

    @staticmethod
    def _condition(
        *,
        column: str,
        op: str | None,
        value: Any,
        prefix: str,
    ) -> tuple[str | None, dict[str, Any]]:
        params: dict[str, Any] = {}
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
            return (
                f"positionCaseInsensitive(toString({column}), toString(%({prefix})s)) > 0",
                params,
            )
        if op == "not_contains":
            return (
                f"positionCaseInsensitive(toString({column}), toString(%({prefix})s)) = 0",
                params,
            )
        if op == "starts_with":
            return (
                f"startsWith(lower(toString({column})), lower(toString(%({prefix})s)))",
                params,
            )
        if op == "ends_with":
            return (
                f"endsWith(lower(toString({column})), lower(toString(%({prefix})s)))",
                params,
            )

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
    def format_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        table: list[dict[str, Any]] = []
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
                    # CH25 EndUser cutover (DESIGN §4.3): the v2 `end_users`
                    # column coerces PG NULL hash → '' on write, whereas the
                    # legacy `tracer_enduser.user_id_hash` (Nullable) preserved
                    # NULL and the old read surfaced it as None. Normalize '' →
                    # None here to keep that contract — matching the sibling
                    # `end_user_dict_reader.resolve_end_user_fields` (`row[3] or
                    # None`). `user_id_type` is Nullable end-to-end (no
                    # coercion) so it is NOT normalized: a genuine '' must stay
                    # '' to match the old FK value.
                    "user_id_hash": row.get("user_id_hash") or None,
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
