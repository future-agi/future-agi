from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

import pytest
from clickhouse_driver.errors import NetworkError

from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.bounded_graph_reads import GraphCandidateSample
from tracer.services.clickhouse.session_graph import fetch_session_graph_ch

PROJECT_ID = "22222222-2222-4222-8222-222222222222"


def _date_filter(start: str, end: str) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _attribute_filter() -> dict:
    return {
        "column_id": "model",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "gpt-4.1",
        },
    }


def _span_attribute_filter(
    key: str,
    *,
    filter_type: str,
    value: object,
) -> dict:
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": "equals",
            "filter_value": value,
        },
    }


WINDOWS = [
    ("2026-08-11T00:00:00Z", "2026-08-12T00:00:00Z", "hour"),
    ("2026-08-05T00:00:00Z", "2026-08-12T00:00:00Z", "day"),
    ("2026-07-13T00:00:00Z", "2026-08-12T00:00:00Z", "day"),
    ("2026-05-12T00:00:00Z", "2026-08-12T00:00:00Z", "week"),
    ("2026-02-12T00:00:00Z", "2026-08-12T00:00:00Z", "month"),
    ("2025-08-12T00:00:00Z", "2026-08-12T00:00:00Z", "month"),
]

FILTER_SHAPES = [
    {
        "column_id": "status",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "ERROR",
        },
    },
    _span_attribute_filter(
        "customer_external_id",
        filter_type="text",
        value="sparse-customer-42",
    ),
    _span_attribute_filter("tokens_bucket", filter_type="number", value=128),
    {
        "column_id": "quality-eval-id",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "number",
            "filter_op": "greater_than",
            "filter_value": 0.8,
        },
    },
    {
        "column_id": "review-label-id",
        "filter_config": {
            "col_type": "ANNOTATION",
            "filter_type": "categorical",
            "filter_op": "equals",
            "filter_value": "approved",
        },
    },
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filters", "interval"),
    [
        ([], "day"),
        *[([_date_filter(start, end)], interval) for start, end, interval in WINDOWS],
    ],
)
def test_trace_primary_date_only_uses_one_interactive_rollup_query(
    monkeypatch, filters, interval
):
    analytics = mock.Mock()
    analytics.execute_ch_query.return_value = mock.Mock(
        data=[
            {
                "time_bucket": datetime(2026, 8, 1),
                "avg_latency": 12,
                "total_tokens": 100,
                "avg_cost": 0.25,
                "traffic_count": 4,
                "prompt_tokens": 60,
                "completion_tokens": 40,
                "error_rate": 25,
            }
        ],
        columns=[
            "time_bucket",
            "avg_latency",
            "total_tokens",
            "avg_cost",
            "traffic_count",
            "prompt_tokens",
            "completion_tokens",
            "error_rate",
        ],
    )
    exact_read = mock.Mock()
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval=interval,
        metric_id="latency",
        observe_type="trace",
    )

    exact_read.assert_not_called()
    analytics.execute_ch_query.assert_called_once()
    call = analytics.execute_ch_query.call_args
    query = call.args[0]
    assert "FROM spans_hourly_rollup" in query
    assert "FROM spans\n" not in query
    assert "trace_session_id_remap" not in query
    assert "countIfMerge(error_count)" in query
    assert "countMerge(error_count)" not in query
    assert 0 < call.kwargs["timeout_ms"] <= 9_500
    settings = call.kwargs["settings"]
    assert settings["max_threads"] == 4
    assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024
    assert settings["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert settings["max_result_bytes"] == 32 * 1024 * 1024
    assert "max_rows_to_read" not in settings
    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_sampled"] is False
    assert response["query_exact"] is False
    assert response["query_provenance"] == "materialized_rollup"
    assert response["query_count"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_date_only_rollup_fails_closed_when_query_settings_are_locked(observe_type):
    analytics = mock.Mock()
    analytics.supports_per_query_read_settings = False

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[],
        interval="day",
        metric_id="latency",
        observe_type=observe_type,
    )

    analytics.execute_ch_query.assert_not_called()
    assert response["data"] == []
    assert response["query_complete"] is False
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "query_failed"
    assert response["query_provenance"] == "server_read_policy_unavailable"


@pytest.mark.unit
def test_session_date_only_rollup_fails_closed_when_query_settings_are_locked():
    analytics = mock.Mock()
    analytics.supports_per_query_read_settings = False

    response = fetch_session_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[],
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": "latency"},
    )

    analytics.execute_ch_query.assert_not_called()
    assert response["data"] == []
    assert response["query_complete"] is False
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "query_failed"
    assert response["query_provenance"] == "server_read_policy_unavailable"


