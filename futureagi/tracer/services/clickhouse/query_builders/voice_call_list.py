"""
Voice Call List Query Builder for ClickHouse.

Replaces the ``list_voice_calls()`` method in ``tracer.views.trace`` with a
multi-phase ClickHouse query strategy:

Phase 1 -- Paginated root conversation spans from the denormalized ``spans``
table (``WHERE parent_span_id IS NULL AND observation_type = 'conversation'``).

Phase 2 -- Eval scores from ``tracer_eval_logger FINAL`` for those trace IDs.

Phase 3 -- Annotations from ``model_hub_score FINAL`` for those trace IDs.

Phase 4 -- Child spans for those trace IDs (for the observation_span field).

The result sets are merged in Python, with raw_log processing delegated to
the existing ``ObservabilityService.process_raw_logs()``.
"""

from typing import Any

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

# Hardcoded simulator phone numbers (must match FilterEngine)
VAPI_PHONE_NUMBERS = [
    "+18568806998",
    "+17755715840",
    "+13463424590",
    "+12175683677",
    "+12175696753",
    "+12175683493",
    "+12175681887",
    "+12176018447",
    "+12176018280",
    "+12175696862",
    "+19168660414",
    "+19163473349",
    "+18563161617",
    "+13463619738",
    "+19847339395",
]


