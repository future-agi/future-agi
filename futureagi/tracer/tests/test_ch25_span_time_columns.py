"""CH25 span reads must use the indexed ``start_time`` column."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
ANNOTATOR_ID = "22222222-2222-4222-8222-222222222222"
EVAL_CONFIG_ID = "33333333-3333-4333-8333-333333333333"
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 8, 1, tzinfo=UTC)


def _annotator_filter() -> list[dict]:
    return [
        {
            "column_id": "annotator",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": ANNOTATOR_ID,
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]


def test_ch25_score_span_resolution_uses_start_time_but_score_keeps_created_at():
    builder = ClickHouseFilterBuilderV2(project_id=PROJECT_ID)

    where, _ = builder.translate(_annotator_filter())

    assert "AND start_time >= %(start_date)s - INTERVAL 1 DAY" in where
    assert "AND start_time < %(end_date)s + INTERVAL 1 DAY" in where
    assert "AND created_at >= %(start_date)s - INTERVAL 1 DAY" not in where
    assert "s.created_at >= %(start_date)s - INTERVAL 1 DAY" in where


def test_legacy_score_span_resolution_preserves_created_at_partition_bound():
    builder = ClickHouseFilterBuilder(project_id=PROJECT_ID)

    where, _ = builder.translate(_annotator_filter())

    assert "AND created_at >= %(start_date)s - INTERVAL 1 DAY" in where
    assert "AND start_time >= %(start_date)s - INTERVAL 1 DAY" not in where


def test_legacy_span_list_preserves_created_at_partition_bound():
    builder = SpanListQueryBuilder(project_id=PROJECT_ID)

    query, _ = builder.build()

    assert "created_at >= %(start_date)s - INTERVAL 1 DAY" in query
    assert "start_time >= %(start_date)s" in query


def _monitor_filters() -> dict:
    return {
        "date_range": [START.isoformat(), END.isoformat()],
        "created_at": START.isoformat(),
    }


@pytest.mark.parametrize(
    "build_query",
    (
        lambda builder: builder.build_metric_value_query(
            MonitorMetricTypeChoices.SPAN_RESPONSE_TIME, START, END
        ),
        lambda builder: builder.build_historical_stats_query(
            MonitorMetricTypeChoices.SPAN_RESPONSE_TIME, START, END
        ),
        lambda builder: builder.build_time_series_query(
            MonitorMetricTypeChoices.SPAN_RESPONSE_TIME, START, END, 3600
        ),
    ),
    ids=("value", "historical", "time-series"),
)
def test_ch25_monitor_span_queries_use_event_time_with_partition_prune(build_query):
    builder = MonitorMetricsQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=_monitor_filters(),
    )

    query, _ = build_query(builder)
    normalized = " ".join(query.split())

    assert "FROM spans" in normalized
    assert "start_time >= %(start_time)s" in normalized
    assert "start_time < %(end_time)s" in normalized
    assert "created_at >= %(start_time)s - INTERVAL 1 DAY" in normalized
    assert "toUInt32(created_at)" not in normalized


def test_legacy_monitor_span_query_uses_event_time_with_partition_prune():
    builder = MonitorMetricsQueryBuilder(project_id=PROJECT_ID)

    query, _ = builder.build_time_series_query(
        MonitorMetricTypeChoices.SPAN_RESPONSE_TIME, START, END, 3600
    )
    normalized = " ".join(query.split())

    assert "toUInt32(start_time)" in normalized
    assert "start_time >= %(start_time)s" in normalized
    assert "start_time < %(end_time)s" in normalized
    assert "created_at >= %(start_time)s - INTERVAL 1 DAY" in normalized


def test_ch25_monitor_eval_query_keeps_eval_created_at_and_scopes_spans_by_start_time():
    builder = MonitorMetricsQueryBuilderV2(
        project_id=PROJECT_ID,
        filters={"date_range": [START.isoformat(), END.isoformat()]},
        eval_config_id=EVAL_CONFIG_ID,
        eval_output_type="SCORE",
    )

    query, _ = builder.build_time_series_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, START, END, 3600
    )
    normalized = " ".join(query.split())

    assert "toUInt32(span_start_time)" in normalized
    assert "eval_scan.created_at >= %(start_time)s - INTERVAL 1 DAY" in normalized
    assert "start_time >= %(start_time)s" in normalized
    assert "start_time < %(end_time)s" in normalized
    assert "eval_scan.created_at < %(end_time)s" not in normalized