@pytest.mark.unit
@pytest.mark.parametrize(("start", "end", "interval"), WINDOWS)
@pytest.mark.parametrize("row_filter", FILTER_SHAPES)
def test_span_filtered_w1_w6_and_sparse_dense_eval_annotation_matrix_is_sampled(
    monkeypatch,
    start,
    end,
    interval,
    row_filter,
):
    window_start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(
        tzinfo=None
    )
    window_end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    sample = GraphCandidateSample(
        rows=(
            {
                "trace_id": "trace-1",
                "id": "span-1",
                "start_time": window_start + timedelta(minutes=1),
                "latency_ms": 42,
                "total_tokens": 12,
                "prompt_tokens": 7,
                "completion_tokens": 5,
                "cost": 0.01,
                "status": "OK",
            },
        ),
        query_complete=False,
        query_status="sampled",
        query_error_code="sample_limit",
        window_start=window_start,
        window_end=window_end,
        elapsed_ms=20,
        query_count=8,
        rows_returned=9,
        result_payload_bytes=500,
        total_rows_lower_bound=9,
        sampling_strategy="time_stratified_latest_state",
        sampling_strata=8,
        sampling_strata_completed=8,
    )
    bounded_read = mock.Mock(return_value=sample)
    exact_read = mock.Mock()
    monkeypatch.setattr(graph_dispatch, "read_graph_candidates", bounded_read)
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=mock.Mock(),
        project_id=PROJECT_ID,
        filters=[_date_filter(start, end), row_filter],
        interval=interval,
        metric_id="latency",
        observe_type="span",
    )

    assert response["query_complete"] is False
    assert response["query_status"] == "sampled"
    assert response["query_sampled"] is True
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
    assert response["query_sampling_strata_completed"] == 8
    assert any(point["value"] == 42 for point in response["data"])
    assert graph_dispatch.graph_payload_is_publishable(
        response,
        allow_sampled=True,
    )
    assert not graph_dispatch.graph_payload_is_publishable(
        response,
        allow_sampled=False,
    )
    assert bounded_read.call_args.kwargs["deadline_ms"] == 9_500
    assert bounded_read.call_args.kwargs["filters"][-1] == row_filter
    exact_read.assert_not_called()


