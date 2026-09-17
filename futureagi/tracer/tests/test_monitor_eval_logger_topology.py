"""CH25 monitor eval reads must match the selected physical logger shape."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-4222-8222-222222222222"
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 8, 1, tzinfo=UTC)


def _builder() -> MonitorMetricsQueryBuilderV2:
    return MonitorMetricsQueryBuilderV2(
        project_id=PROJECT_ID,
        eval_config_id=EVAL_CONFIG_ID,
        eval_output_type="SCORE",
    )


@pytest.mark.parametrize(
    "build_query",
    (
        lambda builder: builder.build_metric_value_query(
            MonitorMetricTypeChoices.EVALUATION_METRICS, START, END
        ),
        lambda builder: builder.build_historical_stats_query(
            MonitorMetricTypeChoices.EVALUATION_METRICS, START, END
        ),
        lambda builder: builder.build_time_series_query(
            MonitorMetricTypeChoices.EVALUATION_METRICS, START, END, 3600
        ),
    ),
    ids=("value", "historical", "time-series"),
)
@pytest.mark.parametrize(
    ("table", "version", "live_predicates", "foreign_columns"),
    (
        (
            "tracer_eval_logger",
            "_peerdb_version",
            (
                "latest_eval._peerdb_is_deleted = 0",
                "latest_eval.deleted = 0 OR latest_eval.deleted IS NULL",
            ),
            ("latest_eval.is_deleted = 0", "eval_scan._version"),
        ),
        (
            "tracer_eval_logger_v2",
            "_version",
            ("latest_eval.is_deleted = 0",),
            (
                "_peerdb_version",
                "_peerdb_is_deleted",
                "latest_eval.deleted",
            ),
        ),
    ),
    ids=("legacy-authoritative", "v2-prepared"),
)
def test_monitor_eval_queries_follow_authoritative_logger_shape(
    settings,
    build_query,
    table,
    version,
    live_predicates,
    foreign_columns,
):
    settings.CH25_EVAL_LOGGER_TABLE = table

    query, params = build_query(_builder())
    normalized = " ".join(query.split())

    assert f"FROM {table} AS eval_scan" in normalized
    assert f"ORDER BY eval_scan.{version} DESC" in normalized
    assert "LIMIT 1 BY eval_scan.id" in normalized
    assert "FINAL" not in normalized
    for predicate in live_predicates:
        assert predicate in normalized
    for column in foreign_columns:
        assert column not in normalized
    assert normalized.index("LIMIT 1 BY eval_scan.id") < normalized.index(
        "WHERE latest_eval."
    )
    assert params["project_id"] == PROJECT_ID
    assert params["eval_config_id"] == EVAL_CONFIG_ID
    assert params["start_time"] == START.replace(tzinfo=None)
    assert params["end_time"] == END.replace(tzinfo=None)
