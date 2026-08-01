from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from django.test import override_settings

from tracer.services.clickhouse.query_builders.monitor_metrics import (
    COUNT_OF_ERRORS,
    DAILY_TOKENS_SPENT,
    ERROR_FREE_SESSION_RATES,
    ERROR_RATES_FOR_FUNCTION_CALLING,
    EVALUATION_METRICS,
    LLM_API_FAILURE_RATES,
    LLM_RESPONSE_TIME,
    MONTHLY_TOKENS_SPENT,
    SERVICE_PROVIDER_ERROR_RATES,
    SPAN_RESPONSE_TIME,
    TOKEN_USAGE,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"
START_TIME = datetime(2026, 7, 1)
END_TIME = datetime(2026, 7, 2)

SPAN_VALUE_METRICS = (
    COUNT_OF_ERRORS,
    ERROR_RATES_FOR_FUNCTION_CALLING,
    ERROR_FREE_SESSION_RATES,
    SERVICE_PROVIDER_ERROR_RATES,
    LLM_API_FAILURE_RATES,
    SPAN_RESPONSE_TIME,
    LLM_RESPONSE_TIME,
    TOKEN_USAGE,
    DAILY_TOKENS_SPENT,
    MONTHLY_TOKENS_SPENT,
)

SPAN_STATS_METRICS = (
    ERROR_RATES_FOR_FUNCTION_CALLING,
    ERROR_FREE_SESSION_RATES,
    SERVICE_PROVIDER_ERROR_RATES,
    LLM_API_FAILURE_RATES,
    SPAN_RESPONSE_TIME,
    LLM_RESPONSE_TIME,
)

SPAN_TIME_SERIES_METRICS = (
    COUNT_OF_ERRORS,
    ERROR_RATES_FOR_FUNCTION_CALLING,
    ERROR_FREE_SESSION_RATES,
    SERVICE_PROVIDER_ERROR_RATES,
    LLM_API_FAILURE_RATES,
    SPAN_RESPONSE_TIME,
    LLM_RESPONSE_TIME,
    TOKEN_USAGE,
    DAILY_TOKENS_SPENT,
    MONTHLY_TOKENS_SPENT,
)


def _builder(**kwargs) -> MonitorMetricsQueryBuilderV2:
    return MonitorMetricsQueryBuilderV2(project_id=PROJECT_ID, **kwargs)


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def test_monitor_clickhouse_call_sites_select_v2_builder_without_rollout_flag() -> None:
    from tracer.utils.monitor import _build_monitor_ch_builder
    from tracer.utils.monitor_graphs import _build_monitor_graph_ch_builder

    monitor = SimpleNamespace(
        metric_type=COUNT_OF_ERRORS,
        metric=None,
        project_id=PROJECT_ID,
        filters={},
        threshold_metric_value=None,
    )

    assert isinstance(_build_monitor_ch_builder(monitor), MonitorMetricsQueryBuilderV2)
    assert isinstance(
        _build_monitor_graph_ch_builder(monitor), MonitorMetricsQueryBuilderV2
    )


def test_monitor_clickhouse_call_sites_reject_missing_project_scope() -> None:
    from tracer.utils.monitor import _build_monitor_ch_builder
    from tracer.utils.monitor_graphs import _build_monitor_graph_ch_builder

    monitor = SimpleNamespace(
        metric_type=COUNT_OF_ERRORS,
        metric=None,
        project_id=None,
        filters={},
        threshold_metric_value=None,
    )

    with pytest.raises(ValueError, match="no project scope"):
        _build_monitor_ch_builder(monitor)
    with pytest.raises(ValueError, match="no project scope"):
        _build_monitor_graph_ch_builder(monitor)


@pytest.mark.parametrize("metric_type", SPAN_VALUE_METRICS)
def test_v2_span_metric_value_queries_use_start_time(metric_type: str) -> None:
    sql, _ = _builder().build_metric_value_query(metric_type, START_TIME, END_TIME)

    assert "FROM spans" in sql
    assert "start_time" in sql
    assert "created_at" not in sql


@pytest.mark.parametrize("metric_type", SPAN_STATS_METRICS)
def test_v2_span_stats_queries_use_start_time(metric_type: str) -> None:
    sql, _ = _builder().build_historical_stats_query(metric_type, START_TIME, END_TIME)

    assert "FROM spans" in sql
    assert "start_time BETWEEN %(start_time)s AND %(end_time)s" in sql
    assert "created_at" not in sql


@pytest.mark.parametrize("metric_type", SPAN_TIME_SERIES_METRICS)
def test_v2_span_time_series_use_start_time_for_bucket_and_bounds(
    metric_type: str,
) -> None:
    sql, _ = _builder().build_time_series_query(metric_type, START_TIME, END_TIME, 3600)

    assert "FROM spans" in sql
    assert "toUInt32(start_time)" in sql
    assert "start_time BETWEEN %(start_time)s AND %(end_time)s" in sql
    assert "created_at" not in sql


def test_v2_monitor_filters_use_v2_compiler_and_direct_session_predicate() -> None:
    builder = _builder(
        filters={
            "span_attributes_filters": [
                {
                    "column_id": "customer.tier",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "enterprise",
                    },
                }
            ],
            "session_id": [SESSION_ID],
            "date_range": [
                "2026-07-01T04:00:00Z",
                "2026-07-01T12:00:00Z",
            ],
            "created_at": "2026-07-01T06:00:00Z",
        }
    )

    sql, params = builder.build_metric_value_query(
        COUNT_OF_ERRORS, START_TIME, END_TIME
    )
    compact = _compact(sql)

    assert builder._FILTER_BUILDER_CLS is ClickHouseFilterBuilderV2
    assert "attrs_string['customer.tier']" in sql
    assert "span_attr_str" not in sql
    assert "trace_session_id IN %(mf_session_ids)s" in compact
    assert "IN (SELECT" not in compact
    assert "project_id = %(project_id)s" in compact
    assert "start_time BETWEEN %(mf_dr_start)s AND %(mf_dr_end)s" in compact
    assert "start_time >= %(mf_created_at)s" in compact
    assert "AND created_at" not in sql
    assert params["mf_session_ids"] == (SESSION_ID,)


