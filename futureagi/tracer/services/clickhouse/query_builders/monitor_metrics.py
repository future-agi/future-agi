"""
Monitor Metrics Query Builder for ClickHouse.

Replaces the PostgreSQL ORM queries in ``tracer.utils.monitor`` and
``tracer.utils.monitor_graphs`` with ClickHouse-native SQL.

Supports all metric types defined in ``MonitorMetricTypeChoices``:
- COUNT_OF_ERRORS
- ERROR_RATES_FOR_FUNCTION_CALLING
- ERROR_FREE_SESSION_RATES
- SERVICE_PROVIDER_ERROR_RATES
- LLM_API_FAILURE_RATES
- SPAN_RESPONSE_TIME
- LLM_RESPONSE_TIME
- TOKEN_USAGE
- DAILY_TOKENS_SPENT
- MONTHLY_TOKENS_SPENT
- EVALUATION_METRICS

Two main query modes:
- ``build_metric_value_query`` -- returns a single scalar value
- ``build_time_series_query`` -- returns time-bucketed series
"""

from datetime import datetime
from typing import Any

import structlog

from tracer.services.clickhouse.eval_logger_table import (
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder, _parse_dt
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder

logger = structlog.get_logger(__name__)

# Mirror of MonitorMetricTypeChoices values
COUNT_OF_ERRORS = "count_of_errors"
ERROR_RATES_FOR_FUNCTION_CALLING = "error_rates_for_function_calling"
ERROR_FREE_SESSION_RATES = "error_free_session_rates"
SERVICE_PROVIDER_ERROR_RATES = "service_provider_error_rates"
LLM_API_FAILURE_RATES = "llm_api_failure_rates"
SPAN_RESPONSE_TIME = "span_response_time"
LLM_RESPONSE_TIME = "llm_response_time"
TOKEN_USAGE = "token_usage"
DAILY_TOKENS_SPENT = "daily_tokens_spent"
MONTHLY_TOKENS_SPENT = "monthly_tokens_spent"
EVALUATION_METRICS = "evaluation_metrics"

SPANS_TABLE = "spans"


class MonitorMetricsQueryBuilder(BaseQueryBuilder):
    """Build ClickHouse queries for monitor metric evaluation and graphing.

    Args:
        project_id: Project UUID string.
        filters: Raw monitor filters dict (the same JSON stored on the
            ``UserAlertMonitor.filters`` field).  These are translated to
            ClickHouse WHERE clauses via :class:`ClickHouseFilterBuilder`.
        eval_config_id: UUID string of the eval config (only needed for
            ``EVALUATION_METRICS``).
        eval_output_type: One of ``"SCORE"``, ``"PASS_FAIL"``, ``"CHOICES"``
            (only needed for ``EVALUATION_METRICS``).
        threshold_metric_value: The threshold metric value from the monitor
            (used for PASS_FAIL and CHOICES eval types).
    """

    # Legacy spans are time-ranged by their indexed arrival timestamp. CH25
    # overrides this with ``start_time``, its partition and primary-key time.
    _SPANS_TIME_COLUMN = "created_at"

    def __init__(
        self,
        project_id: str,
        filters: dict | None = None,
        eval_config_id: str | None = None,
        eval_output_type: str | None = None,
        threshold_metric_value: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project_id, **kwargs)
        self.raw_filters = filters or {}
        self.eval_config_id = eval_config_id
        self.eval_output_type = eval_output_type
        self.threshold_metric_value = threshold_metric_value

        # Translate monitor filters to CH WHERE fragments
        self._filter_clause = ""
        self._filter_params: dict[str, Any] = {}
        self._translate_filters()

    @staticmethod
    def _eval_choice_match_expr(param_name: str = "choice_val") -> str:
        """Return exact choice membership against CH's JSON-string list column."""
        choice_array = "JSONExtract(output_str_list, 'Array(String)')"
        return (
            f"(has({choice_array}, %({param_name})s) OR output_str = %({param_name})s)"
        )

    def _translate_filters(self) -> None:
        """Translate raw monitor filter JSON into CH WHERE clause fragments."""
        ch_conditions: list[str] = []
        params: dict[str, Any] = {}

        if not self.raw_filters:
            self._filter_clause = ""
            self._filter_params = {}
            return

        # Monitor queries bind start_time/end_time per query method, not
        # start_date/end_date. Disable score date pruning here so annotation
        # filters do not emit an unbound ``%(start_date)s`` placeholder.
        fb = ClickHouseFilterBuilder(table=SPANS_TABLE, score_date_scope=False)

        for key, value in self.raw_filters.items():
            if key == "span_attributes_filters" and isinstance(value, list):
                clause, p = fb.translate(value)
                if clause:
                    ch_conditions.append(clause)
                    params.update(p)
            elif key == "observation_type":
                pname = "mf_obs_type"
                if isinstance(value, list):
                    params[pname] = tuple(value)
                    ch_conditions.append(f"observation_type IN %({pname})s")
                elif isinstance(value, str):
                    params[pname] = value
                    ch_conditions.append(f"observation_type = %({pname})s")
            elif key == "session_id" and value:
                pname = "mf_session_id"
                params[pname] = str(value)
                ch_conditions.append(
                    f"trace_id IN ("
                    f"SELECT DISTINCT id FROM spans "
                    f"WHERE session_id = %({pname})s "
                    f"AND is_deleted = 0"
                    f")"
                )
            elif key == "date_range" and isinstance(value, list) and len(value) == 2:
                params["mf_dr_start"] = _parse_dt(value[0])
                params["mf_dr_end"] = _parse_dt(value[1])
                ch_conditions.append(
                    f"{self._SPANS_TIME_COLUMN} BETWEEN "
                    "%(mf_dr_start)s AND %(mf_dr_end)s"
                )
            elif key == "created_at" and value:
                params["mf_created_at"] = _parse_dt(value)
                ch_conditions.append(f"{self._SPANS_TIME_COLUMN} >= %(mf_created_at)s")
            elif key == "project_id":
                # Already handled by project_where()
                pass

        self._filter_clause = " AND ".join(ch_conditions) if ch_conditions else ""
        self._filter_params = params

    def build(self) -> tuple[str, dict[str, Any]]:
        """Not used directly -- use build_metric_value_query or build_time_series_query."""
        raise NotImplementedError(
            "Use build_metric_value_query() or build_time_series_query() instead."
        )

    # ------------------------------------------------------------------
    # Metric value query (single scalar)
    # ------------------------------------------------------------------

    def build_metric_value_query(
        self,
        metric_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[str, dict[str, Any]]:
        """Build a query that returns a single metric value for the time window.

        Returns:
            A ``(query_string, params_dict)`` tuple. The query returns a single
            row with a ``value`` column.
        """
        params = dict(self.params)
        params.update(self._filter_params)
        params["start_time"] = _parse_dt(start_time)
        params["end_time"] = _parse_dt(end_time)

        base_where = self._spans_base_where()

        if metric_type == COUNT_OF_ERRORS:
            query = f"""
                SELECT count() AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
                  AND status = 'ERROR'
            """

        elif metric_type == ERROR_RATES_FOR_FUNCTION_CALLING:
            query = f"""
                SELECT
                    CASE WHEN count() = 0 THEN NULL
                         ELSE countIf(status = 'ERROR') / count()
                    END AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
                  AND observation_type = 'tool'
            """

        elif metric_type == ERROR_FREE_SESSION_RATES:
            query = f"""
                SELECT
                    CASE WHEN uniqExact(session_id) = 0 THEN NULL
                         ELSE uniqExactIf(session_id, error_count = 0)
                              / uniqExact(session_id)
                    END AS value
                FROM (
                    SELECT
                        session_id,
                        countIf(status = 'ERROR') AS error_count
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND session_id != ''
                      AND session_id IS NOT NULL
                    GROUP BY session_id
                )
            """

        elif metric_type == SERVICE_PROVIDER_ERROR_RATES:
            query = f"""
                SELECT
                    CASE WHEN uniqExact(provider) = 0 THEN NULL
                         ELSE uniqExactIf(provider, error_count = 0)
                              / uniqExact(provider)
                    END AS value
                FROM (
                    SELECT
                        provider,
                        countIf(status = 'ERROR') AS error_count
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND provider != ''
                      AND provider IS NOT NULL
                    GROUP BY provider
                )
            """

        elif metric_type == LLM_API_FAILURE_RATES:
            query = f"""
                SELECT
                    CASE WHEN count() = 0 THEN NULL
                         ELSE countIf(status = 'ERROR') / count()
                    END AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
                  AND observation_type = 'llm'
            """

        elif metric_type == SPAN_RESPONSE_TIME:
            query = f"""
                SELECT avg(latency_ms) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
            """

        elif metric_type == LLM_RESPONSE_TIME:
            query = f"""
                SELECT avg(latency_ms) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
                  AND observation_type = 'llm'
            """

        elif metric_type == TOKEN_USAGE:
            query = f"""
                SELECT sum(total_tokens) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
            """

        elif metric_type == DAILY_TOKENS_SPENT:
            query = f"""
                SELECT sum(total_tokens) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} >= %(start_time)s
                  AND {self._SPANS_TIME_COLUMN} < %(end_time)s
            """

        elif metric_type == MONTHLY_TOKENS_SPENT:
            query = f"""
                SELECT sum(total_tokens) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} >= %(start_time)s
                  AND {self._SPANS_TIME_COLUMN} < %(end_time)s
            """

        elif metric_type == EVALUATION_METRICS:
            query, params = self._build_eval_metric_value_query(params)

        else:
            query = "SELECT NULL AS value"

        return query, params

    def _build_eval_metric_value_query(
        self, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Build the eval metric value query against the authoritative logger."""
        if not self.eval_config_id:
            return "SELECT NULL AS value", params

        params["eval_config_id"] = self.eval_config_id

        eval_rows = self._eval_rows_source()

        if self.eval_output_type == "SCORE":
            query = f"""
                SELECT ifNotFinite(avg(output_float), NULL) AS value
                FROM {eval_rows}
            """
        elif self.eval_output_type == "PASS_FAIL":
            output_bool_val = 1 if self.threshold_metric_value == "Passed" else 0
            params["output_bool_val"] = output_bool_val
            query = f"""
                SELECT avg(
                    CASE WHEN output_bool = %(output_bool_val)s THEN 1.0 ELSE 0.0 END
                ) AS value
                FROM {eval_rows}
            """
        elif self.eval_output_type == "CHOICES":
            if not self.threshold_metric_value:
                return "SELECT NULL AS value", params
            params["choice_val"] = self.threshold_metric_value
            choice_match = self._eval_choice_match_expr()
            query = f"""
                SELECT avg(
                    CASE WHEN {choice_match} THEN 1.0 ELSE 0.0 END
                ) AS value
                FROM {eval_rows}
            """
        else:
            query = "SELECT NULL AS value"

        return query, params

    # ------------------------------------------------------------------
    # Historical stats query (mean + stddev)
    # ------------------------------------------------------------------

    def build_historical_stats_query(
        self,
        metric_type: str,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[str, dict[str, Any]]:
        """Build a query that returns mean and stddev for historical analysis.

        For rate-based and latency metrics, computes per-row stats.
        For aggregated metrics (token usage, error counts), computes
        stats over time-bucketed values.

        Returns:
            A ``(query_string, params_dict)`` tuple with ``mean`` and ``stddev`` columns.
        """
        params = dict(self.params)
        params.update(self._filter_params)
        params["start_time"] = _parse_dt(start_time)
        params["end_time"] = _parse_dt(end_time)

        base_where = self._spans_base_where()

        if metric_type == ERROR_RATES_FOR_FUNCTION_CALLING:
            query = f"""
                SELECT
                    avg(is_error) AS mean,
                    stddevSamp(is_error) AS stddev
                FROM (
                    SELECT
                        CASE WHEN status = 'ERROR' THEN 1.0 ELSE 0.0 END AS is_error
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND observation_type = 'tool'
                )
            """

        elif metric_type == ERROR_FREE_SESSION_RATES:
            query = f"""
                SELECT
                    avg(is_error_free) AS mean,
                    stddevSamp(is_error_free) AS stddev
                FROM (
                    SELECT
                        CASE WHEN countIf(status = 'ERROR') > 0 THEN 0.0 ELSE 1.0 END AS is_error_free
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND session_id != ''
                      AND session_id IS NOT NULL
                    GROUP BY session_id
                )
            """

        elif metric_type == SERVICE_PROVIDER_ERROR_RATES:
            query = f"""
                SELECT
                    avg(is_error_free) AS mean,
                    stddevSamp(is_error_free) AS stddev
                FROM (
                    SELECT
                        CASE WHEN countIf(status = 'ERROR') > 0 THEN 0.0 ELSE 1.0 END AS is_error_free
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND provider != ''
                      AND provider IS NOT NULL
                    GROUP BY provider
                )
            """

        elif metric_type == LLM_API_FAILURE_RATES:
            query = f"""
                SELECT
                    avg(is_error) AS mean,
                    stddevSamp(is_error) AS stddev
                FROM (
                    SELECT
                        CASE WHEN status = 'ERROR' THEN 1.0 ELSE 0.0 END AS is_error
                    FROM {SPANS_TABLE}
                    {base_where}
                      AND {self._SPANS_TIME_COLUMN} BETWEEN
                          %(start_time)s AND %(end_time)s
                      AND observation_type = 'llm'
                )
            """

        elif metric_type == SPAN_RESPONSE_TIME:
            query = f"""
                SELECT
                    avg(latency_ms) AS mean,
                    stddevSamp(latency_ms) AS stddev
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
            """

        elif metric_type == LLM_RESPONSE_TIME:
            query = f"""
                SELECT
                    avg(latency_ms) AS mean,
                    stddevSamp(latency_ms) AS stddev
                FROM {SPANS_TABLE}
                {base_where}
                  AND {self._SPANS_TIME_COLUMN} BETWEEN
                      %(start_time)s AND %(end_time)s
                  AND observation_type = 'llm'
            """

        elif metric_type == EVALUATION_METRICS:
            query, params = self._build_eval_stats_query(params)

        else:
            # For COUNT_OF_ERRORS, TOKEN_USAGE, DAILY/MONTHLY_TOKENS_SPENT
            # these are handled via time-series aggregation in Python
            query = "SELECT NULL AS mean, NULL AS stddev"

        return query, params

    def _build_eval_stats_query(
        self, params: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Build eval metric stats (mean/stddev) query."""
        if not self.eval_config_id:
            return "SELECT NULL AS mean, NULL AS stddev", params

        params["eval_config_id"] = self.eval_config_id
        eval_rows = self._eval_rows_source()

        if self.eval_output_type == "SCORE":
            query = f"""
                SELECT
                    ifNotFinite(avg(output_float), NULL) AS mean,
                    ifNotFinite(stddevSamp(output_float), NULL) AS stddev
                FROM {eval_rows}
            """
        elif self.eval_output_type == "PASS_FAIL":
            output_bool_val = 1 if self.threshold_metric_value == "Passed" else 0
            params["output_bool_val"] = output_bool_val
            query = f"""
                SELECT
                    avg(pass_value) AS mean,
                    stddevSamp(pass_value) AS stddev
                FROM (
                    SELECT
                        CASE WHEN output_bool = %(output_bool_val)s THEN 1.0 ELSE 0.0 END AS pass_value
                    FROM {eval_rows}
                )
            """
        elif self.eval_output_type == "CHOICES":
            if not self.threshold_metric_value:
                return "SELECT NULL AS mean, NULL AS stddev", params
            params["choice_val"] = self.threshold_metric_value
            choice_match = self._eval_choice_match_expr()
            query = f"""
                SELECT
                    avg(choice_value) AS mean,
                    stddevSamp(choice_value) AS stddev
                FROM (
                    SELECT
                        CASE WHEN {choice_match} THEN 1.0 ELSE 0.0 END AS choice_value
                    FROM {eval_rows}
                )
            """
        else:
            query = "SELECT NULL AS mean, NULL AS stddev"

        return query, params

    # ------------------------------------------------------------------
    # Time series query (bucketed)
    # ------------------------------------------------------------------

    def build_time_series_query(
        self,
        metric_type: str,
        start_time: datetime,
        end_time: datetime,
        frequency_seconds: int,
    ) -> tuple[str, dict[str, Any]]:
        """Build a time-bucketed query for graph data.

        Returns:
            A ``(query_string, params_dict)`` tuple. The query returns rows with
            ``timestamp`` and ``value`` columns, ordered by timestamp.
        """
        params = dict(self.params)
        params.update(self._filter_params)
        params["start_time"] = _parse_dt(start_time)
        params["end_time"] = _parse_dt(end_time)
        params["freq_seconds"] = frequency_seconds

        span_bucket_expr = (
            "toDateTime(intDiv(toUInt32("
            f"{self._SPANS_TIME_COLUMN}"
            "), %(freq_seconds)s) * %(freq_seconds)s)"
        )

        base_where = self._spans_base_where()
        time_filter = (
            f"AND {self._SPANS_TIME_COLUMN} BETWEEN %(start_time)s AND %(end_time)s"
        )

        if metric_type in (TOKEN_USAGE, DAILY_TOKENS_SPENT, MONTHLY_TOKENS_SPENT):
            query = f"""
                SELECT
                    {span_bucket_expr} AS timestamp,
                    sum(total_tokens) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  {time_filter}
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == COUNT_OF_ERRORS:
            query = f"""
                SELECT
                    {span_bucket_expr} AS timestamp,
                    countIf(status = 'ERROR') AS value
                FROM {SPANS_TABLE}
                {base_where}
                  {time_filter}
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == SPAN_RESPONSE_TIME:
            query = f"""
                SELECT
                    {span_bucket_expr} AS timestamp,
                    avg(latency_ms) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  {time_filter}
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == LLM_RESPONSE_TIME:
            query = f"""
                SELECT
                    {span_bucket_expr} AS timestamp,
                    avg(latency_ms) AS value
                FROM {SPANS_TABLE}
                {base_where}
                  {time_filter}
                  AND observation_type = 'llm'
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type in (ERROR_RATES_FOR_FUNCTION_CALLING, LLM_API_FAILURE_RATES):
            obs_type = (
                "tool" if metric_type == ERROR_RATES_FOR_FUNCTION_CALLING else "llm"
            )
            params["obs_type_ts"] = obs_type
            query = f"""
                SELECT
                    {span_bucket_expr} AS timestamp,
                    CASE WHEN count() = 0 THEN 0
                         ELSE countIf(status = 'ERROR') / count()
                    END AS value
                FROM {SPANS_TABLE}
                {base_where}
                  {time_filter}
                  AND observation_type = %(obs_type_ts)s
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == ERROR_FREE_SESSION_RATES:
            query = f"""
                SELECT
                    timestamp,
                    CASE WHEN uniqExact(session_id) = 0 THEN 0
                         ELSE uniqExactIf(session_id, error_count = 0)
                              / uniqExact(session_id)
                    END AS value
                FROM (
                    SELECT
                        {span_bucket_expr} AS timestamp,
                        session_id,
                        countIf(status = 'ERROR') AS error_count
                    FROM {SPANS_TABLE}
                    {base_where}
                      {time_filter}
                      AND session_id != ''
                      AND session_id IS NOT NULL
                    GROUP BY timestamp, session_id
                )
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == SERVICE_PROVIDER_ERROR_RATES:
            query = f"""
                SELECT
                    timestamp,
                    CASE WHEN uniqExact(provider) = 0 THEN 0
                         ELSE uniqExactIf(provider, error_count = 0)
                              / uniqExact(provider)
                    END AS value
                FROM (
                    SELECT
                        {span_bucket_expr} AS timestamp,
                        provider,
                        countIf(status = 'ERROR') AS error_count
                    FROM {SPANS_TABLE}
                    {base_where}
                      {time_filter}
                      AND provider != ''
                      AND provider IS NOT NULL
                    GROUP BY timestamp, provider
                )
                GROUP BY timestamp
                ORDER BY timestamp
            """

        elif metric_type == EVALUATION_METRICS:
            eval_bucket_expr = (
                "toDateTime(intDiv(toUInt32(created_at), %(freq_seconds)s) "
                "* %(freq_seconds)s)"
            )
            query, params = self._build_eval_time_series_query(params, eval_bucket_expr)

        else:
            query = "SELECT NULL AS timestamp, NULL AS value WHERE 1 = 0"

        return query, params

    def _build_eval_time_series_query(
        self,
        params: dict[str, Any],
        bucket_expr: str,
    ) -> tuple[str, dict[str, Any]]:
        """Build eval metric time-series query."""
        if not self.eval_config_id:
            return "SELECT NULL AS timestamp, NULL AS value WHERE 1 = 0", params

        params["eval_config_id"] = self.eval_config_id
        eval_rows = self._eval_rows_source()

        if self.eval_output_type == "SCORE":
            agg = "avg(output_float)"
        elif self.eval_output_type == "PASS_FAIL":
            output_bool_val = 1 if self.threshold_metric_value == "Passed" else 0
            params["output_bool_val"] = output_bool_val
            agg = (
                "avg(CASE WHEN output_bool = %(output_bool_val)s THEN 1.0 ELSE 0.0 END)"
            )
        elif self.eval_output_type == "CHOICES":
            if not self.threshold_metric_value:
                return "SELECT NULL AS timestamp, NULL AS value WHERE 1 = 0", params
            params["choice_val"] = self.threshold_metric_value
            choice_match = self._eval_choice_match_expr()
            agg = f"avg(CASE WHEN {choice_match} THEN 1.0 ELSE 0.0 END)"
        else:
            return "SELECT NULL AS timestamp, NULL AS value WHERE 1 = 0", params

        query = f"""
            SELECT
                {bucket_expr} AS timestamp,
                ifNotFinite({agg}, NULL) AS value
            FROM {eval_rows}
            GROUP BY timestamp
            ORDER BY timestamp
        """

        return query, params

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _spans_base_where(self) -> str:
        """Return the base WHERE clause for spans table queries."""
        clause = self.project_where()
        if self._filter_clause:
            clause += f" AND {self._filter_clause}"
        return clause

    def _eval_rows_source(self) -> str:
        """Return finite latest-live eval rows for this monitor window.

        Eval storage is an independent rollout choice from the CH25 spans
        schema.  Production currently keeps fresh rows in the legacy-named
        PeerDB-shaped table, while prepared stacks may select the v2 table.
        Collapse physical versions only after config/time/project candidate
        pruning, then apply the selected table's live-row predicate outside
        the collapse so a tombstone cannot resurrect an older eval.
        """
        filter_extra = ""
        if self._filter_clause:
            filter_extra = f" AND {self._filter_clause}"
        eval_table, _ = eval_logger_source()
        _, live_predicate = eval_logger_source(
            "latest_eval", include_cdc_tombstone_guard=True
        )
        version_column = eval_logger_version_column(eval_table)
        return f"""
            (
                SELECT *
                FROM (
                    SELECT eval_scan.*
                    FROM {eval_table} AS eval_scan
                    PREWHERE eval_scan.custom_eval_config_id =
                        toUUID(%(eval_config_id)s)
                      AND eval_scan.created_at BETWEEN
                        %(start_time)s AND %(end_time)s
                    WHERE eval_scan.observation_span_id IN (
                        SELECT id
                        FROM {SPANS_TABLE}
                        WHERE project_id = %(project_id)s
                          AND is_deleted = 0
                          {filter_extra}
                    )
                    ORDER BY eval_scan.{version_column} DESC
                    LIMIT 1 BY eval_scan.id
                ) AS latest_eval
                WHERE {live_predicate}
            ) AS monitor_eval_rows
        """
