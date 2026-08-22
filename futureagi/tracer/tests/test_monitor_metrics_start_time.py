"""Monitor builder time windows — event-time semantics.

Everything (alerts, graphs, baselines) measures when the activity happened in
the user's system: exact half-open ``start_time`` window (also the partition
key, so it prunes directly) + a padded ``created_at`` lower guard against
clock-skewed producers. Buckets are on ``start_time``; eval queries window and
bucket via their joined span (the eval table has no span-time column).
Pure SQL-string assertions, no ClickHouse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-2222-2222-222222222222"
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 8, tzinfo=UTC)

# One unified half-open event-time window for every spans query.
EXACT_HALF_OPEN = "start_time >= %(start_time)s AND start_time < %(end_time)s"
SKEW_GUARD = "created_at >= %(start_time)s - INTERVAL 1 DAY"

SPANS_METRICS = [
    MonitorMetricTypeChoices.COUNT_OF_ERRORS,
    MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING,
    MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES,
    MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
    MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
    MonitorMetricTypeChoices.SPAN_RESPONSE_TIME,
    MonitorMetricTypeChoices.LLM_RESPONSE_TIME,
    MonitorMetricTypeChoices.TOKEN_USAGE,
    MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
    MonitorMetricTypeChoices.MONTHLY_TOKENS_SPENT,
]
HISTORICAL_SPANS = [
    MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING,
    MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES,
    MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
    MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
    MonitorMetricTypeChoices.SPAN_RESPONSE_TIME,
    MonitorMetricTypeChoices.LLM_RESPONSE_TIME,
]


def _builder(
    filters: dict[str, Any] | None = None,
    eval_output_type: str | None = None,
) -> MonitorMetricsQueryBuilder:
    return MonitorMetricsQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        eval_config_id=EVAL_CONFIG_ID if eval_output_type else None,
        eval_output_type=eval_output_type,
        threshold_metric_value="Passed" if eval_output_type == "PASS_FAIL" else None,
    )


def _assert_event_time_window(sql: str) -> None:
    assert EXACT_HALF_OPEN in sql, "missing exact start_time window"
    assert SKEW_GUARD in sql, "missing created_at skew guard"
    assert "created_at BETWEEN" not in sql
    assert "created_at >= %(start_time)s AND created_at < %(end_time)s" not in sql


@pytest.mark.parametrize("metric_type", SPANS_METRICS)
def test_metric_value_event_time_window(metric_type: str) -> None:
    sql, _ = _builder().build_metric_value_query(metric_type, START, END)
    _assert_event_time_window(sql)


@pytest.mark.parametrize("metric_type", HISTORICAL_SPANS)
def test_historical_stats_event_time_window(metric_type: str) -> None:
    sql, _ = _builder().build_historical_stats_query(metric_type, START, END)
    _assert_event_time_window(sql)


@pytest.mark.parametrize("metric_type", SPANS_METRICS)
def test_time_series_buckets_start_time(metric_type: str) -> None:
    sql, _ = _builder().build_time_series_query(metric_type, START, END, 3600)
    assert "toUInt32(start_time)" in sql, "series must bucket on event time"
    assert "toUInt32(created_at)" not in sql
    _assert_event_time_window(sql)


# --- Eval queries window/bucket via the joined span --------------------------


def test_eval_value_query_windows_span_time() -> None:
    # The metric window lives on the SPAN membership join (evals run async
    # after their spans); the eval table keeps only a loose created_at lower
    # bound (its sole partition prune).
    sql, _ = _builder(eval_output_type="SCORE").build_metric_value_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, START, END
    )
    subq = sql.split("INNER JOIN (", 1)[1]
    assert EXACT_HALF_OPEN in subq
    guards = sql.split("ON observation_span_id = sp.id", 1)[1]
    assert SKEW_GUARD in guards
    # No bucket expression in a scalar query.
    assert "toUInt32(" not in sql


def test_eval_time_series_buckets_span_start_time() -> None:
    # Eval graphs chart the user's application timeline: buckets come from
    # the joined span's start_time, never the eval row's created_at.
    sql, _ = _builder(eval_output_type="SCORE").build_time_series_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, START, END, 3600
    )
    assert "toUInt32(sp.start_time)" in sql
    assert "toUInt32(created_at)" not in sql
    assert "INNER JOIN" in sql and "ON observation_span_id = sp.id" in sql
