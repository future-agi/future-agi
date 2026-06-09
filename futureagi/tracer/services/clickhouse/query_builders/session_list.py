"""
Session List Query Builder for ClickHouse.

Replaces the ``list_sessions()`` method in ``tracer.views.trace_session``
with a ClickHouse query that groups the denormalized ``spans`` table by
``trace_session_id``.

Because the ``spans`` table denormalizes trace context (including session
ID) into every span row, we can compute per-session aggregates in a single
``GROUP BY`` without JOINs.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.utils.filter_operators import normalize_filter_op


class SessionListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated session list view.

    Computes per-session aggregates:
    - ``min(start_time)`` -- session start
    - ``max(end_time)`` -- session end
    - ``sum(cost)`` -- total cost
    - ``sum(total_tokens)`` -- total tokens
    - ``uniq(trace_id)`` -- number of traces (HyperLogLog, ~2% error)
    - ``argMin(input, start_time)`` -- first user message
    - ``argMax(input, start_time)`` -- last user message

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of sessions per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        user_id: Optional end-user ID to restrict sessions.
    """

    TABLE = "spans"

    # Mapping from frontend sort column names to ClickHouse expressions
    SORT_FIELD_MAP: Dict[str, str] = {
        "created_at": "session_start",
        "start_time": "session_start",
        "end_time": "session_end",
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
    }

    # Session-level filter columns that map to computed aggregates
    SESSION_FILTER_MAP: Dict[str, str] = {
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
    }

    # Columns that require a session-scoped subquery because they are
    # set on child spans, not root spans. The main query restricts to
    # root spans only, so filtering these directly would miss matches.
    SUBQUERY_FILTER_COLS = {"end_user_id"}

    def __init__(
        self,
        project_id: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: Optional[List[Dict]] = None,
        sort_params: Optional[List[Dict]] = None,
        user_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.user_id = user_id
        self.start_date: Optional[datetime] = None
        self.end_date: Optional[datetime] = None
        # Populated by _extract_end_user_ids() during build
        self._end_user_ids: Optional[List[str]] = None

    def build(self) -> Tuple[str, Dict[str, Any]]:
        """Build the session list query.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Extract end_user_id filters — these need a subquery because
        # end_user_id is set on child spans, not root spans.
        self._end_user_ids = self._extract_end_user_ids()

        # Translate span-level filters (exclude session-level aggregate
        # filters AND end_user_id filters handled via subquery)
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(table=self.TABLE)
        extra_where, extra_params = fb.translate(span_filters)
        self.params.update(extra_params)

        # Build HAVING clauses for aggregate-level filters
        having_clauses = self._build_having_clauses()

        # Sorting
        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = "ORDER BY session_start DESC"

        # Pagination
        offset = self.page_number * self.page_size
        self.params["limit"] = self.page_size + 1  # +1 for has_more
        self.params["offset"] = offset

        # Optional user filter (legacy path via self.user_id kwarg)
        user_clause = ""
        if self.user_id:
            self.params["user_id"] = self.user_id
            user_clause = "AND end_user_id = %(user_id)s"

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        end_user_clause = self._build_end_user_subquery()

        # Light aggregation — no input column (heavy). First/last messages
        # fetched separately via build_content_query().
        query = f"""
        SELECT
            trace_session_id AS session_id,
            min(start_time) AS session_start,
            max(end_time) AS session_end,
            dateDiff('second', min(start_time), max(end_time)) AS duration,
            sum(cost) AS total_cost,
            sum(total_tokens) AS total_tokens,
            uniq(trace_id) AS traces_count
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IS NOT NULL
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {user_clause}
          {end_user_clause}
          {filter_fragment}
        GROUP BY trace_session_id
        {having_fragment}
        {order_clause}
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_content_query(self, session_ids: List[str]) -> Tuple[str, Dict[str, Any]]:
        """Fetch first/last messages for a page of session IDs."""
        if not session_ids:
            return "", {}
        params = {**self.params, "content_session_ids": tuple(session_ids)}
        query = f"""
        SELECT
            trace_session_id AS session_id,
            argMin(input, start_time) AS first_message,
            argMax(input, start_time) AS last_message
        FROM {self.TABLE}
        WHERE {self.project_filter_sql()}
          AND _peerdb_is_deleted = 0
          AND trace_session_id IN %(content_session_ids)s
          AND (parent_span_id IS NULL OR parent_span_id = '')
        GROUP BY trace_session_id
        """
        return query, params

    def has_having_filters(self) -> bool:
        """Return True if any filters target aggregate columns (requiring HAVING)."""
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id in self.SESSION_FILTER_MAP:
                return True
        return False

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build a query to count total matching sessions (for pagination).

        Uses a fast ``count(DISTINCT ...)`` path when no HAVING clauses are
        needed, and falls back to the full aggregation subquery when aggregate
        filters (duration, cost, tokens, traces_count) are present.

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        if not self.has_having_filters():
            return self._build_simple_count_query()
        return self._build_aggregated_count_query()

    def _build_simple_count_query(self) -> Tuple[str, Dict[str, Any]]:
        """Fast count using count(DISTINCT ...) — no GROUP BY needed."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(table=self.TABLE)
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        user_clause = ""
        if self.user_id:
            params["user_id"] = self.user_id
            user_clause = "AND end_user_id = %(user_id)s"

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        end_user_clause = self._build_end_user_subquery()

        query = f"""
        SELECT count(DISTINCT trace_session_id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND trace_session_id IS NOT NULL
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {user_clause}
          {end_user_clause}
          {filter_fragment}
        """
        return query, params

    def _build_aggregated_count_query(self) -> Tuple[str, Dict[str, Any]]:
        """Full aggregation count — required when HAVING clauses exist."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(table=self.TABLE)
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        having_clauses = self._build_having_clauses()

        user_clause = ""
        if self.user_id:
            params["user_id"] = self.user_id
            user_clause = "AND end_user_id = %(user_id)s"

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""
        end_user_clause = self._build_end_user_subquery()

        # Select the aggregate aliases so HAVING on `duration`/`total_cost`/
        # `total_tokens`/`traces_count` resolves (otherwise CH raises Code 47
        # "Unknown expression identifier" — TH-4316).
        query = f"""
        SELECT count() AS total FROM (
            SELECT
                trace_session_id,
                dateDiff('second', min(start_time), max(end_time)) AS duration,
                sum(cost) AS total_cost,
                sum(total_tokens) AS total_tokens,
                uniq(trace_id) AS traces_count
            FROM {self.TABLE}
            {self.project_where()}
              AND trace_session_id IS NOT NULL
              AND trace_session_id != toUUID('{NIL_UUID}')
              AND (parent_span_id IS NULL OR parent_span_id = '')
              AND start_time >= %(start_date)s
              AND start_time < %(end_date)s
              {user_clause}
              {end_user_clause}
              {filter_fragment}
            GROUP BY trace_session_id
            {having_fragment}
        )
        """
        return query, params

    def build_span_attributes_query(
        self, session_ids: List[str]
    ) -> Tuple[str, Dict[str, Any]]:
        """Fetch span attributes for root spans belonging to the given sessions.

        Restricts to root spans only (where custom user-defined attributes
        are typically set) and caps results at 500 rows to prevent unbounded
        scans on sessions with many traces.

        Returns one row per root span with trace_session_id,
        span_attributes_raw, and typed Map columns (span_attr_str,
        span_attr_num) as fallback when the raw JSON blob is empty.
        """
        if not session_ids:
            return "", {}

        params = {**self.params, "attr_session_ids": tuple(session_ids)}
        query = f"""
        SELECT
            trace_session_id AS session_id,
            span_attributes_raw,
            span_attr_str,
            span_attr_num
        FROM {self.TABLE}
        PREWHERE trace_session_id IN %(attr_session_ids)s
        WHERE {self.project_filter_sql()}
          AND _peerdb_is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND (
            (span_attributes_raw != '{{}}' AND span_attributes_raw != '')
            OR length(mapKeys(span_attr_str)) > 0
            OR length(mapKeys(span_attr_num)) > 0
          )
        LIMIT 500
        """
        return query, params

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_sessions(
        rows: List[Tuple],
        columns: List[str],
    ) -> List[Dict[str, Any]]:
        """Convert ClickHouse rows to the session list response format.

        Args:
            rows: Raw rows from ClickHouse (dicts or tuples).
            columns: Column names.

        Returns:
            List of session dicts matching the frontend's expected shape.
        """
        results: List[Dict[str, Any]] = []
        col_idx = {name: i for i, name in enumerate(columns)}

        def _get(row, key, idx, default=None):
            if isinstance(row, dict):
                return row.get(key, default)
            return (
                row[col_idx.get(key, idx)]
                if len(row) > col_idx.get(key, idx)
                else default
            )

        for row in rows:
            session_id = str(_get(row, "session_id", 0, ""))
            if session_id == NIL_UUID:
                continue
            session_start = _get(row, "session_start", 1)
            session_end = _get(row, "session_end", 2)
            duration_val = _get(row, "duration", 3, 0)

            results.append(
                {
                    "session_id": session_id,
                    "session_name": None,
                    "start_time": (
                        session_start.isoformat()
                        if hasattr(session_start, "isoformat")
                        else session_start
                    ),
                    "end_time": (
                        session_end.isoformat()
                        if hasattr(session_end, "isoformat")
                        else session_end
                    ),
                    "duration": float(duration_val) if duration_val else 0,
                    "total_cost": float(_get(row, "total_cost", 4, 0) or 0),
                    "total_tokens": int(_get(row, "total_tokens", 5, 0) or 0),
                    "total_traces_count": int(_get(row, "traces_count", 6, 0) or 0),
                    "first_message": _get(row, "first_message", 7, "") or "",
                    "last_message": _get(row, "last_message", 8, "") or "",
                }
            )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_span_filters(self) -> List[Dict]:
        """Extract filters that apply at the span level (pre-GROUP BY).

        Filters on aggregate columns (duration, total_cost, etc.) are
        handled separately via HAVING clauses. Filters on
        ``SUBQUERY_FILTER_COLS`` (e.g. ``end_user_id``) are handled via
        session-scoped subqueries and also excluded here.
        """
        excluded = self.SESSION_FILTER_MAP.keys() | self.SUBQUERY_FILTER_COLS
        span_filters: List[Dict] = []
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in excluded:
                span_filters.append(f)
        return span_filters

    def _extract_end_user_ids(self) -> Optional[List[str]]:
        """Extract end_user_id values from filters.

        Returns a list of UUID strings if an ``end_user_id`` filter is
        present, or ``None`` if not.
        """
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id != "end_user_id":
                continue
            config = f.get("filter_config") or f.get("filterConfig", {})
            value = config.get("filter_value", config.get("filterValue"))
            if isinstance(value, list):
                return [str(v) for v in value if v]
            if value:
                return [str(value)]
        return None

    def _build_end_user_subquery(self) -> str:
        """Build a subquery clause restricting sessions to those with matching end_user_id.

        ``end_user_id`` is set on child spans, not root spans. The main
        session list query only looks at root spans. This subquery first
        finds ``trace_session_id`` values from ANY span (including child
        spans) where the user matches, then the outer query can aggregate
        root spans for those sessions.

        Returns an empty string when no end_user_id filter is active.
        """
        if not self._end_user_ids:
            return ""
        self.params["_eu_ids"] = tuple(self._end_user_ids)
        return (
            f"AND trace_session_id IN ("
            f"SELECT DISTINCT trace_session_id FROM {self.TABLE} "
            f"WHERE {self.project_filter_sql()} "
            f"AND _peerdb_is_deleted = 0 "
            f"AND end_user_id IN %(_eu_ids)s "
            f"AND trace_session_id IS NOT NULL"
            f")"
        )

    def _build_having_clauses(self) -> str:
        """Build HAVING clause fragments for aggregate-level filters."""
        conditions: List[str] = []
        param_counter = 900  # Use high numbers to avoid conflicts

        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in self.SESSION_FILTER_MAP:
                continue

            config = f.get("filter_config") or f.get("filterConfig", {})
            filter_op = normalize_filter_op(
                config.get("filter_op") or config.get("filterOp")
            )
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
