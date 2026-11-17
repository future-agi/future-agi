"""Monitor builder parity: per-period previous-period baselines for count/token
metrics, population stddev for per-row stats, NULL-on-empty token windows,
provider guard, and eval CHOICES containment. Pure SQL-string assertions."""

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


# --- Per-period baseline for count/token metrics ------------------------------


@pytest.mark.parametrize("metric_type", TIME_AGGREGATED)
def test_time_aggregated_baseline_is_total_per_period_not_calendar_buckets(
    metric_type: str,
) -> None:
    sql, params = _builder().build_historical_stats_query(
        metric_type, START, END, periods=168.0
    )
    # One total over the exact window, divided by the periods in it: a
    # rolling window is never split into partial clock-hour buckets, and
    # periods with no spans count as 0 instead of being skipped.
    assert "/ %(baseline_periods)s" in sql
    assert params["baseline_periods"] == 168.0
    assert "GROUP BY" not in sql
    assert "toStartOf" not in sql
    assert "bucket_value" not in sql
    assert "stddevSamp" not in sql


def test_time_aggregated_baseline_per_metric_and_no_data_rule() -> None:
    sql_err, _ = _builder().build_historical_stats_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END
    )
    # No spans is no history (NULL); spans without errors are a real zero.
    assert (
        "if(count() > 0, countIf(status = 'ERROR') / %(baseline_periods)s, NULL)"
        " AS mean" in sql_err
    )
    sql_tok, _ = _builder().build_historical_stats_query(
        MonitorMetricTypeChoices.TOKEN_USAGE, START, END
    )
    # No token-bearing spans is no history, as in the value query (v2
    # total_tokens is non-Nullable, PG NULL -> 0).
    assert (
        "if(countIf(total_tokens != 0) > 0, sum(total_tokens) / "
        "%(baseline_periods)s, NULL) AS mean" in sql_tok
    )


def test_time_aggregated_baseline_defaults_to_one_period() -> None:
    _, params = _builder().build_historical_stats_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, START, END
    )
    assert params["baseline_periods"] == 1.0


# --- The earlier rule's band for count/token metrics --------------------------
#
# Monitors saved before the threshold update keep production's band: the mean
# and sample stddev of calendar buckets, (0, 0) for an empty window.


@pytest.mark.parametrize(
    ("metric_type", "bucket_value"),
    [
        (MonitorMetricTypeChoices.COUNT_OF_ERRORS, "countIf(status = 'ERROR')"),
        (MonitorMetricTypeChoices.TOKEN_USAGE, "nullIf(sum(total_tokens), 0)"),
        (MonitorMetricTypeChoices.DAILY_TOKENS_SPENT, "nullIf(sum(total_tokens), 0)"),
        (MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT, "nullIf(sum(total_tokens), 0)"),
    ],
)
@pytest.mark.parametrize(
    ("interval_kind", "bucket"),
    [
        ("minute", "toStartOfMinute"),
        ("hour", "toStartOfHour"),
        ("day", "toStartOfDay"),
        ("month", "toStartOfMonth"),
    ],
)
def test_earlier_band_is_calendar_bucket_stats(
    metric_type: str, bucket_value: str, interval_kind: str, bucket: str
) -> None:
    sql, params = _builder().build_earlier_band_query(
        metric_type, START, END, interval_kind=interval_kind
    )
    sql = " ".join(sql.split())

    assert (
        "SELECT coalesce(ifNotFinite(avg(bucket_value), 0), 0) AS mean, "
        "coalesce(ifNotFinite(stddevSamp(bucket_value), 0), 0) AS stddev"
    ) in sql
    assert (
        f"SELECT {bucket}(start_time) AS bucket_ts, {bucket_value} AS bucket_value "
        "FROM spans"
    ) in sql
    assert sql.endswith("GROUP BY bucket_ts )")
    assert "start_time >= %(start_time)s AND start_time < %(end_time)s" in sql
    assert params["start_time"] == START.replace(tzinfo=None)
    assert "baseline_periods" not in params


@pytest.mark.parametrize("metric_type", PER_ROW_HISTORICAL)
def test_earlier_band_for_per_row_metrics_is_their_historical_stats(
    metric_type: str,
) -> None:
    # Rate and latency metrics kept production's per-row mean and stddev.
    assert _builder().build_earlier_band_query(
        metric_type, START, END, interval_kind="hour"
    ) == _builder().build_historical_stats_query(metric_type, START, END)


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
    [
        MonitorMetricTypeChoices.TOKEN_USAGE,
        MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
        MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
    ],
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
    """Pin the get_previous_period_baseline routing: the four time-aggregated
    metrics are served by the CH builder with the periods in the monitor's
    history window, and ObservationSpan (the dropped span table) is never
    queried."""

    @pytest.mark.parametrize(
        ("metric_type", "frequency", "periods"),
        [
            # START..END is 7 days.
            (MonitorMetricTypeChoices.COUNT_OF_ERRORS, 60, 168.0),
            (MonitorMetricTypeChoices.TOKEN_USAGE, 5, 2016.0),
            (MonitorMetricTypeChoices.DAILY_TOKENS_SPENT, 60, 7.0),
            # Shorter than one 30-day period: 7 of its 30 days, so the
            # week's total is scaled up to the period.
            pytest.param(
                MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
                60,
                7 / 30,
                id="monthly-tokens-week-of-a-period",
            ),
        ],
    )
    def test_ch_route_passes_periods_and_skips_pg(
        self, metric_type: str, frequency: int, periods: float
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
        captured: dict = {}

        class _Svc:
            def execute_ch_query(self, query, params, **kwargs):
                captured["query"], captured["params"] = query, params
                return SimpleNamespace(data=[{"mean": 4.2, "stddev": None}])

        with mock.patch.object(monitor_utils, "AnalyticsQueryService", _Svc):
            baseline = monitor_utils.get_previous_period_baseline(monitor, START, END)

        assert baseline == 4.2
        # The periods derived from the monitor reach the per-period division.
        assert "/ %(baseline_periods)s" in captured["query"]
        assert captured["params"]["baseline_periods"] == periods
        # PG path is gone structurally: the module no longer imports the
        # dropped span table at all.
        assert not hasattr(monitor_utils, "ObservationSpan")
