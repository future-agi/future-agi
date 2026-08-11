"""ClickHouse query builder for Observe end-user list and detail metrics."""

from typing import Any

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    bounded_survivor_map_subquery,
    literal_survivor_map_subquery,
    resolved_id_expr,
    survivor_map_subquery,
)


class UnsupportedBoundedUserListQuery(ValueError):
    """Raised when an exact user page cannot use the bounded query path."""


def _touched_survivor_map_subquery(
    *, remap_table: str, candidate_cte: str, candidate_column: str
) -> str:
    """Resolve only consolidation groups reached by a finite candidate CTE."""

    return f"""
        SELECT
            any_id,
            argMin(survivor_id, toString(survivor_id)) AS survivor_id
        FROM (
            SELECT
                arrayJoin(
                    arrayDistinct(arrayConcat(groupArray(old_id), [new_id]))
                ) AS any_id,
                argMin(old_id, toString(old_id)) AS survivor_id
            FROM {remap_table} FINAL
            WHERE new_id IN (
                SELECT DISTINCT new_id
                FROM {remap_table} FINAL
                WHERE old_id IN (
                    SELECT {candidate_column} FROM {candidate_cte}
                )
                   OR new_id IN (
                    SELECT {candidate_column} FROM {candidate_cte}
                )
            )
            GROUP BY new_id
        )
        GROUP BY any_id
    """


