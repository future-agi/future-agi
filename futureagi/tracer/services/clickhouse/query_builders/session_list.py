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
from typing import Any

from tracer.services.clickhouse.query_builders.base import NIL_UUID, BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)


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
    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "session_start",
        "start_time": "session_start",
        "end_time": "session_end",
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
    }

    # Session-level filter columns that map to computed aggregates
    SESSION_FILTER_MAP: dict[str, str] = {
        "duration": "duration",
        "total_cost": "total_cost",
        "total_tokens": "total_tokens",
        "traces_count": "traces_count",
    }

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.user_id = user_id
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the session list query.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate span-level filters (exclude session-level aggregate filters)
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
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

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""

        # P3b step1.5 (DESIGN §3 / id_remap_sql): `_session_from_where` ALWAYS
        # resolves `trace_session_id` new→old (and `end_user_id` too when a user
        # filter is present), so the `GROUP BY trace_session_id` below unifies a
        # cross-cutover straddler's old + new session ids into ONE listed row
        # (closes the no-filter/bookmark browse double-list). Pre-flip the remap
        # joins are no-ops → byte-identical (result-set) to the committed bare
        # scan (gate B).
        time_where = "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
        from_where = self._session_from_where(
            self.params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

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
        {from_where}
        GROUP BY trace_session_id
        {having_fragment}
        {order_clause}
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_content_query(self, session_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch first/last messages for a page of session IDs.

        P3b step1.5 (DESIGN §3 / id_remap_sql): ``session_ids`` are the OLD
        curated ids emitted by the (resolved) browse ``build()``. A straddler's
        NEW-deterministic-id spans carry ``trace_session_id = new_id``, so we
        resolve each span's ``trace_session_id`` new→old through
        ``trace_session_id_remap`` and BOTH filter (``IN session_ids``) and
        ``GROUP BY`` the RESOLVED id — else the new-id spans are missed and a
        straddler's first/last message is computed off only its old-id half.
        Pre-flip the remap is a no-op → byte-identical (gate B).
        """
        if not session_ids:
            return "", {}
        params = {**self.params, "content_session_ids": tuple(session_ids)}
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")
        query = f"""
        SELECT
            trace_session_id AS session_id,
            argMin(input, start_time) AS first_message,
            argMax(input, start_time) AS last_message
        FROM (
            SELECT
                {resolved_ts} AS trace_session_id,
                rs.input AS input,
                rs.start_time AS start_time
            FROM (
                SELECT trace_session_id, input, start_time
                FROM {self.TABLE}
                WHERE {self.project_filter_sql()}
                  AND is_deleted = 0
                  AND (parent_span_id IS NULL OR parent_span_id = '')
            ) AS rs
            {ts_join}
        )
        WHERE trace_session_id IN %(content_session_ids)s
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

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
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

    def _build_simple_count_query(self) -> tuple[str, dict[str, Any]]:
        """Fast count using count(DISTINCT ...) — no GROUP BY needed."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # P3b step1.5: same id-remap-resolved scan as build() (trace_session_id
        # always, end_user_id when filtered) so `count(DISTINCT trace_session_id)`
        # unifies a straddler and the count matches the listed rows (else
        # has_more/pagination lies). Pre-flip a byte-identical no-op (gate B).
        time_where = "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
        from_where = self._session_from_where(
            params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

        query = f"""
        SELECT count(DISTINCT trace_session_id) AS total
        {from_where}
        """
        return query, params

    def _build_aggregated_count_query(self) -> tuple[str, dict[str, Any]]:
        """Full aggregation count — required when HAVING clauses exist."""
        span_filters = self._extract_span_filters()
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(span_filters)

        params = dict(self.params)
        params.update(extra_params)

        having_clauses = self._build_having_clauses()

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        having_fragment = f"HAVING {having_clauses}" if having_clauses else ""

        # P3b step1.5: same id-remap-resolved scan as build()/simple-count so the
        # HAVING-filtered session count unifies a straddler identically (group on
        # the resolved trace_session_id). Pre-flip a byte-identical no-op (gate B).
        time_where = "AND start_time >= %(start_date)s AND start_time < %(end_date)s"
        from_where = self._session_from_where(
            params,
            time_where=time_where,
            filter_fragment=filter_fragment,
        )

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
            {from_where}
            GROUP BY trace_session_id
            {having_fragment}
        )
        """
        return query, params

    def build_span_attributes_query(
        self, session_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
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
        # P3b step1.5 (DESIGN §3 / id_remap_sql): `session_ids` are OLD curated ids
        # from the resolved browse; resolve each span's `trace_session_id` new→old
        # so a straddler's NEW-id spans' attributes attach to the OLD session id
        # the page lists. Filter + project the RESOLVED id. Pre-flip: no-op (gate
        # B). The committed PREWHERE micro-opt becomes a WHERE (the id-remap join
        # dominates the cost at scale anyway).
        #
        # Single-level SELECT (NOT a nested re-projection): the v1→v2 rewrite turns
        # bare `span_attributes_raw` into `toJSONString(attributes_extra) AS
        # span_attributes_raw`; a `<alias>.span_attributes_raw` reference would be
        # mangled by that bare-token rewrite. So the JSON/Map attribute columns
        # stay BARE (CH binds them to `s` — the remap join has no such columns),
        # and only `trace_session_id` is read prefixed as `s.trace_session_id`
        # (not a rewrite-special token) to feed the resolve expression.
        ts_join = remap_left_join(
            "s.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("s.trace_session_id", "ts_remap")
        query = f"""
        SELECT
            {resolved_ts} AS session_id,
            span_attributes_raw,
            span_attr_str,
            span_attr_num
        FROM {self.TABLE} AS s
        {ts_join}
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND (
            (span_attributes_raw != '{{}}' AND span_attributes_raw != '')
            OR length(mapKeys(span_attr_str)) > 0
            OR length(mapKeys(span_attr_num)) > 0
          )
          AND {resolved_ts} IN %(attr_session_ids)s
        LIMIT 500
        """
        return query, params

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_sessions(
        rows: list[tuple],
        columns: list[str],
    ) -> list[dict[str, Any]]:
        """Convert ClickHouse rows to the session list response format.

        Args:
            rows: Raw rows from ClickHouse (dicts or tuples).
            columns: Column names.

        Returns:
            List of session dicts matching the frontend's expected shape.
        """
        results: list[dict[str, Any]] = []
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

    # Filter `column_id`s that select a SET of end-user UUIDs against the
    # spans `end_user_id` column. The cross-project user-detail page (and the
    # session view's `user_id` query param) inject one of these as a synthetic
    # `end_user_id IN (...)` filter via `trace_session.py` (it resolves the raw
    # `user_id` string to a list of curated `EndUser.id`s in PG, then passes
    # them here). These must NOT flow into `ClickHouseFilterBuilder.translate()`
    # — they are resolved through the id-remap on a wrapped layer instead (see
    # `_build_resolved_user_clause` / P3b step1.5), so a cross-cutover straddler
    # unifies. `user` is the FilterBuilder alias for `end_user_id`.
    _ENDUSER_ID_FILTER_COLS = frozenset({"end_user_id", "user"})

    def _extract_span_filters(self) -> list[dict]:
        """Extract filters that apply at the span level (pre-GROUP BY).

        Filters on aggregate columns (duration, total_cost, etc.) are
        handled separately via HAVING clauses. ``end_user_id``/``user``
        identity filters are ALSO excluded here — they are resolved through
        the id-remap by ``_build_resolved_user_clause`` (P3b step1.5) rather
        than compiled raw by ``ClickHouseFilterBuilder``.
        """
        span_filters: list[dict] = []
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id in self.SESSION_FILTER_MAP:
                continue
            if col_id in self._ENDUSER_ID_FILTER_COLS:
                continue
            span_filters.append(f)
        return span_filters

    def _build_resolved_user_clause(self, params: dict[str, Any]) -> str:
        """Build the id-remap-resolved end-user predicate for the session scan.

        Returns a WHERE-fragment that constrains the (already id-remap-resolved)
        ``end_user_id`` column, or ``""`` when there is no user filter. P3b
        step1.5 (DESIGN §3 / id_remap_sql): the user is selected by the OLD
        curated id(s) — ``self.user_id`` and/or the synthetic ``end_user_id``
        IN-filter both carry ``str(EndUser.id)`` values resolved in PG. A
        cross-cutover straddler's NEW (deterministic-id) spans carry
        ``end_user_id = new_id``; by binding this predicate to the RESOLVED
        (new→old) column produced by the wrapped scan, old + new spans select
        as ONE user. Pre-flip the resolved id == the span's own id, so the
        predicate is identical to the committed bare ``end_user_id = ...`` /
        ``IN (...)`` (gate B). Mutates ``params`` with any bound id values.

        Combines, when both present, ``self.user_id`` (equality) AND every
        extracted ``end_user_id``/``user`` filter (IN / NOT IN) with ``AND`` —
        matching how the committed code would have ``AND``-stitched a
        ``user_clause`` plus a synthetic-filter fragment.
        """
        clauses: list[str] = []

        if self.user_id:
            params["user_id"] = self.user_id
            clauses.append("end_user_id = %(user_id)s")

        eu_param_idx = 0
        for f in self.filters:
            col_id = f.get("column_id") or f.get("columnId")
            if col_id not in self._ENDUSER_ID_FILTER_COLS:
                continue
            config = f.get("filter_config") or f.get("filterConfig") or {}
            filter_op = config.get("filter_op") or config.get("filterOp")
            raw_val = config.get("filter_value", config.get("filterValue"))
            ids = raw_val if isinstance(raw_val, list) else [raw_val]
            ids = [str(v) for v in ids if v]
            if not ids:
                # An empty id-set means "match nothing" — preserve that
                # (the synthetic filter falls back to [NIL_UUID] upstream, but
                # guard here too so we never silently drop the constraint).
                clauses.append("0 = 1")
                continue
            outer_op = "NOT IN" if filter_op in ("not_equals", "not_in") else "IN"
            eu_param_idx += 1
            pname = f"eu_remap_{eu_param_idx}"
            params[pname] = tuple(ids)
            clauses.append(f"end_user_id {outer_op} %({pname})s")

        return " AND ".join(clauses)

    # Span columns the session aggregates read (kept narrow so the id-remap
    # wrap projects only what the GROUP BY needs).
    _SESSION_SCAN_COLS = (
        "trace_session_id",
        "trace_id",
        "start_time",
        "end_time",
        "cost",
        "total_tokens",
    )

    def _session_from_where(
        self,
        params: dict[str, Any],
        *,
        time_where: str,
        filter_fragment: str,
    ) -> str:
        """Return the ``FROM … WHERE …`` clause for a session aggregation query.

        P3b step1.5 (DESIGN §3 / id_remap_sql): the scan is ALWAYS wrapped in a
        derived table that resolves ``trace_session_id`` new→old through
        ``trace_session_id_remap`` and projects it AS ``trace_session_id``, so the
        outer ``GROUP BY trace_session_id`` / ``count(DISTINCT trace_session_id)``
        treat a cross-cutover straddler's OLD + NEW session ids as ONE session
        (else the no-filter/bookmark BROWSE lists it twice at the flip). When a
        user filter is present, ``end_user_id`` is ALSO resolved (its remap) and
        the resolved-id predicate binds in the outer ``WHERE`` so old + new spans
        select as ONE user. The session/root/time/project predicates +
        ``{filter_fragment}`` stay on the inner raw scan.

        GATE B: pre-flip every span's id lives in the remap ``old_id`` column, so
        NO span matches a ``new_id`` → ``resolved_id_expr`` (zero-uuid-guarded,
        NOT a COALESCE) returns each span's own id and the LEFT JOIN(s) add
        nothing → the wrapped scan is a transparent pass-through, byte-identical
        (result-set) to the committed bare scan. Mutates ``params`` via
        ``_build_resolved_user_clause``.
        """
        base_predicates = f"""{self.project_where()}
          AND trace_session_id IS NOT NULL
          AND trace_session_id != toUUID('{NIL_UUID}')
          AND (parent_span_id IS NULL OR parent_span_id = '')
          {time_where}
          {filter_fragment}"""

        resolved_user_clause = self._build_resolved_user_clause(params)

        # `trace_session_id` resolution is UNCONDITIONAL (closes the browse split);
        # `end_user_id` resolution is added only when a user filter needs it (its
        # join is otherwise dead weight). Both remap joins hang off the SAME inner
        # row `rs` → DISTINCT aliases (`ts_remap` / `eu_remap`).
        ts_join = remap_left_join(
            "rs.trace_session_id", "trace_session_id_remap", "ts_remap"
        )
        resolved_ts = resolved_id_expr("rs.trace_session_id", "ts_remap")

        # Inner scan projects the narrow aggregate cols; `end_user_id` is pulled
        # in only when the user filter needs to resolve+bind it.
        scan_cols = list(self._SESSION_SCAN_COLS)
        outer_select = [f"{resolved_ts} AS trace_session_id"] + [
            f"rs.{c} AS {c}" for c in scan_cols if c != "trace_session_id"
        ]
        joins = [ts_join]
        inner_extra = ""
        where_clause = ""
        if resolved_user_clause:
            eu_join = remap_left_join("rs.end_user_id", "end_user_id_remap", "eu_remap")
            resolved_eu = resolved_id_expr("rs.end_user_id", "eu_remap")
            outer_select.append(f"{resolved_eu} AS end_user_id")
            joins.append(eu_join)
            inner_extra = ", end_user_id"
            where_clause = f"\n        WHERE {resolved_user_clause}"

        inner_cols = ", ".join(scan_cols)
        outer_select_sql = ",\n                ".join(outer_select)
        joins_sql = "\n            ".join(joins)
        return f"""FROM (
            SELECT
                {outer_select_sql}
            FROM (
                SELECT {inner_cols}{inner_extra}
                FROM {self.TABLE}
                {base_predicates}
            ) AS rs
            {joins_sql}
        ){where_clause}"""

    def _build_having_clauses(self) -> str:
        """Build HAVING clause fragments for aggregate-level filters."""
        conditions: list[str] = []
        param_counter = 900  # Use high numbers to avoid conflicts

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