def test_v2_error_free_session_queries_use_physical_session_column() -> None:
    builder = _builder()
    queries = (
        builder.build_metric_value_query(
            ERROR_FREE_SESSION_RATES, START_TIME, END_TIME
        )[0],
        builder.build_historical_stats_query(
            ERROR_FREE_SESSION_RATES, START_TIME, END_TIME
        )[0],
        builder.build_time_series_query(
            ERROR_FREE_SESSION_RATES, START_TIME, END_TIME, 3600
        )[0],
    )

    for sql in queries:
        assert "trace_session_id" in sql
        assert "session_id" not in sql.replace("trace_session_id", "")


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
def test_v2_eval_queries_keep_eval_time_on_created_at() -> None:
    builder = _builder(
        filters={
            "date_range": [
                "2026-07-01T04:00:00Z",
                "2026-07-01T12:00:00Z",
            ]
        },
        eval_config_id="33333333-3333-3333-3333-333333333333",
        eval_output_type="SCORE",
    )

    value_sql, _ = builder.build_metric_value_query(
        EVALUATION_METRICS, START_TIME, END_TIME
    )
    stats_sql, _ = builder.build_historical_stats_query(
        EVALUATION_METRICS, START_TIME, END_TIME
    )
    series_sql, _ = builder.build_time_series_query(
        EVALUATION_METRICS, START_TIME, END_TIME, 3600
    )

    for sql in (value_sql, stats_sql, series_sql):
        assert "FROM tracer_eval_logger FINAL" in sql
        assert "(deleted = 0 OR deleted IS NULL)" in sql
        assert "created_at BETWEEN %(start_time)s AND %(end_time)s" in sql
        assert "start_time >= %(start_time)s - INTERVAL 1 DAY" in sql
        assert "start_time < %(end_time)s + INTERVAL 1 DAY" in sql
        assert "start_time BETWEEN %(mf_dr_start)s AND %(mf_dr_end)s" in sql

    assert "toUInt32(created_at)" in series_sql
    assert "toUInt32(start_time)" not in series_sql


@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
def test_v2_eval_queries_route_to_v2_logger_and_delete_marker() -> None:
    builder = _builder(
        eval_config_id="33333333-3333-3333-3333-333333333333",
        eval_output_type="SCORE",
    )

    sql, _ = builder.build_metric_value_query(EVALUATION_METRICS, START_TIME, END_TIME)

    assert "FROM tracer_eval_logger_v2 FINAL" in sql
    assert "AND is_deleted = 0" in sql
    assert "deleted IS NULL" not in sql
