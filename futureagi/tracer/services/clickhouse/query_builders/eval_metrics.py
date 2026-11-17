"""
Eval Metrics Query Builder for ClickHouse.

Replaces ``get_eval_graph_data()`` and its helpers from
``tracer.utils.graphs_optimized`` with ClickHouse-native queries.

Strategy:
- All quality metrics read latest raw eval rows (FINAL), then filter tombstones.
- Lifecycle classification precedes conditional sums/counts. Empty populations
  are null, with execution errors and work-state counts separate from quality.
- The lossy insert-time rollup is never a fallback.

Supports three eval output types:
- **float (SCORE):** ``avg(output_float) * 100`` per time bucket.
- **bool (PASS_FAIL):** ``avg(CASE WHEN output_bool = 1 THEN 100 ELSE 0 END)``
  per time bucket (pass rate as a percentage).
- **str_list (CHOICES):** For each choice, counts how often the choice
  appears in ``output_str_list``, expressed as a percentage of valid completed choice results.
"""

from datetime import datetime, timedelta
from typing import Any

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

# Eval output type constants (mirrors EvalOutputType from Django models)
SCORE = "SCORE"
PASS_FAIL = "PASS_FAIL"
CHOICES = "CHOICES"


def normalize_eval_output_type(value: Any) -> str:
    """Map persisted/model aliases to the query builder's canonical values.

    ``EvalTemplate.config["output"]`` stores the model-facing spellings
    (``score``, ``Pass/Fail``, and ``choices``), while older callers supplied
    the uppercase query constants.  Normalize once at the builder boundary so
    every API path selects the correct physical output column.
    """

    token = str(value or SCORE).strip().upper()
    token = token.replace("/", "_").replace("-", "_").replace(" ", "_")
    while "__" in token:
        token = token.replace("__", "_")

    aliases = {
        "SCORE": SCORE,
        "FLOAT": SCORE,
        "PASS_FAIL": PASS_FAIL,
        "PASSFAIL": PASS_FAIL,
        "BOOL": PASS_FAIL,
        "BOOLEAN": PASS_FAIL,
        "CHOICE": CHOICES,
        "CHOICES": CHOICES,
        "STR_LIST": CHOICES,
    }
    return aliases.get(token, SCORE)


