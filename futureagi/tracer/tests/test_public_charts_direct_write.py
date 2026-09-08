"""Direct-write routing contracts for the public Observe chart helpers."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import ObservationSpan
from tracer.services.clickhouse import graph_dispatch
from tracer.utils.graphs_optimized import (
    EvalGraphReadError,
    SystemMetricGraphReadError,
    get_all_system_metrics,
    get_eval_graph_data,
    get_system_metric_data,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-4222-8222-222222222222"
WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "filter_value": [
            "2026-07-24T02:43:12.000Z",
            "2026-07-31T06:59:59.000Z",
        ],
    },
}
FINAL_STATUS_FILTER = {
    "column_id": "final_status",
    "display_name": "final_status",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "in",
        "filter_value": ["Rechazado"],
        "col_type": "SPAN_ATTRIBUTE",
    },
}


@pytest.mark.unit
def test_public_chart_routes_have_no_full_window_latest_spans_collapse():
    source = inspect.getsource(graph_dispatch.fetch_all_system_metrics_ch)
    assert "WITH latest_spans AS" not in source
    assert "ORDER BY _version DESC" not in source
    assert "read_exact_all_system_metrics" in source
    assert "_DeadlineBoundGraphAnalytics" in source
    assert "_read_or_refresh_exact_graph" not in source
    assert "read_graph_candidates" not in source


@pytest.mark.unit
@mock.patch("tracer.services.clickhouse.graph_dispatch.fetch_all_system_metrics_ch")
@mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
def test_all_system_metrics_uses_direct_filtered_ch25_query(
    analytics_cls,
    bounded_metrics,
    settings,
):
    settings.CH25_QUERY_TYPES_V2_PRIMARY = ""
    settings.CH25_QUERY_TYPES_V2_ONLY = ""
    bounded_metrics.return_value = {
        "latency": [],
        "tokens": [],
        "cost": [],
        "traffic": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    with mock.patch.object(ObservationSpan.objects, "filter") as pg_filter:
        result = get_all_system_metrics(
            interval="day",
            filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
            property="average",
            system_metric_filters={"project_id": PROJECT_ID},
        )

    pg_filter.assert_not_called()
    bounded_metrics.assert_called_once_with(
        analytics=analytics_cls.return_value,
        project_id=PROJECT_ID,
        filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
        interval="day",
        refresh=False,
    )
    assert result["query_status"] == "complete"
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@mock.patch("tracer.services.clickhouse.graph_dispatch.fetch_all_system_metrics_ch")
@mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
def test_single_system_metric_does_not_fall_back_or_expose_ch_error(
    analytics_cls,
    exact_metrics,
):
    exact_metrics.side_effect = ServerException(
        "secret ClickHouse host and stack", code=159
    )

    with mock.patch.object(ObservationSpan.objects, "filter") as pg_filter:
        with pytest.raises(SystemMetricGraphReadError) as exc_info:
            get_system_metric_data(
                interval="day",
                filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
                property="average",
                req_data_config={"id": "latency", "type": "SYSTEM_METRIC"},
                system_metric_filters={"project_id": PROJECT_ID},
                observe_type="charts",
            )

    pg_filter.assert_not_called()
    assert str(exc_info.value) == "System metric graph data is temporarily unavailable"
    assert "secret ClickHouse host" not in str(exc_info.value)


@pytest.mark.unit
@mock.patch("tracer.services.clickhouse.graph_dispatch.fetch_all_system_metrics_ch")
@mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
def test_single_system_metric_preserves_unexpected_programming_error(
    analytics_cls,
    exact_metrics,
):
    del analytics_cls
    exact_metrics.side_effect = RuntimeError("unexpected programming failure")

    with mock.patch.object(ObservationSpan.objects, "filter") as pg_filter:
        with pytest.raises(RuntimeError, match="unexpected programming failure"):
            get_system_metric_data(
                interval="day",
                filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
                property="average",
                req_data_config={"id": "latency", "type": "SYSTEM_METRIC"},
                system_metric_filters={"project_id": PROJECT_ID},
                observe_type="charts",
            )

    pg_filter.assert_not_called()


@pytest.mark.unit
@mock.patch("tracer.services.clickhouse.graph_dispatch.fetch_eval_chart_series_ch")
@mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
def test_eval_graph_uses_authoritative_table_and_applies_attribute_filter(
    analytics_cls,
    bounded_eval,
    settings,
):
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
    settings.CH25_QUERY_TYPES_V2_PRIMARY = ""
    settings.CH25_QUERY_TYPES_V2_ONLY = ""
    bounded_eval.return_value = [
        {
            "name": "Outcome",
            "id": EVAL_CONFIG_ID,
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }
    ]
    config = SimpleNamespace(
        id=EVAL_CONFIG_ID,
        name="Outcome",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )

    with mock.patch.object(
        CustomEvalConfig.objects, "select_related"
    ) as select_related:
        select_related.return_value.get.return_value = config
        result = get_eval_graph_data(
            interval="day",
            filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
            property="average",
            observe_type="charts",
            req_data_config={"id": EVAL_CONFIG_ID, "type": "EVAL"},
            eval_logger_filters={"project_id": PROJECT_ID},
        )

    bounded_eval.assert_called_once_with(
        analytics=analytics_cls.return_value,
        project_id=PROJECT_ID,
        filters=[WINDOW_FILTER, FINAL_STATUS_FILTER],
        interval="day",
        req_data_config={
            "id": EVAL_CONFIG_ID,
            "type": "EVAL",
            "eval_output_type": "SCORE",
            "choices": [],
        },
        eval_name="Outcome",
        refresh=False,
    )
    assert result[0]["query_status"] == "complete"
    assert result[0]["query_sampled"] is False


@pytest.mark.unit
@mock.patch("tracer.services.clickhouse.graph_dispatch.fetch_eval_chart_series_ch")
@mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService")
def test_eval_graph_sanitizes_direct_ch25_failure(analytics_cls, exact_eval):
    exact_eval.side_effect = RuntimeError("secret ClickHouse host and stack")
    config = SimpleNamespace(
        id=EVAL_CONFIG_ID,
        name="Outcome",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )

    with mock.patch.object(
        CustomEvalConfig.objects, "select_related"
    ) as select_related:
        select_related.return_value.get.return_value = config
        with pytest.raises(EvalGraphReadError) as exc_info:
            get_eval_graph_data(
                interval="day",
                filters=[WINDOW_FILTER],
                property="average",
                observe_type="charts",
                req_data_config={"id": EVAL_CONFIG_ID, "type": "EVAL"},
                eval_logger_filters={"project_id": PROJECT_ID},
            )

    assert str(exc_info.value) == "Evaluation graph data is temporarily unavailable"
    assert "secret ClickHouse host" not in str(exc_info.value)


@pytest.mark.django_db
@mock.patch("tracer.views.charts.get_all_system_metrics")
def test_public_system_chart_returns_sanitized_retryable_error(
    get_metrics,
    auth_client,
    observe_project,
):
    get_metrics.side_effect = SystemMetricGraphReadError(
        "secret ClickHouse host and stack"
    )
    query = urlencode(
        {
            "project_id": str(observe_project.id),
            "interval": "day",
            "property": "average",
            "req_data_config": '{"id":"all","type":"SYSTEM_METRICS"}',
        }
    )

    response = auth_client.get(f"/tracer/charts/fetch_graph/?{query}")

    assert response.status_code == 503
    payload = str(response.json())
    assert "temporarily unavailable" in payload
    assert "secret ClickHouse host" not in payload