class UserListQueryBuilder(BaseQueryBuilder):
    """Build the Observe Users table query from ClickHouse.

    The output shape intentionally mirrors ``SQLQueryHandler.get_spans_by_end_users``
    so the existing frontend contract does not need a translation layer.
    """

    TABLE = "spans"
    # Preserve rollout-configured storage for the legacy builder while giving
    # the V2 wrapper an explicit direct-write injection point.
    _EVAL_LOGGER_SOURCE = staticmethod(eval_logger_source)

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

    # Columns that can be selected by the exact latest-span page query.  The
    # selector deliberately does *not* use ``span_user_rollup``: that table is
    # populated by an insert-only AggregateMergeTree MV, so a later span
    # tombstone/version reassignment cannot retract its contribution.  Using it
    # for membership or ordering can therefore return the wrong page quickly.
    # Raw-derived metrics that are hydrated only after pagination remain
    # unsupported as page filters/sorts because applying them after ``LIMIT``
    # would also change membership/order.
    _CANDIDATE_FIRST_COLUMNS = frozenset(
        {
            "user_id",
            "user_id_type",
            "user_id_hash",
            "activated_at",
            "created_at",
            "last_active",
            "total_cost",
            "cost",
            "avg_cost",
            "total_tokens",
            "tokens",
            "input_tokens",
            "prompt_tokens",
            "output_tokens",
            "completion_tokens",
            "num_traces",
            "traffic",
            "project_id",
            "end_user_id",
        }
    )

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
        candidate_end_user_ids: list[str] | tuple[str, ...] | None = None,
        candidate_scan_end_user_ids: list[str] | tuple[str, ...] | None = None,
        candidate_end_user_id_map: dict[str, str] | None = None,
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
        self.candidate_end_user_ids = tuple(
            str(value) for value in (candidate_end_user_ids or ())
        )
        self.candidate_scan_end_user_ids = tuple(
            str(value)
            for value in (candidate_scan_end_user_ids or self.candidate_end_user_ids)
        )
        supplied_map = {
            str(any_id): str(survivor_id)
            for any_id, survivor_id in (candidate_end_user_id_map or {}).items()
            if any_id and survivor_id
        }
        if self.candidate_end_user_ids and not supplied_map:
            supplied_map = {
                candidate_id: candidate_id
                for candidate_id in self.candidate_scan_end_user_ids
            }
        if supplied_map:
            missing_aliases = (
                set(self.candidate_scan_end_user_ids) - supplied_map.keys()
            )
            invalid_survivors = set(supplied_map.values()) - set(
                self.candidate_end_user_ids
            )
            if missing_aliases or invalid_survivors:
                raise ValueError("candidate end-user remap is inconsistent")
        self.candidate_end_user_id_map = tuple(supplied_map.items())
        self.include_null_workspace = include_null_workspace
        # When the caller resolved an EMPTY workspace-project set, the read must
        # return nothing — NOT fall through to an org-wide scan. (BaseQueryBuilder
        # treats `project_ids=[]` as falsy and would otherwise drop project
        # scoping entirely, re-introducing a cross-workspace leak.)
        self.empty_scope = empty_scope

    def _finite_end_user_map(
        self,
        *,
        candidate_param: str,
    ) -> tuple[str, dict[str, list[str]]]:
        """Return a literal page map when the cursor already resolved one."""

        if not self.candidate_end_user_id_map:
            return (
                bounded_survivor_map_subquery(
                    "end_user_id_remap", candidate_param=candidate_param
                ),
                {},
            )
        any_ids, survivor_ids = zip(
            *self.candidate_end_user_id_map,
            strict=True,
        )
        return (
            literal_survivor_map_subquery(
                any_ids_param="candidate_remap_any_ids",
                survivor_ids_param="candidate_remap_survivor_ids",
            ),
            {
                "candidate_remap_any_ids": list(any_ids),
                "candidate_remap_survivor_ids": list(survivor_ids),
            },
        )

    def build_dimension_candidate_query(
        self,
        *,
        limit: int,
        before_first_seen: Any | None = None,
        before_end_user_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return one stable, finite page from the curated user dimension.

        This query is only a candidate selector.  The caller replays the
        latest physical spans for the returned ids before publishing a row, so
        a stale/tombstoned dimension entry can create extra bounded work but
        can never become a false list result. Ordering by immutable
        ``first_seen`` plus the lexicographic String form of ``end_user_id``
        gives continuation pages a total order without scanning or grouping the
        tenant's full span window.
        """

        if limit <= 0:
            raise ValueError("dimension candidate limit must be positive")
        if (before_first_seen is None) != (before_end_user_id is None):
            raise ValueError("dimension continuation values must be provided together")

        params: dict[str, Any] = {
            "org_id": self.organization_id,
            "dimension_limit": int(limit),
        }
        if self.project_ids is not None:
            params["project_ids"] = tuple(self.project_ids)
        else:
            params["project_id"] = self.project_id
        if self.search:
            params["search"] = self.search
        if before_first_seen is not None:
            # ``clickhouse-driver`` formats a bound Python datetime at whole-
            # second precision.  A continuation created inside a DateTime64(6)
            # tie would therefore skip every remaining user in that
            # microsecond bucket.  Carry an ISO string and parse it explicitly
            # in ClickHouse so the keyset predicate uses the same precision as
            # the published ordering value.
            params["before_first_seen"] = (
                before_first_seen.isoformat()
                if hasattr(before_first_seen, "isoformat")
                else str(before_first_seen)
            )
            params["before_end_user_id"] = str(before_end_user_id)

        empty_scope_filter = "AND 0 = 1" if self.empty_scope else ""
        search_filter = (
            "AND positionCaseInsensitive(user_id, %(search)s) > 0"
            if self.search
            else ""
        )
        continuation_filter = (
            """
            AND (
                first_seen
                    < parseDateTime64BestEffort(%(before_first_seen)s, 6, 'UTC')
                OR (
                    first_seen
                        = parseDateTime64BestEffort(
                            %(before_first_seen)s, 6, 'UTC'
                        )
                    AND toString(eu.end_user_id) < %(before_end_user_id)s
                )
            )
            """
            if before_first_seen is not None
            else ""
        )
        query = f"""
        SELECT
            toString(eu.end_user_id) AS end_user_id,
            user_id,
            user_id_type,
            user_id_hash,
            first_seen
        FROM end_users AS eu FINAL
        WHERE eu.organization_id = toUUID(%(org_id)s)
          AND eu.is_deleted = 0
          AND notEmpty(eu.user_id)
          AND {self._project_predicate("eu")}
          {empty_scope_filter}
          {search_filter}
          {continuation_filter}
        ORDER BY first_seen DESC, toString(eu.end_user_id) DESC
        LIMIT %(dimension_limit)s
        """
        return query, params

    def build_dimension_survivor_query(
        self, candidate_end_user_ids: list[str] | tuple[str, ...]
    ) -> tuple[str, dict[str, Any]]:
        """Classify a finite dimension page into canonical survivor ids.

        The curated dimension remains old-id keyed during the remap window.
        Only the lexicographic survivor old id may be published; alias rows are
        still consumed by the continuation cursor so they can never loop or be
        emitted on a later page.  Ids absent from the remap table are survivors
        by definition and are filled by the caller.
        """

        ids = tuple(str(value) for value in candidate_end_user_ids if value)
        if not ids:
            return "", {}
        remap = bounded_survivor_map_subquery(
            "end_user_id_remap", candidate_param="dimension_candidate_ids"
        )
        return (
            f"""
            WITH bounded_map AS ({remap})
            SELECT
                toString(any_id) AS any_id,
                toString(survivor_id) AS survivor_id
            FROM bounded_map
            """,
            {"dimension_candidate_ids": ids},
        )

    def supports_candidate_first_page(self) -> bool:
        """Whether the request can page exactly from latest physical spans.

        Raw-span filters and raw-derived metric ordering cannot be moved after
        pagination without changing which users belong on page N. Paginated
        list/export callers fail closed for those shapes; this method is
        deliberately conservative rather than returning a fast wrong page or
        silently running the historical all-users aggregate.
        """

        if self._span_filters():
            return False
        for item in self.filters:
            if self._is_date_filter(item):
                continue
            column_id = item.get("column_id") or item.get("columnId")
            if column_id not in self._CANDIDATE_FIRST_COLUMNS:
                return False
        for item in self.sort_params:
            column_id = item.get("column_id") or item.get("columnId")
            # Preserve the existing API contract: unknown sort columns are
            # ignored and the default ordering is used.
            if column_id not in self.OUTPUT_FILTER_MAP:
                continue
            if column_id not in self._CANDIDATE_FIRST_COLUMNS:
                return False
        return True

    def build_physical_user_presence_query(self) -> tuple[str, dict[str, Any]]:
        """Return a cheap, conservative proof of physical user-span presence.

        A negative result is exact: a live latest span with a user necessarily
        has at least one physical version with a non-null user id.  A positive
        result is intentionally only a hint because it may be a stale version
        or tombstone; the full latest replay still decides membership.
        """

        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            "presence_start_date": start_date,
            "presence_end_date": end_date,
        }
        if self.project_ids is not None:
            params["project_ids"] = tuple(self.project_ids)
        else:
            params["project_id"] = self.project_id
        query = f"""
        SELECT 1 AS has_physical_user_span
        FROM spans
        PREWHERE {self._project_predicate("spans")}
          AND toDate(start_time) BETWEEN
              toDate(%(presence_start_date)s) AND toDate(%(presence_end_date)s)
          AND start_time >= %(presence_start_date)s
          AND start_time < %(presence_end_date)s
          AND isNotNull(end_user_id)
        LIMIT 1
        """
        return query, params

    def _project_predicate(self, alias: str) -> str:
        if self.project_ids is not None:
            return f"{alias}.project_id IN %(project_ids)s"
        return f"{alias}.project_id = %(project_id)s"

    def _candidate_page_ctes(self) -> tuple[str, str, dict[str, Any]]:
        """Build the exact latest-state page selector shared by list reads.

        ``span_user_rollup`` is intentionally absent.  Its insert-only states
        are useful for approximate analytics, but cannot retract a later span
        tombstone, user reassignment, or corrected token/cost value.  This
        selector uses one of two exact latest-state plans.  Cursor reads already
        own a finite dimension candidate set, so they first identify only span
        identities that ever referenced those users and replay every version of
        those identities with ``argMax(_version)``.  This preserves reassignment
        and tombstone semantics while avoiding an all-user aggregation.  The
        legacy numbered path has no finite candidate set and therefore uses
        ``FINAL`` across the requested partitions.  Id remaps are resolved
        before membership, metrics, order, and the page count in both plans.
        """

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
        if self.candidate_end_user_ids:
            self.params["candidate_end_user_ids"] = self.candidate_end_user_ids
            self.params["candidate_scan_end_user_ids"] = (
                self.candidate_scan_end_user_ids
            )
        if self.limit is not None and self.offset is not None:
            self.params["limit"] = int(self.limit)
            self.params["offset"] = int(self.offset)
        if self.max_rows is not None:
            self.params["max_rows"] = int(self.max_rows)

        output_where, output_params = self._output_where()
        self.params.update(output_params)

        empty_scope_filter = "AND 0 = 1" if self.empty_scope else ""
        search_filter = (
            "AND positionCaseInsensitive(eu.user_id, %(search)s) > 0"
            if self.search
            else ""
        )
        end_user_filter = (
            "AND eu.end_user_id = toUUID(%(end_user_id)s)" if self.end_user_id else ""
        )
        candidate_end_user_filter = (
            "HAVING end_user_id IN %(candidate_end_user_ids)s"
            if self.candidate_end_user_ids
            else ""
        )
        final_filter = f"WHERE {output_where}" if output_where else ""
        order_by = self._order_by()
        paginated = self.limit is not None and self.offset is not None
        if paginated:
            pagination = "LIMIT %(limit)s OFFSET %(offset)s"
            total_count_select = "count() OVER() AS total_count"
        elif self.max_rows is not None:
            pagination = "LIMIT %(max_rows)s"
            total_count_select = "0 AS total_count"
        else:
            pagination = ""
            total_count_select = "0 AS total_count"

        if self.candidate_end_user_ids:
            eu_map, finite_map_params = self._finite_end_user_map(
                candidate_param="candidate_end_user_ids"
            )
            self.params.update(finite_map_params)
        else:
            eu_map = survivor_map_subquery("end_user_id_remap")
        resolved_curated_eu = resolved_id_expr("eu.end_user_id", "eu_remap")
        resolved_latest_eu = resolved_id_expr("latest_end_user_id", "span_eu_remap")
        if self.candidate_end_user_ids:
            # ``end_user_id`` is mutable, so it cannot be pushed directly into
            # a FINAL read: doing so can hide a newer reassignment/tombstone and
            # resurrect an older version.  The first scan is only an identity
            # superset.  The second scan replays *all* versions of each selected
            # immutable identity and applies user/deletion predicates afterward.
            usage_ctes = f"""
        candidate_span_identities AS (
            SELECT DISTINCT
                project_id,
                trace_id,
                id,
                start_time
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND end_user_id IN %(candidate_scan_end_user_ids)s
        ),
        latest_candidate_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(cost, _version) AS latest_cost,
                argMax(total_tokens, _version) AS latest_total_tokens,
                argMax(prompt_tokens, _version) AS latest_prompt_tokens,
                argMax(completion_tokens, _version) AS latest_completion_tokens,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        exact_usage AS (
            SELECT
                {resolved_latest_eu} AS end_user_id,
                sum(latest_cost) AS total_cost,
                sum(toInt64(latest_total_tokens)) AS total_tokens,
                sum(toInt64(latest_prompt_tokens)) AS input_tokens,
                sum(toInt64(latest_completion_tokens)) AS output_tokens,
                uniqExact(trace_id) AS num_traces,
                max(latest_end_time) AS last_active
            FROM latest_candidate_spans
            LEFT JOIN eu_survivor_map AS span_eu_remap
                ON latest_end_user_id = span_eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_latest_eu} IN (
                  SELECT end_user_id FROM filtered_end_users
              )
            GROUP BY end_user_id
        )
            """
        else:
            usage_ctes = f"""
        exact_usage AS (
            SELECT
                {resolved_id_expr("sp.end_user_id", "span_eu_remap")} AS end_user_id,
                sum(sp.cost) AS total_cost,
                sum(toInt64(sp.total_tokens)) AS total_tokens,
                sum(toInt64(sp.prompt_tokens)) AS input_tokens,
                sum(toInt64(sp.completion_tokens)) AS output_tokens,
                uniqExact(sp.trace_id) AS num_traces,
                max(sp.end_time) AS last_active
            FROM spans AS sp FINAL
            LEFT JOIN eu_survivor_map AS span_eu_remap
                ON sp.end_user_id = span_eu_remap.any_id
            PREWHERE {self._project_predicate("sp")}
              AND toDate(sp.start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND sp.start_time >= %(start_date)s
              AND sp.start_time < %(end_date)s
            WHERE sp.is_deleted = 0
              AND isNotNull(sp.end_user_id)
              AND {resolved_id_expr("sp.end_user_id", "span_eu_remap")} IN (
                  SELECT end_user_id FROM filtered_end_users
              )
            GROUP BY end_user_id
        )
            """
        ctes = f"""
        eu_survivor_map AS ({eu_map}),
        filtered_end_users_raw AS (
            SELECT
                eu.project_id,
                eu.end_user_id,
                eu.user_id,
                eu.user_id_type,
                eu.user_id_hash,
                eu.first_seen,
                eu.version
            FROM end_users AS eu FINAL
            WHERE eu.organization_id = toUUID(%(org_id)s)
              AND eu.is_deleted = 0
              AND notEmpty(eu.user_id)
              AND {self._project_predicate("eu")}
              {empty_scope_filter}
              {search_filter}
              {end_user_filter}
        ),
        filtered_end_users AS (
            SELECT
                {resolved_curated_eu} AS end_user_id,
                argMax(eu.user_id, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id,
                argMax(eu.user_id_type, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id_type,
                argMax(eu.user_id_hash, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id_hash,
                min(eu.first_seen) AS first_seen,
                argMax(eu.project_id, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS project_id
            FROM filtered_end_users_raw AS eu
            LEFT JOIN eu_survivor_map AS eu_remap
                ON eu.end_user_id = eu_remap.any_id
            GROUP BY end_user_id
            {candidate_end_user_filter}
        ),
        {usage_ctes},
        base_rows AS (
            SELECT
                eu.user_id AS user_id,
                coalesce(xu.total_cost, 0) AS total_cost,
                coalesce(xu.total_tokens, 0) AS total_tokens,
                coalesce(xu.input_tokens, 0) AS input_tokens,
                coalesce(xu.output_tokens, 0) AS output_tokens,
                coalesce(xu.num_traces, 0) AS num_traces,
                eu.first_seen AS activated_at,
                xu.last_active AS last_active,
                eu.project_id AS project_id,
                eu.user_id_type AS user_id_type,
                eu.user_id_hash AS user_id_hash,
                eu.end_user_id AS end_user_id
            FROM filtered_end_users AS eu
            INNER JOIN exact_usage AS xu ON xu.end_user_id = eu.end_user_id
        ),
        candidate_users AS (
            SELECT
                *,
                {total_count_select}
            FROM base_rows
            {final_filter}
            {order_by}
            {pagination}
        )
        """
        return ctes, order_by, dict(self.params)

    def build_candidate_page_query(self) -> tuple[str, dict[str, Any]]:
        """Return only the finite user page selected from compact dimensions."""

        if not self.supports_candidate_first_page():
            raise UnsupportedBoundedUserListQuery(
                "user list filter/sort is not supported by the bounded query path"
            )
        ctes, order_by, params = self._candidate_page_ctes()
        query = f"""
        WITH
        {ctes}
        SELECT
            user_id,
            total_cost,
            total_tokens,
            input_tokens,
            output_tokens,
            num_traces,
            activated_at,
            last_active,
            project_id,
            user_id_type,
            user_id_hash,
            end_user_id,
            total_count
        FROM candidate_users
        {order_by}
        """
        return query, params

    def build_page_metrics_query(
        self, end_user_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Hydrate raw-derived metrics for one already-selected user page.

        Every physical span identity is replayed with ``argMax(_version)``;
        tombstones are rejected only after replay, and both end-user/session
        ids are canonicalized through the remap survivor tables.
        """

        if not end_user_ids:
            return "", {}
        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            "candidate_end_user_ids": tuple(str(value) for value in end_user_ids),
            "start_date": start_date,
            "end_date": end_date,
        }
        if self.project_ids:
            params["project_ids"] = tuple(self.project_ids)
        else:
            params["project_id"] = self.project_id

        # Page hydration runs only after a finite user page is selected. Building
        # the global remap window here made this read exceed the 256 MiB
        # production ceiling on large remap tables. Expand only the consolidation
        # groups touched by the page ids.
        eu_map, finite_map_params = self._finite_end_user_map(
            candidate_param="candidate_end_user_ids"
        )
        params.update(finite_map_params)
        ts_map = survivor_map_subquery("trace_session_id_remap")
        resolved_latest_eu = resolved_id_expr("latest_end_user_id", "span_eu_remap")
        resolved_latest_session = resolved_id_expr(
            "latest_trace_session_id", "span_ts_remap"
        )

        query = f"""
        WITH
        eu_survivor_map AS ({eu_map}),
        ts_survivor_map AS ({ts_map}),
        expanded_candidate_user_ids AS (
            SELECT any_id AS end_user_id
            FROM eu_survivor_map
            WHERE survivor_id IN %(candidate_end_user_ids)s
            UNION DISTINCT
            SELECT end_user_id
            FROM end_users FINAL
            WHERE end_user_id IN %(candidate_end_user_ids)s
        ),
        candidate_span_identities AS (
            SELECT DISTINCT
                project_id,
                trace_id,
                id,
                start_time
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND end_user_id IN (
                  SELECT end_user_id FROM expanded_candidate_user_ids
              )
        ),
        latest_candidate_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(tuple(trace_session_id), _version).1 AS latest_trace_session_id,
                argMax(observation_type, _version) AS latest_observation_type,
                argMax(status, _version) AS latest_status,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(latency_ms, _version) AS latest_latency_ms,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        resolved_candidate_spans AS (
            SELECT
                {resolved_latest_eu} AS end_user_id,
                {resolved_latest_session} AS trace_session_id,
                trace_id,
                start_time,
                latest_end_time AS end_time,
                latest_latency_ms AS latency_ms,
                latest_observation_type AS observation_type,
                latest_status AS status
            FROM latest_candidate_spans
            LEFT JOIN eu_survivor_map AS span_eu_remap
                ON latest_end_user_id = span_eu_remap.any_id
            LEFT JOIN ts_survivor_map AS span_ts_remap
                ON latest_trace_session_id = span_ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_latest_eu} IN %(candidate_end_user_ids)s
        ),
        extra_metrics AS (
            SELECT
                end_user_id,
                countIf(observation_type = 'llm') AS num_llm_calls,
                uniqExactIf(trace_id, observation_type = 'guardrail') AS num_guardrails_triggered,
                round(avgIf(latency_ms, isNotNull(latency_ms)), 2) AS avg_trace_latency,
                uniqExact(toDate(start_time)) AS num_active_days,
                uniqExactIf(trace_id, status = 'ERROR') AS num_traces_with_errors
            FROM resolved_candidate_spans
            GROUP BY end_user_id
        ),
        session_durations AS (
            SELECT
                end_user_id,
                trace_session_id,
                dateDiff('millisecond', min(start_time), max(end_time)) / 1000.0 AS duration_seconds
            FROM resolved_candidate_spans
            WHERE isNotNull(trace_session_id)
              AND trace_session_id != toUUID('00000000-0000-0000-0000-000000000000')
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
        metric_user_ids AS (
            SELECT end_user_id FROM extra_metrics
            UNION DISTINCT
            SELECT end_user_id FROM session_aggregates
        )
        SELECT
            ids.end_user_id AS end_user_id,
            coalesce(sa.num_sessions, 0) AS num_sessions,
            coalesce(sa.avg_session_duration, 0) AS avg_session_duration,
            coalesce(em.avg_trace_latency, 0) AS avg_trace_latency,
            coalesce(em.num_llm_calls, 0) AS num_llm_calls,
            coalesce(em.num_guardrails_triggered, 0) AS num_guardrails_triggered,
            coalesce(em.num_active_days, 0) AS num_active_days,
            coalesce(em.num_traces_with_errors, 0) AS num_traces_with_errors
        FROM metric_user_ids AS ids
        LEFT JOIN session_aggregates AS sa ON sa.end_user_id = ids.end_user_id
        LEFT JOIN extra_metrics AS em ON em.end_user_id = ids.end_user_id
        """
        return query, params

    def build_requested_page_metric_queries(
        self,
        end_user_ids: list[str],
        metric_keys: set[str] | frozenset[str] | tuple[str, ...] | list[str],
    ) -> list[tuple[str, dict[str, Any], tuple[str, ...]]]:
        """Build exact, column-minimal metric reads for a finite user page.

        Session and non-session metrics are separate statements so each
        latest-version replay keeps only the aggregate states it actually
        needs.  This trades at most one additional bounded scan for a much
        lower peak-memory ceiling; no result is sampled or approximated.
        """

        requested = set(metric_keys)
        supported = {
            "num_sessions",
            "avg_session_duration",
            "avg_trace_latency",
            "num_llm_calls",
            "num_guardrails_triggered",
            "num_active_days",
            "num_traces_with_errors",
        }
        requested &= supported
        if not end_user_ids or not requested:
            return []

        start_date, end_date = self.parse_time_range(self.filters)
        base_params: dict[str, Any] = {
            "candidate_end_user_ids": tuple(str(value) for value in end_user_ids),
            "start_date": start_date,
            "end_date": end_date,
        }
        if self.project_ids:
            base_params["project_ids"] = tuple(self.project_ids)
        else:
            base_params["project_id"] = self.project_id

        eu_map, finite_map_params = self._finite_end_user_map(
            candidate_param="candidate_end_user_ids"
        )
        base_params.update(finite_map_params)
        resolved_eu = resolved_id_expr("latest_end_user_id", "span_eu_remap")

        def common_prefix(state_selects: list[str], *, include_sessions: bool) -> str:
            remap_ctes = f"eu_survivor_map AS ({eu_map})"
            state_projection = "".join(
                f",\n                {state_select}" for state_select in state_selects
            )
            session_remap_ctes = ""
            if include_sessions:
                # Session ids are not known until the page's latest span
                # versions have been replayed. Build the survivor map only for
                # those touched groups; unmatched ids fall back to themselves.
                session_remap_ctes = """,
        candidate_session_ids AS (
            SELECT DISTINCT latest_trace_session_id AS trace_session_id
            FROM latest_candidate_spans
            WHERE isNotNull(latest_trace_session_id)
              AND latest_trace_session_id != toUUID(
                  '00000000-0000-0000-0000-000000000000'
              )
        ),
        touched_session_groups AS (
            SELECT DISTINCT new_id
            FROM trace_session_id_remap FINAL
            WHERE old_id IN (SELECT trace_session_id FROM candidate_session_ids)
               OR new_id IN (SELECT trace_session_id FROM candidate_session_ids)
        ),
        ts_survivor_map AS (
            SELECT
                arrayJoin(
                    arrayDistinct(arrayConcat(groupArray(old_id), [new_id]))
                ) AS any_id,
                argMin(old_id, toString(old_id)) AS survivor_id
            FROM trace_session_id_remap FINAL
            WHERE new_id IN (SELECT new_id FROM touched_session_groups)
            GROUP BY new_id
        )
                """
            return f"""
        WITH
        {remap_ctes},
        expanded_candidate_user_ids AS (
            SELECT any_id AS end_user_id
            FROM eu_survivor_map
            WHERE survivor_id IN %(candidate_end_user_ids)s
            UNION DISTINCT
            SELECT end_user_id
            FROM end_users FINAL
            WHERE end_user_id IN %(candidate_end_user_ids)s
        ),
        candidate_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN
                  toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND end_user_id IN (
                  SELECT end_user_id FROM expanded_candidate_user_ids
              )
        ),
        latest_candidate_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id
                {state_projection},
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN
                  toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
            )
            GROUP BY project_id, trace_id, id, start_time
        )
        {session_remap_ctes}
            """

        queries: list[tuple[str, dict[str, Any], tuple[str, ...]]] = []
        session_fields = tuple(
            field
            for field in ("num_sessions", "avg_session_duration")
            if field in requested
        )
        if session_fields:
            resolved_session = resolved_id_expr(
                "latest_trace_session_id", "span_ts_remap"
            )
            prefix = common_prefix(
                [
                    "argMax(tuple(trace_session_id), _version).1 "
                    "AS latest_trace_session_id",
                    "argMax(tuple(end_time), _version).1 AS latest_end_time",
                ],
                include_sessions=True,
            )
            projections = []
            if "num_sessions" in session_fields:
                projections.append("count() AS num_sessions")
            if "avg_session_duration" in session_fields:
                projections.append(
                    "round(avg(duration_seconds), 2) AS avg_session_duration"
                )
            query = f"""
            {prefix},
            resolved_candidate_spans AS (
                SELECT
                    {resolved_eu} AS end_user_id,
                    {resolved_session} AS trace_session_id,
                    start_time,
                    latest_end_time AS end_time
                FROM latest_candidate_spans
                LEFT JOIN eu_survivor_map AS span_eu_remap
                    ON latest_end_user_id = span_eu_remap.any_id
                LEFT JOIN ts_survivor_map AS span_ts_remap
                    ON latest_trace_session_id = span_ts_remap.any_id
                WHERE latest_is_deleted = 0
                  AND {resolved_eu} IN %(candidate_end_user_ids)s
                  AND isNotNull(latest_trace_session_id)
                  AND {resolved_session} != toUUID(
                      '00000000-0000-0000-0000-000000000000'
                  )
            ),
            session_durations AS (
                SELECT
                    end_user_id,
                    trace_session_id,
                    dateDiff(
                        'millisecond', min(start_time), max(end_time)
                    ) / 1000.0 AS duration_seconds
                FROM resolved_candidate_spans
                GROUP BY end_user_id, trace_session_id
            )
            SELECT
                toString(end_user_id) AS end_user_id,
                {", ".join(projections)}
            FROM session_durations
            GROUP BY end_user_id
            """
            queries.append((query, dict(base_params), session_fields))

        span_fields = tuple(
            field
            for field in (
                "avg_trace_latency",
                "num_llm_calls",
                "num_guardrails_triggered",
                "num_active_days",
                "num_traces_with_errors",
            )
            if field in requested
        )
        if span_fields:
            state_selects: list[str] = []
            projections: list[str] = []
            if "avg_trace_latency" in span_fields:
                state_selects.append(
                    "argMax(latency_ms, _version) AS latest_latency_ms"
                )
                projections.append(
                    "round(avgIf(latest_latency_ms, "
                    "isNotNull(latest_latency_ms)), 2) AS avg_trace_latency"
                )
            if {"num_llm_calls", "num_guardrails_triggered"} & set(span_fields):
                state_selects.append(
                    "argMax(observation_type, _version) AS latest_observation_type"
                )
            if "num_llm_calls" in span_fields:
                projections.append(
                    "countIf(latest_observation_type = 'llm') AS num_llm_calls"
                )
            if "num_guardrails_triggered" in span_fields:
                projections.append(
                    "uniqExactIf(trace_id, latest_observation_type = 'guardrail') "
                    "AS num_guardrails_triggered"
                )
            if "num_active_days" in span_fields:
                projections.append("uniqExact(toDate(start_time)) AS num_active_days")
            if "num_traces_with_errors" in span_fields:
                state_selects.append("argMax(status, _version) AS latest_status")
                projections.append(
                    "uniqExactIf(trace_id, latest_status = 'ERROR') "
                    "AS num_traces_with_errors"
                )
            prefix = common_prefix(state_selects, include_sessions=False)
            query = f"""
            {prefix}
            SELECT
                toString({resolved_eu}) AS end_user_id,
                {", ".join(projections)}
            FROM latest_candidate_spans
            LEFT JOIN eu_survivor_map AS span_eu_remap
                ON latest_end_user_id = span_eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_eu} IN %(candidate_end_user_ids)s
            GROUP BY end_user_id
            """
            queries.append((query, dict(base_params), span_fields))

        return queries

    def _build_candidate_first(self) -> tuple[str, dict[str, Any]]:
        """Select the user page from rollups, then enrich only page users.

        The raw enrichment replays each candidate physical span identity with
        ``argMax(_version)`` before accepting it, so a newer tombstone cannot
        leak into the metrics.  End-user and session ids are resolved through
        the many-to-one survivor maps before grouping.
        """

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

        output_where, output_params = self._output_where()
        self.params.update(output_params)

        empty_scope_filter = "AND 0 = 1" if self.empty_scope else ""
        search_filter = (
            "AND positionCaseInsensitive(eu.user_id, %(search)s) > 0"
            if self.search
            else ""
        )
        end_user_filter = (
            "AND eu.end_user_id = toUUID(%(end_user_id)s)" if self.end_user_id else ""
        )
        final_filter = f"WHERE {output_where}" if output_where else ""
        order_by = self._order_by()
        paginated = self.limit is not None and self.offset is not None
        if paginated:
            pagination = "LIMIT %(limit)s OFFSET %(offset)s"
            total_count_select = "count() OVER() AS total_count"
        elif self.max_rows is not None:
            pagination = "LIMIT %(max_rows)s"
            total_count_select = "0 AS total_count"
        else:
            pagination = ""
            total_count_select = "0 AS total_count"

        eu_map = _touched_survivor_map_subquery(
            remap_table="end_user_id_remap",
            candidate_cte="filtered_end_users_raw",
            candidate_column="end_user_id",
        )
        ts_map = _touched_survivor_map_subquery(
            remap_table="trace_session_id_remap",
            candidate_cte="candidate_trace_session_ids",
            candidate_column="trace_session_id",
        )
        resolved_curated_eu = resolved_id_expr("eu.end_user_id", "eu_remap")
        resolved_usage_eu = resolved_id_expr(
            "latest_usage_end_user_id", "usage_eu_remap"
        )
        resolved_latest_eu = resolved_id_expr("latest_end_user_id", "span_eu_remap")
        resolved_latest_session = resolved_id_expr(
            "latest_trace_session_id", "span_ts_remap"
        )

        query = f"""
        WITH
        filtered_end_users_raw AS (
            SELECT
                eu.project_id,
                eu.end_user_id,
                eu.user_id,
                eu.user_id_type,
                eu.user_id_hash,
                eu.first_seen,
                eu.version
            FROM end_users AS eu FINAL
            WHERE eu.organization_id = toUUID(%(org_id)s)
              AND eu.is_deleted = 0
              AND notEmpty(eu.user_id)
              AND {self._project_predicate("eu")}
              {empty_scope_filter}
              {search_filter}
              {end_user_filter}
        ),
        eu_survivor_map AS ({eu_map}),
        filtered_end_users AS (
            SELECT
                {resolved_curated_eu} AS end_user_id,
                argMax(eu.user_id, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id,
                argMax(eu.user_id_type, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id_type,
                argMax(eu.user_id_hash, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS user_id_hash,
                min(eu.first_seen) AS first_seen,
                argMax(eu.project_id, tuple(eu.end_user_id = {resolved_curated_eu}, eu.version)) AS project_id
            FROM filtered_end_users_raw AS eu
            LEFT JOIN eu_survivor_map AS eu_remap
                ON eu.end_user_id = eu_remap.any_id
            GROUP BY end_user_id
        ),
        expanded_filtered_end_user_ids AS (
            SELECT any_id AS end_user_id
            FROM eu_survivor_map
            WHERE survivor_id IN (SELECT end_user_id FROM filtered_end_users)
            UNION DISTINCT
            SELECT end_user_id
            FROM filtered_end_users
        ),
        usage_candidate_span_identities AS (
            SELECT DISTINCT
                project_id,
                trace_id,
                id,
                start_time
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND end_user_id IN (
                  SELECT end_user_id FROM expanded_filtered_end_user_ids
              )
        ),
        latest_usage_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_usage_end_user_id,
                argMax(tuple(end_time), _version).1 AS latest_usage_end_time,
                argMax(cost, _version) AS latest_usage_cost,
                argMax(total_tokens, _version) AS latest_usage_total_tokens,
                argMax(prompt_tokens, _version) AS latest_usage_prompt_tokens,
                argMax(completion_tokens, _version) AS latest_usage_completion_tokens,
                argMax(is_deleted, _version) AS latest_usage_is_deleted
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM usage_candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        exact_usage AS (
            SELECT
                {resolved_usage_eu} AS end_user_id,
                sum(latest_usage_cost) AS total_cost,
                sum(toInt64(latest_usage_total_tokens)) AS total_tokens,
                sum(toInt64(latest_usage_prompt_tokens)) AS input_tokens,
                sum(toInt64(latest_usage_completion_tokens)) AS output_tokens,
                uniqExact(trace_id) AS num_traces,
                max(latest_usage_end_time) AS last_active
            FROM latest_usage_spans
            LEFT JOIN eu_survivor_map AS usage_eu_remap
                ON latest_usage_end_user_id = usage_eu_remap.any_id
            WHERE latest_usage_is_deleted = 0
              AND {resolved_usage_eu} IN (
                  SELECT end_user_id FROM filtered_end_users
              )
            GROUP BY end_user_id
        ),
        base_rows AS (
            SELECT
                eu.user_id AS user_id,
                coalesce(xu.total_cost, 0) AS total_cost,
                coalesce(xu.total_tokens, 0) AS total_tokens,
                coalesce(xu.input_tokens, 0) AS input_tokens,
                coalesce(xu.output_tokens, 0) AS output_tokens,
                coalesce(xu.num_traces, 0) AS num_traces,
                eu.first_seen AS activated_at,
                xu.last_active AS last_active,
                eu.project_id AS project_id,
                eu.user_id_type AS user_id_type,
                eu.user_id_hash AS user_id_hash,
                eu.end_user_id AS end_user_id
            FROM filtered_end_users AS eu
            INNER JOIN exact_usage AS xu ON xu.end_user_id = eu.end_user_id
        ),
        candidate_users AS (
            SELECT
                *,
                {total_count_select}
            FROM base_rows
            {final_filter}
            {order_by}
            {pagination}
        ),
        expanded_candidate_user_ids AS (
            SELECT any_id AS end_user_id
            FROM eu_survivor_map
            WHERE survivor_id IN (SELECT end_user_id FROM candidate_users)
            UNION DISTINCT
            SELECT end_user_id FROM candidate_users
        ),
        candidate_span_identities AS (
            SELECT DISTINCT
                project_id,
                trace_id,
                id,
                start_time
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND end_user_id IN (
                  SELECT end_user_id FROM expanded_candidate_user_ids
              )
        ),
        latest_candidate_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(tuple(trace_session_id), _version).1 AS latest_trace_session_id,
                argMax(observation_type, _version) AS latest_observation_type,
                argMax(status, _version) AS latest_status,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(latency_ms, _version) AS latest_latency_ms,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE {self._project_predicate("spans")}
              AND toDate(start_time) BETWEEN toDate(%(start_date)s) AND toDate(%(end_date)s)
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
            )
            GROUP BY project_id, trace_id, id, start_time
        ),
        candidate_trace_session_ids AS (
            SELECT DISTINCT latest_trace_session_id AS trace_session_id
            FROM latest_candidate_spans
            WHERE isNotNull(latest_trace_session_id)
              AND latest_trace_session_id !=
                  toUUID('00000000-0000-0000-0000-000000000000')
        ),
        ts_survivor_map AS ({ts_map}),
        resolved_candidate_spans AS (
            SELECT
                {resolved_latest_eu} AS end_user_id,
                {resolved_latest_session} AS trace_session_id,
                trace_id,
                start_time,
                latest_end_time AS end_time,
                latest_latency_ms AS latency_ms,
                latest_observation_type AS observation_type,
                latest_status AS status
            FROM latest_candidate_spans
            LEFT JOIN eu_survivor_map AS span_eu_remap
                ON latest_end_user_id = span_eu_remap.any_id
            LEFT JOIN ts_survivor_map AS span_ts_remap
                ON latest_trace_session_id = span_ts_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_latest_eu} IN (
                  SELECT end_user_id FROM candidate_users
              )
        ),
        extra_metrics AS (
            SELECT
                end_user_id,
                countIf(observation_type = 'llm') AS num_llm_calls,
                uniqExactIf(trace_id, observation_type = 'guardrail') AS num_guardrails_triggered,
                round(avgIf(latency_ms, isNotNull(latency_ms)), 2) AS avg_trace_latency,
                uniqExact(toDate(start_time)) AS num_active_days,
                uniqExactIf(trace_id, status = 'ERROR') AS num_traces_with_errors
            FROM resolved_candidate_spans
            GROUP BY end_user_id
        ),
        session_durations AS (
            SELECT
                end_user_id,
                trace_session_id,
                dateDiff('millisecond', min(start_time), max(end_time)) / 1000.0 AS duration_seconds
            FROM resolved_candidate_spans
            WHERE isNotNull(trace_session_id)
              AND trace_session_id != toUUID('00000000-0000-0000-0000-000000000000')
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
        )
        SELECT
            cu.user_id AS user_id,
            cu.total_cost AS total_cost,
            cu.total_tokens AS total_tokens,
            cu.input_tokens AS input_tokens,
            cu.output_tokens AS output_tokens,
            cu.num_traces AS num_traces,
            coalesce(sa.num_sessions, 0) AS num_sessions,
            coalesce(sa.avg_session_duration, 0) AS avg_session_duration,
            coalesce(em.avg_trace_latency, 0) AS avg_trace_latency,
            coalesce(em.num_llm_calls, 0) AS num_llm_calls,
            coalesce(em.num_guardrails_triggered, 0) AS num_guardrails_triggered,
            cu.activated_at AS activated_at,
            cu.last_active AS last_active,
            coalesce(em.num_active_days, 0) AS num_active_days,
            coalesce(em.num_traces_with_errors, 0) AS num_traces_with_errors,
            0 AS bool_eval_pass_rate,
            0 AS avg_output_float,
            cu.project_id AS project_id,
            cu.user_id_type AS user_id_type,
            cu.user_id_hash AS user_id_hash,
            cu.end_user_id AS end_user_id,
            cu.total_count AS total_count
        FROM candidate_users AS cu
        LEFT JOIN session_aggregates AS sa ON sa.end_user_id = cu.end_user_id
        LEFT JOIN extra_metrics AS em ON em.end_user_id = cu.end_user_id
        {order_by}
        """
        return query, self.params

    def build(self) -> tuple[str, dict[str, Any]]:
        if self.supports_candidate_first_page():
            return self._build_candidate_first()

        # Observe Users list/export requests must never silently fall back to
        # the historical all-users/raw-spans aggregation.  That query is exact,
        # but on large tenants it is the known 15--30 second incident path.
        # Raw-derived filters/sorts need a separately proven bounded selector;
        # until one exists, fail closed so the API can return a sanitized,
        # retryable response instead of consuming the full query deadline.
        raise UnsupportedBoundedUserListQuery(
            "user list filter/sort is not supported by the exact bounded query path"
        )

    def build_eval_query(
        self,
        end_user_ids: list[str],
        *,
        allowed_eval_config_ids: list[str] | tuple[str, ...] | None = None,
        allowed_eval_config_ids_by_project: dict[str, list[str] | tuple[str, ...]]
        | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a lightweight eval-pass-rate query for a page of user IDs.

        Runs AFTER the main ``build()`` query returns the paginated page.
        Only joins eval_logger against the page's users (not all users),
        avoiding the expensive full-table FINAL scan in the hot path.
        """
        project_config_map = {
            str(project_id): tuple(
                dict.fromkeys(str(value) for value in config_ids if value)
            )
            for project_id, config_ids in (
                allowed_eval_config_ids_by_project or {}
            ).items()
            if project_id and config_ids
        }
        if not end_user_ids or not (allowed_eval_config_ids or project_config_map):
            return "", {}
        start_date, end_date = self.parse_time_range(self.filters)
        eval_table, eval_nd = self._EVAL_LOGGER_SOURCE(
            "eval_scan", include_cdc_tombstone_guard=True
        )
        params: dict[str, Any] = {
            "eval_eu_ids": tuple(end_user_ids),
            "start_date": start_date,
            "end_date": end_date,
        }
        if project_config_map:
            eval_scope_clauses = []
            for index, (config_project_id, config_ids) in enumerate(
                sorted(project_config_map.items())
            ):
                params[f"eval_project_id_{index}"] = config_project_id
                params[f"eval_config_ids_{index}"] = config_ids
                eval_scope_clauses.append(
                    "(ut.project_id = toUUID(%(eval_project_id_"
                    f"{index})s) AND eval_scan.custom_eval_config_id IN "
                    f"%(eval_config_ids_{index})s)"
                )
            eval_scope_filter = "(" + " OR ".join(eval_scope_clauses) + ")"
        else:
            params["allowed_eval_config_ids"] = tuple(
                str(value) for value in allowed_eval_config_ids or ()
            )
            eval_scope_filter = (
                "eval_scan.custom_eval_config_id IN %(allowed_eval_config_ids)s"
            )
        if self.project_ids:
            params["project_ids"] = tuple(self.project_ids)
            project_filter = "AND spans.project_id IN %(project_ids)s"
        elif self.project_id:
            params["project_id"] = self.project_id
            project_filter = "AND spans.project_id = %(project_id)s"
        else:
            project_filter = ""
        # Eval hydration also owns a finite page id set. Keep remap aggregation
        # proportional to that set instead of materializing the tenant-global
        # survivor window before applying the id predicate.
        eu_map, finite_map_params = self._finite_end_user_map(
            candidate_param="eval_eu_ids"
        )
        params.update(finite_map_params)
        resolved_eu = resolved_id_expr("latest_end_user_id", "eval_eu_remap")
        query = f"""
        WITH
        eu_survivor_map AS ({eu_map}),
        candidate_span_identities AS (
            SELECT DISTINCT project_id, trace_id, id, start_time
            FROM spans
            PREWHERE start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {project_filter}
              AND (
                  end_user_id IN %(eval_eu_ids)s
                  OR end_user_id IN (
                      SELECT any_id
                      FROM eu_survivor_map
                      WHERE survivor_id IN %(eval_eu_ids)s
                  )
              )
        ),
        latest_candidate_spans AS (
            SELECT
                project_id,
                trace_id,
                id,
                start_time,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM spans
            PREWHERE start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {project_filter}
              AND (project_id, trace_id, id, start_time) IN (
                  SELECT project_id, trace_id, id, start_time
                  FROM candidate_span_identities
              )
            GROUP BY project_id, trace_id, id, start_time
        ),
        user_traces AS (
            SELECT DISTINCT
                project_id,
                {resolved_eu} AS end_user_id,
                trace_id
            FROM latest_candidate_spans
            LEFT JOIN eu_survivor_map AS eval_eu_remap
                ON latest_end_user_id = eval_eu_remap.any_id
            WHERE latest_is_deleted = 0
              AND {resolved_eu} IN %(eval_eu_ids)s
        )
        SELECT
            ut.end_user_id AS end_user_id,
            round(
                100.0 * countIf(eval_scan.output_bool = 1)
                / nullIf(countIf(isNotNull(eval_scan.output_bool)), 0),
                2
            ) AS bool_eval_pass_rate,
            round(avg(eval_scan.output_float), 2) AS avg_output_float
        FROM {eval_table} AS eval_scan FINAL
        INNER JOIN user_traces AS ut
            ON eval_scan.trace_id = toUUIDOrNull(ut.trace_id)
        WHERE {eval_nd}
          AND {eval_scope_filter}
        GROUP BY ut.end_user_id
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
            return "ORDER BY last_active DESC NULLS LAST, end_user_id DESC"
        parts: list[str] = []
        has_end_user_tiebreaker = False
        for sort in self.sort_params:
            column_id = sort.get("column_id")
            column = self.OUTPUT_FILTER_MAP.get(column_id)
            if not column:
                continue
            direction = str(sort.get("direction") or "desc").upper()
            if direction not in ("ASC", "DESC"):
                direction = "DESC"
            parts.append(f"{column} {direction} NULLS LAST")
            has_end_user_tiebreaker = has_end_user_tiebreaker or column == "end_user_id"
        if parts and not has_end_user_tiebreaker:
            # Stable pagination: equal metric/label values must not reshuffle
            # between page requests as ClickHouse changes its parallel merge
            # order. UUID is globally unique, so it is a total-order suffix.
            parts.append("end_user_id DESC")
        return (
            f"ORDER BY {', '.join(parts)}"
            if parts
            else "ORDER BY last_active DESC NULLS LAST, end_user_id DESC"
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
