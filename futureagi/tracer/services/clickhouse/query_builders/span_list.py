"""
Span List Query Builder for ClickHouse.

Replaces the ``list_spans_observe()`` method in ``tracer.views.observation_span``
with a three-phase ClickHouse query strategy:

Phase 1 -- Paginated spans from the denormalized ``spans`` table (all spans,
not just root spans).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for the page of
span IDs, grouped by ``(observation_span_id, custom_eval_config_id)``.

Phase 3 -- Annotations from ``model_hub_score FINAL`` for the page of
span IDs, grouped by ``(observation_span_id, label_id)``.

The three result sets are merged in Python to produce the final response.
"""

from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder


class SpanListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated span list (observe) view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of spans per page.
        filters: Frontend filter list.
        sort_params: Frontend sort specification list.
        eval_config_ids: List of ``CustomEvalConfig`` UUID strings.
        annotation_label_ids: List of ``AnnotationsLabels`` UUID strings.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    ANNOTATION_TABLE = "model_hub_score"

    SORT_FIELD_MAP: Dict[str, str] = {
        "created_at": "start_time",
        "start_time": "start_time",
        "latency": "latency_ms",
        "latency_ms": "latency_ms",
        "cost": "cost",
        "total_tokens": "total_tokens",
        "name": "name",
        "span_name": "name",
        "status": "status",
    }

    def __init__(
        self,
        project_id: Optional[str] = None,
        project_ids: Optional[List[str]] = None,
        page_number: int = 0,
        page_size: int = 50,
        filters: Optional[List[Dict]] = None,
        sort_params: Optional[List[Dict]] = None,
        eval_config_ids: Optional[List[str]] = None,
        annotation_label_ids: Optional[List[str]] = None,
        end_user_id: Optional[str] = None,
        project_version_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id=project_id, project_ids=project_ids, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.sort_params = sort_params or []
        self.eval_config_ids = eval_config_ids or []
        self.annotation_label_ids = annotation_label_ids or []
        self.end_user_id = end_user_id
        self.project_version_id = project_version_id

    # ------------------------------------------------------------------
    # Phase 1: Paginated span list
    # ------------------------------------------------------------------

    def build(self) -> Tuple[str, Dict[str, Any]]:
        """Build the Phase-1 query for paginated span data."""
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        )
        extra_where, extra_params = fb.translate(self.filters)
        self.params.update(extra_params)

        order_clause = fb.translate_sort(
            self.sort_params, field_map=self.SORT_FIELD_MAP
        )
        if not order_clause:
            order_clause = "ORDER BY start_time DESC"

        offset = self.page_number * self.page_size
        self.params["limit"] = self.page_size + 1  # +1 for has_more detection
        self.params["offset"] = offset

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        end_user_fragment = ""
        if self.end_user_id:
            end_user_fragment = "AND end_user_id = %(end_user_id)s"
            self.params["end_user_id"] = self.end_user_id

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            self.params["project_version_id"] = self.project_version_id

        # Light columns only — input/output fetched via build_content_query()
        query = f"""
        SELECT
            id,
            trace_id,
            name,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            cost,
            total_tokens,
            model,
            provider,
            end_user_id,
            created_at
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {end_user_fragment}
          {pv_fragment}
          {filter_fragment}
        {order_clause}
        LIMIT 1 BY id
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_content_query(self, span_ids: list) -> Tuple[str, Dict[str, Any]]:
        """Fetch input/output for a page of span IDs."""
        if not span_ids:
            return "", {}
        params = {**self.params, "content_span_ids": tuple(span_ids)}
        query = f"""
        SELECT id, input, output, span_attributes_raw
        FROM {self.TABLE}
        PREWHERE id IN %(content_span_ids)s
        WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0
        """
        return query, params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build a count query for total matching spans."""
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            query_mode=ClickHouseFilterBuilder.QUERY_MODE_SPAN,
        )
        extra_where, extra_params = fb.translate(self.filters)
        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""

        end_user_fragment = ""
        if self.end_user_id:
            end_user_fragment = "AND end_user_id = %(end_user_id)s"
            params["end_user_id"] = self.end_user_id

        pv_fragment = ""
        if self.project_version_id:
            pv_fragment = "AND project_version_id = %(project_version_id)s"
            params["project_version_id"] = self.project_version_id

        query = f"""
        SELECT uniqExact(id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {end_user_fragment}
          {pv_fragment}
          {filter_fragment}
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 2: Eval scores for a set of span IDs
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        span_ids: List[str],
    ) -> Tuple[str, Dict[str, Any]]:
        """Build the Phase-2 eval-scores query for a page of span IDs."""
        if not span_ids or not self.eval_config_ids:
            return "", {}

        params: Dict[str, Any] = {
            "span_ids": tuple(span_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        # Include errored rows but compute aggregates only over successful
        # rows (error = 0). ``error_count`` and ``success_count`` let the
        # pivot distinguish "no eval run" vs "all errored" vs a real
        # Pass/Fail/score value — surfaces an Error state on the UI instead
        # of a misleading 0/Fail.
        # ``str_lists`` keeps every non-errored ``output_str_list`` so the
        # pivot can compute per-choice percentages for CHOICES evals
        # (column shape: ``{config_id}**{choice}``).
        # ``output_str`` is Nullable(String) and most evaluators leave it
        # NULL. ClickHouse three-valued logic means ``NULL != 'ERROR'`` is
        # NULL (not TRUE), so a bare ``output_str != 'ERROR'`` guard
        # silently excludes every non-errored row with a NULL
        # ``output_str`` — collapsing ``success_count`` to 0, making
        # ``avg_score``/``pass_rate`` NaN, and leaving eval columns blank
        # on the span list. Use ``ifNull(...)`` to keep the comparison
        # NULL-safe.
        query = f"""
        SELECT
            observation_span_id,
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
          AND observation_span_id IN %(span_ids)s
          AND custom_eval_config_id IN %(eval_config_ids)s
        GROUP BY observation_span_id, custom_eval_config_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations for a set of span IDs
    # ------------------------------------------------------------------

    def build_annotation_query(
        self,
        span_ids: List[str],
    ) -> Tuple[str, Dict[str, Any]]:
        """Build the Phase-3 annotation query for a page of span IDs."""
        if not span_ids or not self.annotation_label_ids:
            return "", {}

        params: Dict[str, Any] = {
            "span_ids": tuple(span_ids),
            "label_ids": tuple(self.annotation_label_ids),
        }

        query = f"""
        SELECT
            observation_span_id,
            toString(label_id) AS label_id,
            anyLast(value) AS value
        FROM {self.ANNOTATION_TABLE} FINAL
        WHERE _peerdb_is_deleted = 0
          AND observation_span_id IN %(span_ids)s
          AND label_id IN %(label_ids)s
        GROUP BY observation_span_id, label_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    @staticmethod
    def pivot_eval_results(
        eval_rows: List[Dict],
    ) -> Dict[str, Dict[str, Any]]:
        """Pivot eval query results into a nested dict keyed by span_id.

        Returns:
            ``{span_id: {eval_config_id: score_value_or_error_marker}}``.
            Value is a number for successful evals or ``{"error": True}``
            when all rows for the (span, config) pair errored. For CHOICES
            evals (non-empty ``str_lists``) the value is a ``{choice: pct}``
            dict that the caller spreads into ``{config_id}**{choice}`` keys.
        """
        import json as _json

        result: Dict[str, Dict[str, Any]] = {}
        for row in eval_rows:
            span_id = str(row.get("observation_span_id", ""))
            config_id = str(row.get("eval_config_id", ""))
            avg_score = row.get("avg_score")
            pass_rate = row.get("pass_rate")
            success_count = row.get("success_count", 0) or 0
            error_count = row.get("error_count", 0) or 0
            str_lists = row.get("str_lists") or []

            # All rows errored — surface an explicit error marker so the
            # UI can render an error state (distinct from "no eval run").
            if success_count == 0 and error_count > 0:
                result.setdefault(span_id, {})[config_id] = {"error": True}
                continue

            # CHOICES eval: compute per-choice percentage across all
            # non-errored eval rows for this (span, config) pair.
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
                per_choice = {k: round(100.0 * v / total, 2) for k, v in counts.items()}
                result.setdefault(span_id, {})[config_id] = per_choice
                continue

            # Determine the score value matching PG format
            if avg_score is not None and avg_score != 0:
                score = round(avg_score * 100, 2)
            elif pass_rate is not None:
                score = round(pass_rate, 2)
            else:
                score = None

            result.setdefault(span_id, {})[config_id] = score

        return result

    @staticmethod
    def pivot_annotation_results(
        annotation_rows: List[Dict],
        label_types: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Pivot annotation query results into a nested dict keyed by span_id.

        Args:
            annotation_rows: Rows from the Phase-3 query.
            label_types: Optional mapping of label_id -> annotation type
                (NUMERIC, STAR, THUMBS_UP_DOWN, CATEGORICAL).

        Returns:
            ``{span_id: {label_id: annotation_value}}``.
        """
        import json

        label_types = label_types or {}
        result: Dict[str, Dict[str, Any]] = {}
        for row in annotation_rows:
            span_id = str(row.get("observation_span_id", ""))
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

            result.setdefault(span_id, {})[label_id] = value

        return result