@pytest.mark.unit
def test_trace_filtered_system_graph_uses_bounded_candidates_and_child_decoration(
    monkeypatch,
):
    analytics = mock.Mock()
    start = datetime(2026, 8, 1)
    sample = GraphCandidateSample(
        rows=(
            {
                "trace_id": "trace-1",
                "start_time": start + timedelta(hours=1),
            },
        ),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=start,
        window_end=start + timedelta(days=11),
        elapsed_ms=5,
        query_count=1,
        rows_returned=1,
        result_payload_bytes=100,
        total_rows_lower_bound=1,
    )
    bounded_read = mock.Mock(return_value=sample)
    decorated = {
        "metric_name": "latency",
        "data": [
            {
                "timestamp": "2026-08-01T00:00:00",
                "value": 12,
                "primary_traffic": 1,
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    child_decoration = mock.Mock(return_value=decorated)
    exact_read = mock.Mock()
    monkeypatch.setattr(graph_dispatch, "read_graph_candidates", bounded_read)
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_trace_system_metric_graph",
        child_decoration,
    )
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )
    filters = [
        _date_filter("2026-08-01T00:00:00Z", "2026-08-12T00:00:00Z"),
        _attribute_filter(),
    ]

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    assert response["data"] == decorated["data"]
    assert response["query_exact"] is True
    assert response["query_provenance"] == "bounded_candidates"
    exact_read.assert_not_called()
    bounded_read.assert_called_once()
    assert bounded_read.call_args.kwargs["deadline_ms"] == 9_500
    bounded_analytics = bounded_read.call_args.kwargs["analytics"]
    assert child_decoration.call_args.kwargs["analytics"] is bounded_analytics
    assert child_decoration.call_args.kwargs["sample"] is sample


@pytest.mark.unit
def test_filtered_trace_discovery_and_decoration_share_one_9_5_second_deadline(
    monkeypatch,
):
    analytics = mock.Mock()
    analytics.execute_ch_query.return_value = mock.Mock(data=[], columns=[])
    deadline = mock.Mock()
    deadline.remaining_ms.side_effect = [9_300, 8_700]
    start = datetime(2026, 8, 1)
    sample = GraphCandidateSample(
        rows=(),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=start,
        window_end=start + timedelta(days=1),
        elapsed_ms=1,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        total_rows_lower_bound=0,
    )
    phase_analytics = []

    def bounded_read(*, analytics, **_kwargs):
        phase_analytics.append(analytics)
        analytics.execute_ch_query(
            "SELECT bounded seed",
            {},
            timeout_ms=60_000,
            settings={"max_rows_to_read": 1, "max_threads": 8},
        )
        return sample

    def decorate(*, analytics, **_kwargs):
        phase_analytics.append(analytics)
        analytics.execute_ch_query(
            "SELECT finite child decoration",
            {},
            timeout_ms=60_000,
            settings={"max_threads": 8},
        )
        return {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    deadline_start = mock.Mock(return_value=deadline)
    monkeypatch.setattr(graph_dispatch.ReadDeadline, "start", deadline_start)
    monkeypatch.setattr(graph_dispatch, "read_graph_candidates", bounded_read)
    monkeypatch.setattr(graph_dispatch, "_fetch_trace_system_metric_graph", decorate)

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    assert response["query_status"] == "complete"
    deadline_start.assert_called_once_with(9_500)
    assert phase_analytics[0] is phase_analytics[1]
    assert deadline.remaining_ms.call_count == 2
    assert [
        call.kwargs["timeout_ms"] for call in analytics.execute_ch_query.call_args_list
    ] == [
        9_300,
        8_700,
    ]
    for call in analytics.execute_ch_query.call_args_list:
        settings = call.kwargs["settings"]
        assert "max_rows_to_read" not in settings
        assert settings["max_threads"] == 4
        assert settings["max_memory_usage"] == 36 * 1024 * 1024 * 1024


@pytest.mark.unit
def test_filtered_graph_budget_failure_preserves_advancing_stratum_metadata(
    monkeypatch,
):
    start = datetime(2025, 8, 12)
    incomplete = GraphCandidateSample(
        rows=({"id": "span-1", "start_time": start},),
        query_complete=False,
        query_status="degraded",
        query_error_code="read_budget_exceeded",
        window_start=start,
        window_end=start + timedelta(days=365),
        elapsed_ms=9_475,
        query_count=3,
        rows_returned=4,
        result_payload_bytes=400,
        total_rows_lower_bound=4,
        sampling_strategy="time_stratified_latest_state",
        sampling_strata=8,
        sampling_strata_completed=3,
    )
    exact_read = mock.Mock()
    monkeypatch.setattr(
        graph_dispatch,
        "read_graph_candidates",
        mock.Mock(return_value=incomplete),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=mock.Mock(),
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="month",
        metric_id="traffic",
        observe_type="span",
    )

    assert response["data"] == []
    assert response["query_status"] == "degraded"
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_sampling_strategy"] == "time_stratified_latest_state"
    assert response["query_sampling_strata"] == 8
    assert response["query_sampling_strata_completed"] == 3
    assert response["query_sample_size"] == 1
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
    exact_read.assert_not_called()


@pytest.mark.unit
def test_filtered_graph_programming_defect_is_not_disguised_as_degraded(
    monkeypatch,
):
    start = datetime(2026, 8, 1)
    sample = GraphCandidateSample(
        rows=(),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=start,
        window_end=start + timedelta(days=1),
        elapsed_ms=1,
        query_count=1,
        rows_returned=0,
        result_payload_bytes=0,
        total_rows_lower_bound=0,
    )
    monkeypatch.setattr(
        graph_dispatch,
        "read_graph_candidates",
        mock.Mock(return_value=sample),
    )
    monkeypatch.setattr(
        graph_dispatch,
        "aggregate_system_candidate_graph",
        mock.Mock(side_effect=AssertionError("malformed candidate row")),
    )

    with pytest.raises(AssertionError, match="malformed candidate row"):
        graph_dispatch.fetch_system_metric_graph_ch(
            analytics=mock.Mock(),
            project_id=PROJECT_ID,
            filters=[_attribute_filter()],
            interval="day",
            metric_id="latency",
            observe_type="span",
        )


@pytest.mark.unit
def test_trace_rollup_failure_propagates_without_exact_or_raw_fallback(monkeypatch):
    analytics = mock.Mock()
    failure = NetworkError("private ClickHouse details")
    analytics.execute_ch_query.side_effect = failure
    exact_read = mock.Mock()
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        exact_read,
    )

    with pytest.raises(NetworkError) as raised:
        graph_dispatch.fetch_system_metric_graph_ch(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-08-01T00:00:00Z",
                    "2026-08-12T00:00:00Z",
                )
            ],
            interval="day",
            metric_id="latency",
        )

    assert raised.value is failure
    exact_read.assert_not_called()
    assert "FROM spans_hourly_rollup" in analytics.execute_ch_query.call_args.args[0]


