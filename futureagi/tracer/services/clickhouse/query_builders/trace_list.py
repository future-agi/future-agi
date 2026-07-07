"""
Trace List Query Builder for ClickHouse.

Replaces the ``list_traces()`` method in ``tracer.views.trace`` with a
two-phase ClickHouse query strategy:

Phase 1 -- Paginated trace IDs + root span data from the denormalized
``spans`` table (``WHERE parent_span_id IS NULL``).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for those
trace IDs, grouped by ``(trace_id, custom_eval_config_id)``.

The two result sets are merged in Python.
"""

import math
from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.eval_status import (
    non_terminal_eval_marker,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

# TODO: switch this to "start_time" once we create an index on that column .
TIME_FILTER_COLUMN = "created_at"  # Options: "created_at" | "start_time"


class TraceListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated trace list view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of traces per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        eval_config_ids: List of ``CustomEvalConfig`` UUID strings to
            fetch eval scores for.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    # Filter compiler class; the v2 list builder overrides this to the v2
    # builder so it reads the v2 dimension tables (end_users, etc.).
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilder

    # Mapping from sort column names the frontend sends to actual
    # ClickHouse column names on the root span.
    SORT_FIELD_MAP: dict[str, str] = {
        "created_at": "start_time",
        "start_time": "start_time",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "cost": "cost",
        "total_tokens": "total_tokens",
        "name": "trace_name",
        "trace_name": "trace_name",
        "status": "status",
    }

    # All available light columns for configurable column selection.
    AVAILABLE_COLUMNS: list[str] = [
        "trace_id",
        "trace_name",
        "name",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "model",
        "provider",
        "trace_session_id",
        "project_id",
    ]

    def __init__(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: list[dict] | None = None,
        sort_params: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        project_version_id: str | None = None,
        search: str | None = None,
        columns: list[str] | None = None,
        annotation_label_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.eval_config_ids = eval_config_ids or []
        self.project_version_id = project_version_id
        self.search = search.strip() if search else None
        self.columns = columns
        self.annotation_label_ids = annotation_label_ids or []
        self.start_date: datetime | None = None
        self.end_date: datetime | None = None

    # ------------------------------------------------------------------
    # Phase 1: Paginated trace list
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated root-span trace data.

        Returns:
            A ``(query_string, params)`` tuple.  The query returns one row
            per trace with root-span metadata.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate attribute / metric filters
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        # Sorting
        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = "ORDER BY start_time DESC"

        # Pagination
        offset = self.page_number * self.page_size
        self.params["limit"] = self.page_size
        self.params["offset"] = offset

        # Build optional filter fragment
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # Optional project_version_id filter (used by prototype tab)
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        # Search filter on trace_name
        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            self.params["search"] = f"%{self.search}%"

        # Configurable columns — only SELECT requested columns.
        # trace_id is always included.
        if self.columns:
            valid = [c for c in self.columns if c in self.AVAILABLE_COLUMNS]
            if "trace_id" not in valid:
                valid.insert(0, "trace_id")
            # Alias 'name' to 'span_name' for backward compatibility
            select_cols = []
            for c in valid:
                if c == "name":
                    select_cols.append("name AS span_name")
                else:
                    select_cols.append(c)
            select_clause = ",\n            ".join(select_cols)
        else:
            select_clause = """trace_id,
            trace_name,
            name AS span_name,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            cost,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            model,
            provider,
            trace_session_id,
            project_id"""

        # Phase 1: light columns only (no input/output/attrs/metadata).
        # Heavy columns are fetched in build_content_query() for just the
        # returned trace_ids — avoids OOM on large tables.
        #
        # `created_at` is the partition/sort key (`PARTITION BY
        # toYYYYMM(created_at)`, `ORDER BY (project_id, toDate(created_at),
        # trace_id, id)`). Adding a **lower bound only** on `created_at`
        # lets CH prune old partitions — without it, the existing
        # `start_time` filter alone triggers a full project scan because
        # `start_time` isn't indexed. `start_time` remains the semantic
        # bound so user-visible timestamps are respected exactly.
        #
        # NO UPPER BOUND on `created_at`: prod data shows 0.5% of rows
        # arrive >7 days late (SDK buffering, backfills, manual uploads);
        # an upper bound would silently drop them. A 1-day buffer on the
        # lower bound tolerates clock skew. This delivers 100% of the
        # pruning benefit (upper bound tested: zero additional win since
        # no row has `created_at` in the future).
        #
        # On a 3.5M-span project, 7d page-1 drops from 663ms/3.5M rows
        # to 256ms/306K rows (~2.5x faster, 91% less I/O).
        #
        # PERF: no `LIMIT 1 BY trace_id`. That clause deduped multi-root /
        # duplicate-version traces, but forced CH to read + full-sort EVERY
        # root span in the window before applying ORDER BY … LIMIT —
        # O(roots-in-window) memory that OOM-crashed the server at millions
        # of traces. Without it, `ORDER BY … LIMIT n` runs as a bounded
        # top-N (size-n heap, O(n) memory). Duplicate trace_ids on a page
        # (multi-root traces, un-merged ReplacingMergeTree versions) are
        # rare; the view dedups the returned page by trace_id in Python,
        # keeping the first occurrence — the same row `LIMIT 1 BY` kept.
        query = f"""
        SELECT
            {select_clause}
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_id_query(self) -> tuple[str, dict[str, Any]]:
        """Filtered trace ids only — same root-span predicate/window as build(),
        no pagination/order. Lets the eval resolver select the same traces this
        list endpoint returns."""
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            self.params["search"] = f"%{self.search}%"

        query = f"""
        SELECT trace_id
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        LIMIT 1 BY trace_id
        """
        return query, self.params

    def build_content_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch heavy columns (input, output, attributes) for a page of traces.

        Uses PREWHERE on trace_id for fast point lookups — avoids scanning
        heavy columns for the entire table.
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "content_trace_ids": tuple(trace_ids),
        }

        query = f"""
        SELECT
            trace_id,
            input,
            output,
            attrs_string,
            attrs_number,
            toJSONString(metadata) AS metadata,
            dictGetOrDefault('trace_dict', 'tags', toUUID(trace_id), '[]') AS trace_tags
        FROM {self.TABLE}
        PREWHERE trace_id IN %(content_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
        LIMIT 1 BY trace_id
        """
        return query, params

    def build_span_attributes_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Aggregate span attributes across all spans of each trace.

        Returns one row per trace with groupArrayDistinct for each attribute key.
        Skips raw/large content keys.
        """
        if not trace_ids:
            return "", {}

        params = {**self.params, "attr_trace_ids": tuple(trace_ids)}
        query = f"""
        SELECT
            trace_id,
            attributes_extra
        FROM {self.TABLE}
        PREWHERE trace_id IN %(attr_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND is_deleted = 0
          AND attributes_extra != '{{}}'
          AND attributes_extra != ''
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching traces (for pagination).

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        fb = self._FILTER_BUILDER_CLS(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
            # PERF: bound the trace-membership span subqueries the compiler
            # emits (model/status/attr/user filters) to the query's time
            # window — without this each filter scans the project's entire
            # span history. Safe here: this builder always binds
            # %(start_date)s before translate(). See filters.py.
            span_date_scope=True,
        )
        extra_where, extra_params = fb.translate(self.filters)
        # Merge params -- reuse the same start/end dates
        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        # Optional project_version_id filter
        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            params["project_version_id"] = self.project_version_id

        # Search filter (reuse from build())
        search_fragment = ""
        if self.search:
            search_fragment = "AND trace_name ILIKE %(search)s"
            params["search"] = f"%{self.search}%"

        # See comment in build() — lower-bound-only `created_at` filter
        # prunes old partitions. Drops 7d count from 716ms/3.5M rows to
        # 255ms/306K rows on a 3.5M-span project, without dropping any
        # rows that legitimately match the user's `start_time` window.

        query = f"""
        SELECT uniq(trace_id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND {TIME_FILTER_COLUMN} >= %(start_date)s
          AND {TIME_FILTER_COLUMN} < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Span count per trace (optional — only if columns include span_count)
    # ------------------------------------------------------------------

    def build_span_count_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Count spans and errors per trace for a page of trace IDs."""
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "sc_trace_ids": tuple(trace_ids),
        }
        query = f"""
        SELECT
            trace_id,
            count() AS span_count,
            countIf(status = 'ERROR') AS error_count
        FROM {self.TABLE}
        WHERE {self.project_filter_sql()}
          AND trace_id IN %(sc_trace_ids)s
          AND is_deleted = 0
        GROUP BY trace_id
        """
        return query, params

    @staticmethod
    def pivot_span_count_results(
        data: list[dict],
    ) -> dict[str, dict[str, int]]:
        """Pivot span count results into ``{trace_id: {span_count, error_count}}``."""
        result: dict[str, dict[str, int]] = {}
        for row in data:
            tid = str(row.get("trace_id", ""))
            if tid:
                result[tid] = {
                    "span_count": row.get("span_count", 0),
                    "error_count": row.get("error_count", 0),
                }
        return result

    # ------------------------------------------------------------------
    # Phase 2: Eval scores for a set of trace IDs
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build the Phase-2 eval-scores query for a page of trace IDs.

        Queries ``tracer_eval_logger FINAL`` grouped by
        ``(trace_id, custom_eval_config_id)`` to produce one aggregated
        score row per (trace, eval config) pair.

        Args:
            trace_ids: List of trace ID strings from Phase 1.

        Returns:
            A ``(query_string, params)`` tuple.  Returns empty query if
            no trace_ids or no eval_config_ids.
        """
        if not trace_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        # Partition-prune `tracer_eval_logger` (PARTITION BY toYYYYMM(created_at))
        # so the FINAL merge can skip months that cannot match this page.
        # The page of trace_ids was selected by build() within the user's
        # [start_date, end_date] window on `start_time`, so the matching eval
        # rows' `created_at` falls inside that window plus ingestion skew. A
        # lower-bound-only filter with a 1-day skew buffer (identical to the
        # mitigation in build()/build_count_query()) prunes old partitions
        # without dropping any legitimately-matching eval row. Guarded on
        # self.start_date so callers that invoke build_eval_query() without a
        # prior build() (e.g. unit tests) keep their current behavior.
        created_at_fragment = ""
        if self.start_date is not None:
            params["start_date"] = self.start_date
            created_at_fragment = "AND created_at >= %(start_date)s - INTERVAL 1 DAY"

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score nor
        # masquerades as a real value. The per-status counts let the pivot pick
        # one cell state per (trace, config) by the precedence
        # completed > errored > skipped > running > pending.
        # ``success_count`` excludes non-terminal/skipped/errored rows via
        # ``status NOT IN (...)``: a bare ``error = 0`` guard also matches
        # pending/running/skipped rows (they carry ``error = 0`` and a NULL
        # output). NOT-IN (rather than ``status = 'completed'``) keeps legacy
        # rows whose mirrored ``status`` is empty/NULL counted as completed.
        # ``str_lists`` keeps every completed ``output_str_list`` so the pivot
        # can compute per-choice percentages for CHOICES evals.
        # ``output_str`` is Nullable(String); ClickHouse 3-valued logic makes
        # ``NULL != 'ERROR'`` NULL (not TRUE), so use ``ifNull(...)`` to keep
        # the comparison NULL-safe.
        # New per-status columns are appended after ``str_lists`` so the pivot's
        # positional column fallbacks (0..7) stay valid.
        query = f"""
        SELECT
            trace_id,
            toString(custom_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN, which
            -- json.dumps(allow_nan=False) rejects. NULL serializes as null.
            ifNotFinite(avgIf(
                output_float,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS avg_score,
            ifNotFinite(avgIf(
                CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ), NULL) AS pass_rate,
            countIf(
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS success_count,
            countIf(
                error = 1 OR ifNull(output_str, '') = 'ERROR' OR status = 'errored'
            ) AS error_count,
            count() AS eval_count,
            groupArrayIf(
                output_str_list,
                error = 0 AND ifNull(output_str, '') != 'ERROR' AND status NOT IN ('pending', 'running', 'skipped', 'errored')
            ) AS str_lists,
            countIf(status = 'skipped') AS skipped_count,
            countIf(status = 'running') AS running_count,
            countIf(status = 'pending') AS pending_count,
            anyIf(skipped_reason, status = 'skipped') AS skipped_reason
        -- PERF: no table-level FINAL. FINAL forced a merge across the WHOLE
        -- eval table before the WHERE was applied, so a page of ~50 trace ids
        -- dragged a merge over tens of millions of rows — GBs of memory that
        -- OOM-crashed the server. Instead de-dup only the page-scoped slice:
        -- the inner scan is pruned to the page's trace ids (idx_trace_id
        -- bloom) + config ids + the created_at partition bound, then ORDER BY
        -- _peerdb_version DESC + LIMIT 1 BY id keeps the newest version of
        -- each eval row — verified identical to FINAL for live rows (status
        -- transitions collapse to the newest version). One accepted
        -- divergence: the not-deleted WHERE runs BEFORE dedup, so an eval
        -- whose newest un-merged version is a soft-delete marker transiently
        -- surfaces its previous version until the next merge.
        FROM (
            SELECT
                trace_id,
                custom_eval_config_id,
                output_float,
                output_bool,
                output_str,
                output_str_list,
                error,
                status,
                skipped_reason
            FROM {self.EVAL_TABLE}
            WHERE _peerdb_is_deleted = 0
              AND (deleted = 0 OR deleted IS NULL)
              AND trace_id IN %(trace_ids)s
              AND custom_eval_config_id IN %(eval_config_ids)s
              {created_at_fragment}
            ORDER BY _peerdb_version DESC
            LIMIT 1 BY id
        )
        GROUP BY trace_id, custom_eval_config_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of trace IDs
    # ------------------------------------------------------------------

    ANNOTATION_TABLE = "model_hub_score"

    def build_annotation_query(
        self,
        trace_ids: list[str],
        annotation_label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build annotation query for a page of trace IDs."""
        if not trace_ids or not annotation_label_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "label_ids": tuple(annotation_label_ids),
        }

        query = f"""
        SELECT
            if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
            ) AS trace_id,
            toString(s.label_id) AS label_id,
            anyLast(s.value) AS value,
            toString(anyLast(s.annotator_id)) AS annotator_id
        FROM {self.ANNOTATION_TABLE} AS s FINAL
        LEFT JOIN {self.TABLE} AS sp
          ON sp.id = s.observation_span_id
         AND sp._peerdb_is_deleted = 0
        WHERE s._peerdb_is_deleted = 0
          AND s.deleted = false
          AND if(
                isNull(s.trace_id)
                OR s.trace_id = toUUID('00000000-0000-0000-0000-000000000000'),
                sp.trace_id,
                toString(s.trace_id)
              ) IN %(trace_ids)s
          AND s.label_id IN %(label_ids)s
        GROUP BY trace_id, label_id
        """
        return query, params

    def build_user_id_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch user_id strings from ClickHouse for a page of trace IDs.

        Uses enduser_dict to resolve end_user_id UUIDs to user_id strings
        in a single query. Returns one user_id per trace (uses `any()`
        aggregation to pick the first non-null value across all spans).
        """
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "user_trace_ids": tuple(trace_ids),
        }

        query = f"""
        SELECT trace_id, user_id
        FROM (
            SELECT
                trace_id,
                dictGetOrDefault('enduser_dict', 'user_id', any(end_user_id), '') AS user_id
            FROM {self.TABLE}
            PREWHERE trace_id IN %(user_trace_ids)s
            WHERE {self.project_filter_sql()}
              AND _peerdb_is_deleted = 0
              AND end_user_id IS NOT NULL
              AND end_user_id != toUUID('00000000-0000-0000-0000-000000000000')
            GROUP BY trace_id
        )
        WHERE user_id != ''
        """
        return query, params

    def resolve_user_ids(self, trace_ids: list[str], analytics) -> dict[str, str]:
        """Resolve user_id strings for a page of trace IDs.

        Single-query lookup using ClickHouse enduser_dict:
        - Queries ClickHouse for user_id strings via dictionary lookup (~50-100ms)
        - No PostgreSQL round-trip needed

        Args:
            trace_ids: List of trace ID strings to resolve users for.
            analytics: Analytics service instance for executing CH queries.

        Returns:
            Dict mapping trace_id → user_id string.
        """
        if not trace_ids:
            return {}

        user_query, user_params = self.build_user_id_query(trace_ids)
        if not user_query:
            return {}

        result = analytics.execute_ch_query(user_query, user_params, timeout_ms=10000)

        # Build trace_id → user_id mapping (filter already applied in query)
        user_id_map = {
            str(row.get("trace_id", "")): row.get("user_id")
            for row in result.data
            if row.get("user_id")
        }

        return user_id_map

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: list[dict],
        label_types: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Pivot annotation results keyed by trace_id.

        Returns:
            ``{trace_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: dict[str, dict[str, Any]] = {}
        for row in annotation_rows:
            trace_id = str(row.get("trace_id", ""))
            label_id = str(row.get("label_id", ""))
            label_type = label_types.get(label_id, "").lower()

            raw_val = row.get("value", "{}")
            if isinstance(raw_val, str):
                try:
                    val = json.loads(raw_val)
                except (json.JSONDecodeError, TypeError):
                    val = {}
            else:
                val = raw_val if isinstance(raw_val, dict) else {}

            if label_type in ("numeric", "star"):
                value_key = "value" if label_type == "numeric" else "rating"
                value = val.get(value_key) if isinstance(val, dict) else val
            elif label_type == "thumbs_up_down":
                thumb_val = val.get("value") if isinstance(val, dict) else val
                value = thumb_val in (True, "up", 1, "true")
            elif label_type == "categorical":
                value = val.get("selected", []) if isinstance(val, dict) else val
            elif label_type == "text":
                value = val.get("text", val) if isinstance(val, dict) else val
            else:
                value = val

            result.setdefault(trace_id, {})[label_id] = value

        return result

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    @staticmethod
    def pivot_eval_results(
        eval_rows: list[tuple],
        eval_columns: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by trace_id.

        Args:
            eval_rows: Rows from the Phase-2 eval query.
            eval_columns: Column names for those rows.

        Returns:
            A dict of ``{trace_id: {eval_config_id: score_dict}}``.
        """
        result: dict[str, dict[str, Any]] = {}
        col_idx = {name: i for i, name in enumerate(eval_columns)}

        def _get(row, key, idx, default=None):
            if isinstance(row, dict):
                return row.get(key, default)
            return (
                row[col_idx.get(key, idx)]
                if len(row) > col_idx.get(key, idx)
                else default
            )

        import json as _json

        for row in eval_rows:
            trace_id = str(_get(row, "trace_id", 0, ""))
            config_id = str(_get(row, "eval_config_id", 1, ""))
            avg_score = _get(row, "avg_score", 2)
            pass_rate = _get(row, "pass_rate", 3)
            success_count = _get(row, "success_count", 4, 0) or 0
            error_count = _get(row, "error_count", 5, 0) or 0
            str_lists = _get(row, "str_lists", 7, []) or []

            # All rows errored — surface an explicit error marker so the
            # UI can render an error state (distinct from "no eval run").
            if success_count == 0 and error_count > 0:
                result.setdefault(trace_id, {})[config_id] = {"error": True}
                continue

            # CHOICES eval: compute per-choice percentage across all
            # non-errored eval rows for this (trace, config) pair. Caller
            # spreads into ``{config_id}**{choice}`` columns.
            #
            # ClickHouse stores ``output_str_list`` as ``String DEFAULT '[]'``,
            # so non-CHOICES evals (Pass/Fail, score) come back as the string
            # ``'[]'`` — truthy, slipping past the ``if not sl`` guard. Only
            # treat entries with actual choice values as CHOICES data; empty
            # inner lists must fall through to ``avg_score``/``pass_rate``.
            parsed = []
            for sl in str_lists:
                if not sl:
                    continue
                if isinstance(sl, list):
                    if sl:
                        parsed.append([str(x) for x in sl])
                elif isinstance(sl, str) and sl.startswith("["):
                    try:
                        p = _json.loads(sl)
                        if isinstance(p, list) and p:
                            parsed.append([str(x) for x in p])
                    except _json.JSONDecodeError:
                        continue
            if parsed:
                total = len(parsed)
                counts: dict[str, int] = {}
                for lst in parsed:
                    for choice in set(lst):
                        counts[choice] = counts.get(choice, 0) + 1
                per_choice = {k: round(100.0 * v / total, 2) for k, v in counts.items()}
                result.setdefault(trace_id, {})[config_id] = {
                    "per_choice": per_choice,
                }
                continue

            # ClickHouse ``avgIf`` returns NaN when no rows pass the
            # condition (or when all matching values are NULL). Python's
            # ``bool(float('nan'))`` is True, so a plain ``if avg_score``
            # guard leaks NaN into the JSON response and trips DRF's
            # strict encoder. Filter non-finite values explicitly.
            def _finite(v):
                return (
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                )

            avg_val = round(avg_score * 100, 2) if _finite(avg_score) else None
            pass_val = round(pass_rate, 2) if _finite(pass_rate) else None

            # No completed score: surface a non-terminal / skipped lifecycle
            # marker (skipped > running > pending) so the cell renders a
            # loading/pending/skipped state instead of a misleading blank.
            if avg_val is None and pass_val is None:
                marker = non_terminal_eval_marker(
                    {
                        "skipped_count": _get(row, "skipped_count", 8, 0) or 0,
                        "running_count": _get(row, "running_count", 9, 0) or 0,
                        "pending_count": _get(row, "pending_count", 10, 0) or 0,
                        "skipped_reason": _get(row, "skipped_reason", 11, None),
                    }
                )
                if marker is not None:
                    result.setdefault(trace_id, {})[config_id] = marker
                    continue

            score_data = {
                "avg_score": avg_val,
                "pass_rate": pass_val,
                "count": _get(row, "eval_count", 6, 0) or 0,
            }
            result.setdefault(trace_id, {})[config_id] = score_data

        return result