class VoiceCallListQueryBuilder(BaseQueryBuilder):
    """Build queries for the paginated voice call list view.

    Args:
        project_id: Project UUID string.
        page_number: Zero-based page index.
        page_size: Number of calls per page.
        filters: Frontend filter list.
        eval_config_ids: Eval config UUID strings for Phase 2.
        remove_simulation_calls: Whether to exclude simulator calls.
    """

    TABLE = "spans"
    EVAL_TABLE = "tracer_eval_logger"
    ANNOTATION_TABLE = "model_hub_score"

    def __init__(
        self,
        project_id: str,
        page_number: int = 0,
        page_size: int = 10,
        filters: list[dict] | None = None,
        eval_config_ids: list[str] | None = None,
        remove_simulation_calls: bool = False,
        annotation_label_ids: list[str] | None = None,
        strict_filters: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.page_number = page_number
        self.page_size = page_size
        self.filters = filters or []
        self.eval_config_ids = eval_config_ids or []
        self.remove_simulation_calls = remove_simulation_calls
        self.annotation_label_ids = annotation_label_ids or []
        # Fail loud on an untranslatable filter (rule resolve) instead of
        # silently dropping it; the grid stays lenient. See TraceListQueryBuilder.
        self.strict_filters = strict_filters

    # ------------------------------------------------------------------
    # Phase 1: Paginated root conversation spans
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the Phase-1 query for paginated voice call data."""
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(
            self.filters, strict=self.strict_filters
        )
        self.params.update(extra_params)

        offset = self.page_number * self.page_size
        self.params["limit"] = (
            self.page_size + 1
        )  # fetch one extra for has_more detection
        self.params["offset"] = offset

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        simulation_filter = self._build_simulation_filter()

        # Light columns only — heavy span_attributes_raw fetched via
        # build_content_query() after pagination to avoid CH OOM.
        query = f"""
        SELECT
            trace_id,
            id AS span_id,
            observation_type,
            status,
            start_time,
            end_time,
            latency_ms,
            provider
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {filter_fragment}
          {simulation_filter}
        ORDER BY start_time DESC
        LIMIT 1 BY trace_id
        LIMIT %(limit)s
        OFFSET %(offset)s
        """
        return query, self.params

    def build_id_query(self) -> tuple[str, dict[str, Any]]:
        """Filtered conversation-root span ids only — same predicate/window as
        build(), no pagination/order. Lets the eval resolver select the same
        voice calls this list endpoint returns."""
        start_date, end_date = self.parse_time_range(self.filters)
        self.params["start_date"] = start_date
        self.params["end_date"] = end_date

        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(
            self.filters, strict=self.strict_filters
        )
        self.params.update(extra_params)
        filter_fragment = f"AND {extra_where}" if extra_where else ""

        query = f"""
        SELECT id
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {filter_fragment}
        ORDER BY start_time DESC
        LIMIT 1 BY trace_id
        """
        return query, self.params

    def build_content_query(self, span_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Fetch heavy attribute columns for a page of voice call span IDs."""
        if not span_ids:
            return "", {}
        params = {**self.params, "content_span_ids": tuple(span_ids)}
        query = f"""
        SELECT id AS span_id, span_attributes_raw, span_attr_str, span_attr_num, metadata_map
        FROM {self.TABLE}
        PREWHERE id IN %(content_span_ids)s
        WHERE project_id = %(project_id)s AND is_deleted = 0
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Build a query to count total matching voice calls."""
        fb = ClickHouseFilterBuilder(
            table=self.TABLE,
            annotation_label_ids=self.annotation_label_ids,
            project_id=self.project_id,
            project_ids=self.project_ids,
        )
        extra_where, extra_params = fb.translate(
            self.filters, strict=self.strict_filters
        )
        params = dict(self.params)
        params.update(extra_params)

        filter_fragment = f"AND {extra_where}" if extra_where else ""
        simulation_filter = self._build_simulation_filter()

        query = f"""
        SELECT uniqExact(trace_id) AS total
        FROM {self.TABLE}
        {self.project_where()}
          AND (parent_span_id IS NULL OR parent_span_id = '')
          AND observation_type = 'conversation'
          AND created_at >= %(start_date)s - INTERVAL 1 DAY
          AND start_time >= %(start_date)s
          AND start_time < %(end_date)s
          {filter_fragment}
          {simulation_filter}
        """
        return query, params

    def _build_simulation_filter(self) -> str:
        """Build SQL fragment to exclude simulator calls.

        NOTE: Simulation filtering is done in Python (post-Phase 1b) rather
        than in SQL, because the phone numbers live inside the heavy
        ``span_attributes_raw`` JSON blob and scanning it causes ClickHouse
        OOM.  This method is kept as a no-op to avoid breaking callers.
        """
        return ""

    # ------------------------------------------------------------------
    # Python-side simulation filter (used after Phase 1b)
    # ------------------------------------------------------------------

    @staticmethod
    def is_simulator_call(span_attrs: dict, provider: str) -> bool:
        """Return True if the call comes from a known simulator phone number.

        Called after Phase 1b when span_attributes_raw has been parsed.
        """
        raw_log = span_attrs.get("raw_log") or {}
        if provider == "vapi":
            phone = (raw_log.get("customer") or {}).get("number", "")
        elif provider == "retell":
            phone = raw_log.get("from_number", "")
        else:
            return False
        return phone in VAPI_PHONE_NUMBERS

    # ------------------------------------------------------------------
    # Phase 2: Eval scores
    # ------------------------------------------------------------------

    def build_eval_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build eval-scores query for a page of trace IDs."""
        if not trace_ids or not self.eval_config_ids:
            return "", {}

        params: dict[str, Any] = {
            "trace_ids": tuple(trace_ids),
            "eval_config_ids": tuple(self.eval_config_ids),
        }

        # Aggregates are computed only over *completed*, non-errored rows so a
        # non-terminal (pending/running) or skipped row never skews a score nor
        # masquerades as a real value. The per-status counts let the shared
        # pivot pick one cell state by the precedence
        # completed > errored > skipped > running > pending; ``success_count``
        # excludes non-terminal/skipped/errored rows via ``status NOT IN (...)``
        # (a bare ``error = 0`` guard also matches pending/running/skipped
        # rows). NOT-IN keeps legacy rows whose mirrored ``status`` is
        # empty/NULL counted as completed.
        # Column order must match what ``pivot_eval_results`` expects:
        # trace_id, eval_config_id, avg_score, pass_rate, success_count,
        # error_count, eval_count, str_lists — new per-status columns are
        # appended after ``str_lists`` so the pivot's positional fallbacks hold.
        query = f"""
        SELECT
            trace_id,
            toString(custom_eval_config_id) AS eval_config_id,
            -- ifNotFinite(, NULL): avgIf over an all-NULL group returns NaN,
            -- which json.dumps(allow_nan=False) rejects. NULL serializes as null.
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
        FROM {self.EVAL_TABLE} FINAL
        WHERE _peerdb_is_deleted = 0
          AND (deleted = 0 OR deleted IS NULL)
          AND trace_id IN %(trace_ids)s
          AND custom_eval_config_id IN %(eval_config_ids)s
        GROUP BY trace_id, custom_eval_config_id
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 3: Annotations
    # ------------------------------------------------------------------

    def build_annotation_query(
        self,
        trace_ids: list[str],
        annotation_label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build annotation query for a page of trace IDs.

        Returns per-annotator rows so the view can build the structured
        annotation format expected by the frontend:
        ``{score: N, annotators: {userId: {userId, userName, score}}}``
        """
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
            toString(s.annotator_id) AS user_id,
            s.value
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
        """
        return query, params

    # ------------------------------------------------------------------
    # Phase 4: Child spans per trace
    # ------------------------------------------------------------------

    def build_child_spans_query(
        self,
        trace_ids: list[str],
    ) -> tuple[str, dict[str, Any]]:
        """Build query to fetch child spans for voice call traces."""
        if not trace_ids:
            return "", {}

        params: dict[str, Any] = {
            "project_id": self.project_id,
            "trace_ids": tuple(trace_ids),
        }

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
            model,
            provider,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost,
            input,
            output,
            parent_span_id,
            span_attributes_raw,
            span_attr_str,
            span_attr_num,
            span_attr_bool,
            metadata_map,
            status_message,
            tags
        FROM {self.TABLE}
        WHERE project_id = %(project_id)s
          AND is_deleted = 0
          AND trace_id IN %(trace_ids)s
          AND parent_span_id IS NOT NULL
        ORDER BY start_time ASC
        """
        return query, params