@pytest.mark.unit
def test_session_rollup_failure_propagates_without_exact_or_raw_fallback(monkeypatch):
    analytics = mock.Mock()
    failure = NetworkError("private ClickHouse details")
    analytics.execute_ch_query.side_effect = failure
    exact_read = mock.Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.session_graph.read_or_schedule_exact_snapshot",
        exact_read,
    )

    with pytest.raises(NetworkError) as raised:
        fetch_session_graph_ch(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-08-01T00:00:00Z",
                    "2026-08-12T00:00:00Z",
                )
            ],
            interval="day",
            req_data_config={"id": "session_count", "type": "SYSTEM_METRIC"},
        )

    assert raised.value is failure
    exact_read.assert_not_called()
    query = analytics.execute_ch_query.call_args.args[0]
    assert "FROM spans_per_session AS sps" in query
    assert "trace_session_id_remap" not in query


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["trace", "session"])
def test_rollup_schema_drift_fails_closed_instead_of_publishing_zero(
    monkeypatch, surface
):
    analytics = mock.Mock()
    analytics.execute_ch_query.return_value = mock.Mock(
        data=[{"time_bucket": datetime(2026, 8, 1)}],
        columns=["time_bucket"],
    )
    exact_read = mock.Mock()
    if surface == "trace":
        monkeypatch.setattr(
            graph_dispatch,
            "read_or_schedule_exact_snapshot",
            exact_read,
        )

        def invoke():
            return graph_dispatch.fetch_system_metric_graph_ch(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(
                        "2026-08-01T00:00:00Z",
                        "2026-08-12T00:00:00Z",
                    )
                ],
                interval="day",
                metric_id="latency",
            )
    else:
        monkeypatch.setattr(
            "tracer.services.clickhouse.session_graph.read_or_schedule_exact_snapshot",
            exact_read,
        )

        def invoke():
            return fetch_session_graph_ch(
                analytics=analytics,
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(
                        "2026-08-01T00:00:00Z",
                        "2026-08-12T00:00:00Z",
                    )
                ],
                interval="day",
                req_data_config={"id": "session_count", "type": "SYSTEM_METRIC"},
            )

    with pytest.raises(graph_dispatch.BoundedGraphReadError) as raised:
        invoke()

    assert raised.value.error_code == "query_failed"
    exact_read.assert_not_called()
