"""Monitor builder parity: calendar-bucketed historical stats for count/token
metrics, stddevPop vs stddevSamp split, NULL-on-empty token windows, provider
guard, and eval CHOICES containment. Pure SQL-string assertions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-2222-2222-222222222222"
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 8, tzinfo=UTC)

TIME_AGGREGATED = [
    MonitorMetricTypeChoices.COUNT_OF_ERRORS,
    MonitorMetricTypeChoices.TOKEN_USAGE,
    MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
    MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
]
PER_ROW_HISTORICAL = [
    MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING,
    MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES,
    MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
    MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
    MonitorMetricTypeChoices.SPAN_RESPONSE_TIME,
    MonitorMetricTypeChoices.LLM_RESPONSE_TIME,
]


def _builder(eval_output_type: str | None = None) -> MonitorMetricsQueryBuilder:
    return MonitorMetricsQueryBuilder(
        project_id=PROJECT_ID,
        eval_config_id=EVAL_CONFIG_ID if eval_output_type else None,
        eval_output_type=eval_output_type,
        threshold_metric_value="Good" if eval_output_type == "CHOICES" else None,
    )


# --- Calendar-bucketed historical stats for count/token metrics ---------------


@pytest.mark.parametrize(
    "interval_kind,bucket_fn",
    [
        ("minute", "toStartOfMinute"),
        ("hour", "toStartOfHour"),
        ("day", "toStartOfDay"),
        ("month", "toStartOfMonth"),
    ],
)
@pytest.mark.parametrize("metric_type", TIME_AGGREGATED)
def test_time_aggregated_historical_buckets_calendar(
    metric_type: str, interval_kind: str, bucket_fn: str
) -> None:
    sql, _ = _builder().build_historical_stats_query(
        metric_type, START, END, interval_kind=interval_kind
    )
    # Event time: buckets share the window's axis, so no out-of-window bucket.
    assert f"{bucket_fn}(start_time) AS bucket_ts" in sql
    assert "GROUP BY bucket_ts" in sql
    # Sample stddev here (old path used statistics.stdev), collapsed to 0.
    assert "stddevSamp(bucket_value)" in sql
    assert "coalesce(ifNotFinite(avg(bucket_value), 0), 0)" in sql


def test_time_aggregated_historical_agg_per_metric() -> None:
    sql_err, _ = _builder().build_historical_stats_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END, interval_kind="hour"
    )
    assert "countIf(status = 'ERROR') AS bucket_value" in sql_err
    sql_tok, _ = _builder().build_historical_stats_query(
        MonitorMetricTypeChoices.TOKEN_USAGE, START, END, interval_kind="hour"
    )
    # No-token buckets excluded (v2 total_tokens is non-Nullable, PG NULL -> 0).
    assert "nullIf(sum(total_tokens), 0) AS bucket_value" in sql_tok


def test_time_aggregated_historical_defaults_to_hour() -> None:
    sql, _ = _builder().build_historical_stats_query(MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END)
    assert "toStartOfHour(start_time) AS bucket_ts" in sql


# --- stddevPop for per-row + eval stats (PG StdDev is population) -------------


@pytest.mark.parametrize("metric_type", PER_ROW_HISTORICAL)
def test_per_row_historical_uses_population_stddev(metric_type: str) -> None:
    sql, _ = _builder().build_historical_stats_query(metric_type, START, END)
    assert "stddevPop(" in sql
    assert "stddevSamp" not in sql


def test_eval_stats_use_population_stddev() -> None:
    sql, _ = _builder(eval_output_type="SCORE").build_historical_stats_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, START, END
    )
    assert "stddevPop(" in sql
    assert "stddevSamp" not in sql


# --- No-token window yields NULL, not 0 ---------------------------------------


@pytest.mark.parametrize(
    "metric_type",
    [MonitorMetricTypeChoices.TOKEN_USAGE, MonitorMetricTypeChoices.DAILY_TOKENS_SPENT, MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT],
)
def test_token_value_null_on_empty_window(metric_type: str) -> None:
    sql, _ = _builder().build_metric_value_query(metric_type, START, END)
    assert "CASE WHEN countIf(total_tokens != 0) = 0 THEN NULL" in sql
    assert "sum(total_tokens)" in sql


# --- Provider guard (v2 provider is non-Nullable; '' means no provider) -------


def test_provider_guard_excludes_empty_string_only() -> None:
    for sql, _ in (
        _builder().build_metric_value_query(
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES, START, END
        ),
        _builder().build_historical_stats_query(
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES, START, END
        ),
        _builder().build_time_series_query(
            MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES, START, END, 3600
        ),
    ):
        assert "provider != ''" in sql
        assert "provider IS NOT NULL" not in sql


# --- Eval CHOICES list containment only (PG parity) ---------------------------


def test_eval_choices_no_output_str_fallback() -> None:
    sql, _ = _builder(eval_output_type="CHOICES").build_metric_value_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, START, END
    )
    assert "has(JSONExtract(output_str_list, 'Array(String)'), %(choice_val)s)" in sql
    assert "OR output_str" not in sql


# --- Evaluator routing: CH serves these metrics, PG is never touched ----------


class TestHistoricalStatsRouting:
    """Pin the _get_historical_stats routing hunk: the four time-aggregated
    metrics are served by the CH builder with the monitor's interval_kind,
    and ObservationSpan (the dropped span table) is never queried."""

    @pytest.mark.parametrize(
        ("metric_type", "frequency", "bucket_fn"),
        [
            (MonitorMetricTypeChoices.COUNT_OF_ERRORS, 60, "toStartOfHour"),
            (MonitorMetricTypeChoices.TOKEN_USAGE, 5, "toStartOfMinute"),
            (MonitorMetricTypeChoices.DAILY_TOKENS_SPENT, 60, "toStartOfDay"),
            (MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT, 60, "toStartOfMonth"),
        ],
    )
    def test_ch_route_passes_interval_kind_and_skips_pg(
        self, metric_type: str, frequency: int, bucket_fn: str
    ) -> None:
        from types import SimpleNamespace
        from unittest import mock

        from tracer.models.monitor import UserAlertMonitor
        from tracer.utils import monitor as monitor_utils

        monitor = UserAlertMonitor(
            project_id=PROJECT_ID,
            metric_type=metric_type,
            alert_frequency=frequency,
            filters={},
        )
        captured: dict[str, str] = {}

        class _Svc:
            def execute_ch_query(self, query, params, **kwargs):
                captured["query"] = query
                return SimpleNamespace(data=[{"mean": 4.2, "stddev": 1.1}])

        with mock.patch.object(monitor_utils, "AnalyticsQueryService", _Svc):
            mean, stddev = monitor_utils._get_historical_stats(monitor, START, END)

        assert (mean, stddev) == (4.2, 1.1)
        # interval_kind derived from the monitor reaches the bucket function.
        assert f"{bucket_fn}(start_time)" in captured["query"]
        # PG path is gone structurally: the module no longer imports the
        # dropped span table at all.
        assert not hasattr(monitor_utils, "ObservationSpan")
