"""
Eval Metrics Query Builder for ClickHouse.

Replaces ``get_eval_graph_data()`` and its helpers from
``tracer.utils.graphs_optimized`` with ClickHouse-native queries.

Strategy:
- Unfiltered eval dashboard queries read from the ``eval_metrics_hourly``
  pre-aggregated table.
- Filtered queries or per-eval-config breakdowns read from the
  ``tracer_eval_logger`` CDC table (with FINAL for correct deduplication).

Supports three eval output types:
- **float (SCORE):** ``avg(output_float) * 100`` per time bucket.
- **bool (PASS_FAIL):** ``avg(CASE WHEN output_bool = 1 THEN 100 ELSE 0 END)``
  per time bucket (pass rate as a percentage).
- **str_list (CHOICES):** For each choice, counts how often the choice
  appears in ``output_str_list``, expressed as a percentage of total evals.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

# Eval output type constants (mirrors EvalOutputType from Django models)
SCORE = "SCORE"
PASS_FAIL = "PASS_FAIL"
CHOICES = "CHOICES"


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
        choices: List of choice strings (required when ``eval_output_type``
            is ``"CHOICES"``).
        use_preaggregated: Whether to attempt reading from the pre-aggregated
            table.  Defaults to ``True``; set ``False`` when specific filters
            prevent using the aggregate table.
    """

    AGG_TABLE = "eval_metrics_hourly"
    RAW_TABLE = "tracer_eval_logger"

    def __init__(
        self,
        custom_eval_config_id: str,
        project_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        interval: str = "hour",
        eval_output_type: str = SCORE,
        eval_name: str = "",
        choices: Optional[List[str]] = None,
        use_preaggregated: bool = True,
        filters: Optional[List[dict]] = None,
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
        self.eval_output_type = eval_output_type
        self.eval_name = eval_name or "Unknown"
        self.choices = choices or []
        self.filters = filters or []
        # Pre-aggregated eval rows do not carry arbitrary trace/span filter
        # dimensions. If filters are present, force the raw logger path so the
        # graph reflects the filtered result set.
        self.use_preaggregated = use_preaggregated and not self.filters

        # Default time range
        if start_date is None or end_date is None:
            from datetime import timedelta

            self.end_date = end_date or datetime.utcnow()
            self.start_date = start_date or (self.end_date - timedelta(days=7))
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

        fb = ClickHouseFilterBuilder(project_ids=[self.project_id])
        extra_where, extra_params = fb.translate(self.filters)
        if extra_where:
            self.params.update(extra_params)
            return (
                f"AND trace_id IN ("
                f"SELECT DISTINCT trace_id FROM spans "
                f"WHERE project_id = %(project_id)s AND _peerdb_is_deleted = 0 "
                f"AND {extra_where})"
            )
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> Tuple[str, Dict[str, Any]]:
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
        rows: List[Tuple],
        columns: List[str],
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
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

    # ------------------------------------------------------------------
    # Score (float) queries
    # ------------------------------------------------------------------

    def _build_score_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build query for SCORE eval type: ``avg(output_float) * 100``."""
        if self.use_preaggregated:
            return self._build_score_agg()
        return self._build_score_raw()

    def _build_score_agg(self) -> Tuple[str, Dict[str, Any]]:
        """Score query against the pre-aggregated table."""
        bucket_fn = self.time_bucket_expr(self.interval)
        query = f"""
        SELECT
            {bucket_fn}(hour) AS time_bucket,
            (sum(float_sum) / greatest(sum(float_count), 1)) * 100
                AS value
        FROM {self.AGG_TABLE}
        WHERE project_id = %(project_id)s
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND hour >= %(start_date)s
          AND hour < %(end_date)s
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_score_raw(self) -> Tuple[str, Dict[str, Any]]:
        """Score query against the raw CDC table."""
        bucket_fn = self.time_bucket_expr(self.interval)
        filter_frag = self._filter_fragment()
        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            ifNotFinite(avg(output_float) * 100, NULL) AS value
        FROM {self.RAW_TABLE} FINAL
        WHERE project_id = %(project_id)s
          AND _peerdb_is_deleted = 0
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {filter_frag}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    # ------------------------------------------------------------------
    # Pass/Fail (bool) queries
    # ------------------------------------------------------------------

    def _build_pass_fail_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build query for PASS_FAIL eval type: pass rate as percentage."""
        if self.use_preaggregated:
            return self._build_pass_fail_agg()
        return self._build_pass_fail_raw()

    def _build_pass_fail_agg(self) -> Tuple[str, Dict[str, Any]]:
        """Pass/Fail query against the pre-aggregated table."""
        bucket_fn = self.time_bucket_expr(self.interval)
        query = f"""
        SELECT
            {bucket_fn}(hour) AS time_bucket,
            (sum(bool_pass) * 100.0)
                / greatest(sum(bool_pass) + sum(bool_fail), 1) AS value
        FROM {self.AGG_TABLE}
        WHERE project_id = %(project_id)s
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND hour >= %(start_date)s
          AND hour < %(end_date)s
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    def _build_pass_fail_raw(self) -> Tuple[str, Dict[str, Any]]:
        """Pass/Fail query against the raw CDC table."""
        bucket_fn = self.time_bucket_expr(self.interval)
        filter_frag = self._filter_fragment()
        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            ifNotFinite(avg(CASE WHEN output_bool = 1 THEN 100.0 ELSE 0.0 END), NULL)
                AS value
        FROM {self.RAW_TABLE} FINAL
        WHERE project_id = %(project_id)s
          AND _peerdb_is_deleted = 0
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {filter_frag}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    # ------------------------------------------------------------------
    # Choices (str_list) queries
    # ------------------------------------------------------------------

    def _build_choices_query(self) -> Tuple[str, Dict[str, Any]]:
        """Build query for CHOICES eval type.

        Generates per-choice percentage columns using ClickHouse's
        ``has()`` function for array containment checks.
        """
        bucket_fn = self.time_bucket_expr(self.interval)

        if not self.choices:
            # No choices defined -- return a simple count query
            return self._build_score_raw()

        # Build per-choice columns. ClickHouse stores output_str_list as a JSON
        # string, so parse it before calling has(); output_str is kept as the
        # single-choice fallback for older rows/imports.
        choice_cols: List[str] = []
        choice_array_expr = "JSONExtract(output_str_list, 'Array(String)')"
        for i, choice in enumerate(self.choices):
            param_name = f"choice_{i}"
            self.params[param_name] = choice
            choice_cols.append(
                f"countIf(has({choice_array_expr}, %({param_name})s) "
                f"OR output_str = %({param_name})s) * 100.0 "
                f"/ greatest(count(), 1) AS `choice_{i}`"
            )

        choice_select = ",\n            ".join(choice_cols)

        filter_frag = self._filter_fragment()
        query = f"""
        SELECT
            {bucket_fn}(created_at) AS time_bucket,
            count() AS total_count,
            {choice_select}
        FROM {self.RAW_TABLE} FINAL
        WHERE project_id = %(project_id)s
          AND _peerdb_is_deleted = 0
          AND custom_eval_config_id = toUUID(%(eval_config_id)s)
          AND created_at >= %(start_date)s
          AND created_at < %(end_date)s
          {filter_frag}
        GROUP BY time_bucket
        ORDER BY time_bucket
        """
        return query, self.params

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def _format_single_series(
        self,
        rows: List[Tuple],
        columns: List[str],
    ) -> Dict[str, Any]:
        """Format rows into a single-series eval result dict."""
        data_points = self.format_time_series(
            rows=rows,
            columns=columns,
            interval=self.interval,
            start_date=self.start_date,
            end_date=self.end_date,
            value_keys=["value"],
        )

        return {
            "name": self.eval_name,
            "data": data_points,
            "id": str(self.custom_eval_config_id),
        }

    def _format_choices_result(
        self,
        rows: List[Tuple],
        columns: List[str],
    ) -> List[Dict[str, Any]]:
        """Format choices query rows into multi-series result.

        Returns one series per choice option, each with the standard
        ``{name, data, id}`` shape.
        """
        results: List[Dict[str, Any]] = []

        for i, choice in enumerate(self.choices):
            col_name = f"choice_{i}"
            # Find column index for this choice
            col_idx = columns.index(col_name) if col_name in columns else None

            if col_idx is None:
                # Choice column not found; produce empty series
                data_points = self.format_time_series(
                    rows=[],
                    columns=["time_bucket", "value"],
                    interval=self.interval,
                    start_date=self.start_date,
                    end_date=self.end_date,
                    value_keys=["value"],
                )
            else:
                # Extract (time_bucket, choice_value) tuples
                def _get_val(row, key, idx):
                    if isinstance(row, dict):
                        return row.get(key, 0)
                    return row[idx] if len(row) > idx else 0

                choice_rows = [
                    (_get_val(row, "time_bucket", 0), _get_val(row, col_name, col_idx))
                    for row in rows
                ]
                data_points = self.format_time_series(
                    rows=choice_rows,
                    columns=["time_bucket", "value"],
                    interval=self.interval,
                    start_date=self.start_date,
                    end_date=self.end_date,
                    value_keys=["value"],
                )

            results.append(
                {
                    "name": f"{self.eval_name} - {choice}",
                    "data": data_points,
                    "id": str(self.custom_eval_config_id),
                }
            )

        return results