class EvalMetricsQueryBuilder(BaseQueryBuilder):
    """Build time-series eval metric queries.

    The output matches the shape produced by ``_aggregate_for_standard_view``::

        {
            "name": "Eval Name",
            "data": [{"timestamp": "...", "value": 42.5}, ...],
            "id": "eval-config-uuid"
        }

    For the ``"charts"`` screen type with bool/choices evals, returns a list
    of such dicts (one per bool value or choice option).

    Args:
        custom_eval_config_id: UUID string of the eval config.
        project_id: Project UUID string.
        start_date: Start of the time range.
        end_date: End of the time range.
        interval: Time bucket interval.
        eval_output_type: One of ``"SCORE"``, ``"PASS_FAIL"``, ``"CHOICES"``.
        eval_name: Human-readable name of the eval config (for output).
        choices: Displayed choice strings (required when ``eval_output_type``
            is ``"CHOICES"``).
        valid_choices: Full configured vocabulary for scalar-result validity,
            independent of the displayed subset. Defaults to ``choices``.
        use_preaggregated: Retained for caller compatibility; ignored. Quality
            metrics always use latest raw rows, never the insert-time rollup.
    """

    _EVAL_LOGGER_SOURCE = staticmethod(eval_logger_source)
    _EVAL_TRACE_ID_EXPR = "trace_id"

    def __init__(
        self,
        custom_eval_config_id: str,
        project_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        interval: str = "hour",
        eval_output_type: str = SCORE,
        eval_name: str = "",
        choices: list[str] | None = None,
        use_preaggregated: bool = True,
        filters: list[dict] | None = None,
        valid_choices: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)

        # Resolve eval name to UUID if needed
        from tracer.utils.eval_helpers import resolve_eval_config_id

        custom_eval_config_id = resolve_eval_config_id(
            custom_eval_config_id, project_ids=getattr(self, "project_ids", None)
        )

        self.custom_eval_config_id = custom_eval_config_id
        self.interval = interval
        self.eval_output_type = normalize_eval_output_type(eval_output_type)
        self.eval_name = eval_name or "Unknown"
        self.choices = list(choices or [])
        self.valid_choices = list(
            self.choices if valid_choices is None else valid_choices
        )
        self.filters = filters or []
        self.use_preaggregated = False

        # Graph endpoints historically default to a compact 7-day window.
        # BaseQueryBuilder's list-view fallback is intentionally much wider,
        # so only use parse_time_range here when the caller supplied filters.
        if start_date is None or end_date is None:
            if self.filters:
                parsed_start, parsed_end = self.parse_time_range(self.filters)
            else:
                parsed_end = datetime.utcnow()
                parsed_start = parsed_end - timedelta(days=7)
            self.start_date = start_date or parsed_start
            self.end_date = end_date or parsed_end
        else:
            self.start_date = start_date
            self.end_date = end_date

        self.params["start_date"] = self.start_date
        self.params["end_date"] = self.end_date
        self.params["eval_config_id"] = self.custom_eval_config_id

    def _filter_fragment(self) -> str:
        """Build a trace_id IN subquery from filters, if any."""
        if not self.filters:
            return ""
        from tracer.services.clickhouse.query_builders.filters import (
            ClickHouseFilterBuilder,
        )

        fb = ClickHouseFilterBuilder(project_id=self.project_id)
        extra_where, extra_params = fb.translate(self.filters)
        if extra_where:
            self.params.update(extra_params)
            return (
                f"AND {self._EVAL_TRACE_ID_EXPR} IN ("
                f"SELECT DISTINCT trace_id FROM spans "
                f"WHERE project_id = %(project_id)s AND is_deleted = 0 "
                f"AND start_time >= %(start_date)s "
                f"AND start_time < %(end_date)s "
                f"AND {extra_where})"
            )
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> tuple[str, dict[str, Any]]:
        """Build the eval metrics query.

        Dispatches to the appropriate builder based on eval output type and
        whether pre-aggregated data is available.

        Returns:
            A ``(query_string, params)`` tuple.
        """
        if self.eval_output_type == SCORE:
            return self._build_score_query()
        elif self.eval_output_type == PASS_FAIL:
            return self._build_pass_fail_query()
        elif self.eval_output_type == CHOICES:
            return self._build_choices_query()
        else:
            # Fallback to score query
            return self._build_score_query()

    def format_result(
        self,
        rows: list[tuple],
        columns: list[str],
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Format the query results into the standard eval graph response.

        Args:
            rows: Raw rows from ClickHouse.
            columns: Column names.

        Returns:
            A dict (single series) or list of dicts (multi-series for
            charts screen).
        """
        if self.eval_output_type == CHOICES:
            return self._format_choices_result(rows, columns)
        else:
            return self._format_single_series(rows, columns)

    # The insert-time rollup cannot retract CDC versions or distinguish retained
    # error outputs. All entry points, including legacy *_agg callers, use raw.
    def _build_score_query(self):
        return self._build_score_raw()

    def _build_score_agg(self):
        return self._build_score_raw()

    def _build_pass_fail_query(self):
        return self._build_pass_fail_raw()

    def _build_pass_fail_agg(self):
        return self._build_pass_fail_raw()

    def _project_predicate(self):
        # Preserve the MV's span/trace/session dimension resolution. A missing
        # dimension fails closed (NULL), never the all-zero project default.
        return """if(raw_eval_logger.target_type = 'session',
            dictGetOrNull('trace_session_dict', 'project_id', raw_eval_logger.trace_session_id),
            dictGetOrNull('trace_dict', 'project_id', raw_eval_logger.trace_id)
        ) = toUUID(%(project_id)s)"""

    def _build_raw_query(self, valid_output, value_select, failure="0"):
        table, live = self._EVAL_LOGGER_SOURCE(
            "raw_eval_logger", include_cdc_tombstone_guard=True
        )
        if table.endswith("_v2"):
            # 011_eval_logger_v2 has neither status nor skipped_reason. Treating
            # those missing columns as completed would recreate the false-zero
            # bug. No lossy rollup or legacy-table fallback is permitted.
            # Import at read time: exact_graph_reads imports this builder.
            from tracer.services.clickhouse.exact_graph_reads import (
                EvalLifecycleUnavailable,
            )

            raise EvalLifecycleUnavailable(
                "Evaluation quality metrics are unavailable because the selected "
                "eval logger does not preserve lifecycle columns."
            )
        bucket = self.time_bucket_expr(self.interval)
        filters = self._filter_fragment()
        query = f"""
        WITH
            (ifNull(error, 0) != 0
                OR lowerUTF8(trimBoth(ifNull(output_str, ''))) = 'error'
                OR lowerUTF8(ifNull(status, 'completed')) IN
                    ('errored', 'error', 'failed', 'rate_limited', 'insufficient_credits')) AS nk_error,
            (NOT nk_error AND (ifNull(skipped_reason, '') != ''
                OR status = 'skipped')) AS nk_skipped,
            (NOT nk_error AND NOT nk_skipped AND status = 'completed') AS nk_completed,
            (nk_completed AND ({valid_output})) AS nk_valid
        SELECT
            {bucket}(created_at) AS time_bucket,
            {value_select},
            count() AS primary_traffic,
            count() AS count,
            countIf(nk_valid) AS scored_count,
            count() - countIf(nk_valid) AS unscored_count,
            countIf(nk_completed) AS completed_count,
            countIf(nk_error) AS error_count,
            countIf(NOT nk_error AND NOT nk_skipped AND status = 'pending') AS pending_count,
            countIf(NOT nk_error AND NOT nk_skipped AND status = 'running') AS running_count,
            countIf(nk_skipped) AS skipped_count,
            countIf(nk_valid AND ({failure})) AS failed_count,
            countIf(nk_error) * 100.0 / nullIf(countIf(nk_error OR nk_completed), 0) AS error_rate
        FROM (SELECT * FROM {table} FINAL) AS raw_eval_logger
        WHERE {live}
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          AND {self._project_predicate()}
          {filters}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_score_raw(self):
        return self._build_raw_query(
            "output_float IS NOT NULL AND isFinite(output_float)",
            "sumIf(ifNull(output_float, 0), nk_valid) * 100.0 / nullIf(countIf(nk_valid), 0) AS value",
            "output_float < 0.3",
        )

    def _build_pass_fail_raw(self):
        return self._build_raw_query(
            "output_bool IS NOT NULL AND output_bool IN (0, 1)",
            "countIf(nk_valid AND output_bool = 1) * 100.0 / nullIf(countIf(nk_valid), 0) AS value",
            "output_bool = 0",
        )

    def _build_choices_query(self):
        choices = "JSONExtract(ifNull(output_str_list, '[]'), 'Array(String)')"
        self.params["valid_choices"] = self.valid_choices
        valid = f"notEmpty({choices}) OR has(%(valid_choices)s, ifNull(output_str, ''))"
        columns = []
        for i, choice in enumerate(self.choices):
            self.params[f"choice_{i}"] = choice
            columns.append(
                f"countIf(nk_valid AND (has({choices}, %(choice_{i})s) OR output_str = %(choice_{i})s)) "
                f"* 100.0 / nullIf(countIf(nk_valid), 0) AS choice_{i}"
            )
        return self._build_raw_query(valid, ", ".join(columns) or "NULL AS value")

    _COUNT_KEYS = (
        "count",
        "primary_traffic",
        "scored_count",
        "unscored_count",
        "completed_count",
        "error_count",
        "pending_count",
        "running_count",
        "skipped_count",
        "failed_count",
    )

    def _format_points(self, rows, columns, value_column="value"):
        """Keep null scores and zero counts, including empty time buckets.

        BaseQueryBuilder.format_time_series intentionally zero-fills resource
        metrics; quality series must not pass through that formatter.
        """
        points = {}
        for row in rows:
            data = (
                row if isinstance(row, dict) else dict(zip(columns, row, strict=False))
            )
            timestamp = data.get("time_bucket")
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if timestamp is None:
                continue
            timestamp = self._normalize_timestamp(timestamp, self.interval)
            points[timestamp] = {
                "timestamp": timestamp.isoformat(),
                "value": data.get(value_column),
                "error_rate": data.get("error_rate"),
                **{key: data.get(key, 0) for key in self._COUNT_KEYS},
            }
        return [
            points.get(
                timestamp,
                {
                    "timestamp": timestamp.isoformat(),
                    "value": None,
                    "error_rate": None,
                    **dict.fromkeys(self._COUNT_KEYS, 0),
                },
            )
            for timestamp in self._generate_timestamp_range(
                self.start_date, self.end_date, self.interval
            )
        ]

    def _format_single_series(self, rows, columns):
        return {
            "name": self.eval_name,
            "id": str(self.custom_eval_config_id),
            "data": self._format_points(rows, columns),
        }

    def _format_choices_result(self, rows, columns):
        return [
            {
                "name": f"{self.eval_name} - {choice}",
                "id": str(self.custom_eval_config_id),
                "data": self._format_points(rows, columns, f"choice_{i}"),
            }
            for i, choice in enumerate(self.choices)
        ]
