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
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


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

    # Mapping from sort column names the frontend sends to actual
    # ClickHouse column names on the root span.
    SORT_FIELD_MAP: Dict[str, str] = {
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
    AVAILABLE_COLUMNS: List[str] = [
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
        project_id: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: Optional[List[Dict]] = None,
        sort_params: Optional[List[Dict]] = None,
        eval_config_ids: Optional[List[str]] = None,
        project_version_id: Optional[str] = None,
        search: Optional[str] = None,
        columns: Optional[List[str]] = None,
        annotation_label_ids: Optional[List[str]] = None,
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
        self.start_date: Optional[datetime] = None
        self.end_date: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Phase 1: Paginated trace list
    # ------------------------------------------------------------------

    def build(self) -> Tuple[str, Dict[str, Any]]:
        """Build the Phase-1 query for paginated root-span trace data.

        Returns:
            A ``(query_string, params)`` tuple.  The query returns one row
            per trace with root-span metadata.
        """
        self.start_date, self.end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date

        # Translate attribute / metric filters
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
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
        self.params["limit"] = self.page_size + 1  # +1 for has_more detection
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

        # Phase 1: light columns only (no input/output/span_attr/metadata).
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
        query = f"""
        SELECT
            {select_clause}
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT 1 BY trace_id
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_content_query(self, trace_ids: List[str]) -> Tuple[str, Dict[str, Any]]:
        """Fetch heavy columns (input, output, attributes) for a page of traces.

        Uses PREWHERE on trace_id for fast point lookups — avoids scanning
        heavy columns for the entire table.
        """
        if not trace_ids:
            return "", {}

        params: Dict[str, Any] = {
            **self.params,
            "content_trace_ids": tuple(trace_ids),
        }

        query = f"""
        SELECT
            trace_id,
            input,
            output,
            span_attr_str,
            span_attr_num,
            metadata_map,
            trace_tags
        FROM {self.TABLE}
        PREWHERE trace_id IN %(content_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND _peerdb_is_deleted = 0
          AND (parent_span_id IS NULL OR parent_span_id = '')
        LIMIT 1 BY trace_id
        """
        return query, params

    def build_span_attributes_query(
        self, trace_ids: List[str]
    ) -> Tuple[str, Dict[str, Any]]:
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
            span_attributes_raw
        FROM {self.TABLE}
        PREWHERE trace_id IN %(attr_trace_ids)s
        WHERE {self.project_filter_sql()}
          AND _peerdb_is_deleted = 0
          AND span_attributes_raw != '{{}}'
          AND span_attributes_raw != ''
        """
        return query, params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build a query to count total matching traces (for pagination).

        Returns:
            A ``(query_string, params)`` tuple returning a single count.
        """
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
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
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {pv_fragment}
          {search_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Span count per trace (optional — only if columns include span_count)
    # ------------------------------------------------------------------

    def build_span_count_query(
        self, trace_ids: List[str]
    ) -> Tuple[str, Dict[str, Any]]:
        """Count spans and errors per trace for a page of trace IDs."""
        if not trace_ids:
            return "", {}

        params: Dict[str, Any] = {
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
          AND _peerdb_is_deleted = 0
        GROUP BY trace_id
        """
        return query, params

    @staticmethod
    def pivot_span_count_results(
        data: List[Dict],
    ) -> Dict[str, Dict[str, int]]:
        """Pivot span count results into ``{trace_id: {span_count, error_count}}``."""
        result: Dict[str, Dict[str, int]] = {}
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
        trace_ids: List[str],
    ) -> Tuple[str, Dict[str, Any]]:
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

        params: Dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        # Include errored rows but compute aggregates only over successful
        # rows (error = 0). ``success_count`` / ``error_count`` let the
        # pivot surface an explicit error state on the UI when every eval
        # row for a (trace, config) pair errored (distinct from "no eval
        # run" vs a real Pass/Fail/score). ``str_lists`` keeps every
        # non-errored ``output_str_list`` so the pivot can compute
        # per-choice percentages for CHOICES evals.
        # ``output_str`` is Nullable(String) and most evaluators leave it
        # NULL. ClickHouse three-valued logic means ``NULL != 'ERROR'`` is
        # NULL (not TRUE), so a bare ``output_str != 'ERROR'`` guard
        # silently excludes every non-errored row with a NULL
        # ``output_str`` — collapsing ``success_count`` to 0, making
        # ``avg_score``/``pass_rate`` NaN, and leaving eval columns blank
        # on the trace list. Use ``ifNull(...)`` to keep the comparison
        # NULL-safe.
        query = f"""
        SELECT
            trace_id,
            toString(custom_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN, which
            -- json.dumps(allow_nan=False) rejects. NULL serializes as null.
            ifNotFinite(avgIf(
                output_float,
                error = 0 AND ifNull(output_str, '') != 'ERROR'
            ), NULL) AS avg_score,
            ifNotFinite(avgIf(
                CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END,
                error = 0 AND ifNull(output_str, '') != 'ERROR'
            ), NULL) AS pass_rate,
            countIf(
                error = 0 AND ifNull(output_str, '') != 'ERROR'
            ) AS success_count,
            countIf(
                error = 1 OR ifNull(output_str, '') = 'ERROR'
            ) AS error_count,
            count() AS eval_count,
            groupArrayIf(
                output_str_list,
                error = 0 AND ifNull(output_str, '') != 'ERROR'
            ) AS str_lists
        FROM {self.EVAL_TABLE} FINAL
        WHERE _peerdb_is_deleted = 0
          AND trace_id IN %(trace_ids)s
          AND custom_eval_config_id IN %(eval_config_ids)s
        GROUP BY trace_id, custom_eval_config_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of trace IDs
    # ------------------------------------------------------------------

    ANNOTATION_TABLE = "model_hub_score"

    def build_annotation_query(
        self,
        trace_ids: List[str],
        annotation_label_ids: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Build annotation query for a page of trace IDs."""
        if not trace_ids or not annotation_label_ids:
            return "", {}

        params: Dict[str, Any] = {
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

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: List[Dict],
        label_types: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Pivot annotation results keyed by trace_id.

        Returns:
            ``{trace_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: Dict[str, Dict[str, Any]] = {}
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
        eval_rows: List[Tuple],
        eval_columns: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by trace_id.

        Args:
            eval_rows: Rows from the Phase-2 eval query.
            eval_columns: Column names for those rows.

        Returns:
            A dict of ``{trace_id: {eval_config_id: score_dict}}``.
        """
        result: Dict[str, Dict[str, Any]] = {}
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
                counts: Dict[str, int] = {}
                for lst in parsed:
                    for choice in set(lst):
                        counts[choice] = counts.get(choice, 0) + 1
                per_choice = {
                    k: round(100.0 * v / total, 2) for k, v in counts.items()
                }
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

            score_data = {
                "avg_score": (
                    round(avg_score * 100, 2) if _finite(avg_score) else None
                ),
                "pass_rate": (
                    round(pass_rate, 2) if _finite(pass_rate) else None
                ),
                "count": _get(row, "eval_count", 6, 0) or 0,
            }
            result.setdefault(trace_id, {})[config_id] = score_data

        return result
