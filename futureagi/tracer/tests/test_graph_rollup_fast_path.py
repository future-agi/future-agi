from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest
from clickhouse_driver.errors import NetworkError
from django.conf import settings as django_settings

from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
from tracer.services.clickhouse.session_graph import fetch_session_graph_ch

PROJECT_ID = "22222222-2222-4222-8222-222222222222"


def _assert_exact_route(
    monkeypatch,
    *,
    surface="trace",
    filters=None,
    interval="day",
    refresh=False,
    payload=None,
    analytics=None,
):
    from tracer.services.clickhouse import session_graph

    analytics = analytics if analytics is not None else mock.Mock()
    filters = list(filters or [])
    payload = (
        payload
        if payload is not None
        else {
            "metric_name": "latency",
            "data": [],
            "query_status": "pending",
            "query_complete": False,
            "query_sampled": False,
            "query_refreshing": True,
        }
    )
    schedule = mock.Mock(return_value=payload)
    owner = session_graph if surface == "session" else graph_dispatch
    monkeypatch.setattr(owner, "read_or_schedule_exact_snapshot", schedule)
    raw = mock.Mock(
        side_effect=AssertionError("public graphs must not read raw versions")
    )
    monkeypatch.setattr(graph_dispatch, "_fetch_direct_raw_system_metric_graph", raw)
    common = {
        "analytics": analytics,
        "project_id": PROJECT_ID,
        "filters": filters,
        "interval": interval,
        "refresh": refresh,
        "organization_id": "organization",
        "workspace_id": "workspace",
    }
    if surface == "session":
        result = fetch_session_graph_ch(
            **common, req_data_config={"type": "SYSTEM_METRIC", "id": "latency"}
        )
    else:
        result = graph_dispatch.fetch_system_metric_graph_ch(
            **common, metric_id="latency", observe_type=surface
        )
    assert result is payload
    schedule.assert_called_once()
    namespace, identity = schedule.call_args.args
    assert namespace == (
        "observe-session-system-graph"
        if surface == "session"
        else "observe-system-graph"
    )
    assert identity["filters"] == filters and identity["interval"] == interval
    assert identity["organization_id"] == "organization"
    assert identity["workspace_id"] == "workspace"
    assert schedule.call_args.kwargs["refresh"] is refresh
    analytics.execute_ch_query.assert_not_called()
    raw.assert_not_called()
    return schedule


def _legacy_rollup(
    *, surface="trace", analytics, filters, interval="day", metric_id="latency"
):
    # Retain low-level rollup diagnostics without routing public exact reads here.
    from time import monotonic

    from tracer.services.clickhouse import session_graph

    common = {
        "analytics": analytics,
        "project_id": PROJECT_ID,
        "filters": filters,
        "interval": interval,
        "metric_id": metric_id,
    }
    if surface == "session":
        return session_graph._fetch_rollup_system_metric_graph(
            **common, started=monotonic()
        )
    return graph_dispatch._fetch_rollup_system_metric_graph(
        **common, observe_type="trace", timeout_ms=30000
    )


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
def test_legacy_trace_primary_date_only_uses_one_interactive_rollup_query(
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

    response = _legacy_rollup(
        analytics=analytics,
        filters=filters,
        interval=interval,
        metric_id="latency",
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
    assert (
        0
        < call.kwargs["timeout_ms"]
        <= django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    settings = call.kwargs["settings"]
    assert settings["max_threads"] == django_settings.DASHBOARD_TRACE_READ_MAX_THREADS
    assert (
        settings["max_memory_usage"]
        == django_settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES
    )
    assert settings["max_bytes_to_read"] == django_settings.OBSERVABILITY_LIST_MAX_BYTES
    assert (
        settings["max_result_bytes"]
        == django_settings.DASHBOARD_ROLLUP_MAX_RESULT_BYTES
    )
    assert "max_rows_to_read" not in settings
    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_sampled"] is False
    assert response["query_exact"] is False
    assert response["query_provenance"] == "materialized_rollup"
    assert response["query_count"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_time_only_graph_schedules_worker_with_own_read_policy(
    monkeypatch, observe_type
):
    analytics = mock.Mock()
    analytics.supports_per_query_read_settings = False
    _assert_exact_route(monkeypatch, surface=observe_type, analytics=analytics)


@pytest.mark.unit
def test_session_graph_schedules_worker_with_own_read_policy(monkeypatch):
    analytics = mock.Mock()
    analytics.supports_per_query_read_settings = False
    _assert_exact_route(monkeypatch, surface="session", analytics=analytics)


@pytest.mark.unit
@pytest.mark.parametrize(("start", "end", "interval"), WINDOWS)
@pytest.mark.parametrize("row_filter", FILTER_SHAPES)
def test_span_filter_matrix_schedules_exact_population(
    monkeypatch, start, end, interval, row_filter
):
    _assert_exact_route(
        monkeypatch,
        surface="span",
        filters=[_date_filter(start, end), row_filter],
        interval=interval,
    )


@pytest.mark.unit
def test_trace_graph_preserves_last_complete_exact_snapshot(monkeypatch):
    payload = {
        "metric_name": "latency",
        "data": [
            {"timestamp": "2026-08-01T00:00:00", "value": 12, "primary_traffic": 1}
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
        "query_exact": True,
        "query_provenance": "exact_snapshot",
        "query_refreshing": True,
    }
    _assert_exact_route(
        monkeypatch, filters=[_attribute_filter()], payload=payload, refresh=True
    )


def _empty_graph_query_result():
    return mock.Mock(
        data=[],
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


@pytest.mark.unit
def test_selective_scalar_witness_adds_cost_gated_trace_seed(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        mock.Mock(data=[{"rows": 1_600_000, "marks": 259}], columns=[]),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter(
            "customer_id",
            filter_type="text",
            value="customer-42",
        ),
    ]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=30_000,
    )

    assert analytics.execute_ch_query.call_count == 2
    estimate_call, graph_call = analytics.execute_ch_query.call_args_list
    assert "EXPLAIN ESTIMATE" in estimate_call.args[0]
    assert estimate_call.kwargs["timeout_ms"] <= 1_500
    assert "trace_id GLOBAL IN" in graph_call.args[0]
    assert graph_call.args[0].count("cluster('all-sharded'") == 2
    assert "FINAL" not in graph_call.args[0].upper()
    assert "graph_match_0 = 1" in graph_call.args[0]
    assert response["query_count"] == 2


@pytest.mark.unit
def test_dense_scalar_witness_keeps_one_pass_trace_query(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        mock.Mock(data=[{"rows": 106_000_000, "marks": 14_612}], columns=[]),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter(
            "prompt_slug",
            filter_type="text",
            value="summary",
        ),
    ]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=30_000,
    )

    assert analytics.execute_ch_query.call_count == 2
    graph_query = analytics.execute_ch_query.call_args_list[1].args[0]
    assert "trace_id GLOBAL IN" not in graph_query
    assert graph_query.count("cluster('all-sharded'") == 1
    assert response["query_count"] == 2


@pytest.mark.unit
def test_multiple_filters_choose_selective_witness_and_reapply_every_filter(
    monkeypatch,
):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        mock.Mock(data=[{"rows": 106_000_000, "marks": 14_612}], columns=[]),
        mock.Mock(data=[{"rows": 1_600_000, "marks": 259}], columns=[]),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter(
            "prompt_slug",
            filter_type="text",
            value="summary",
        ),
        _span_attribute_filter(
            "customer_id",
            filter_type="text",
            value="customer-42",
        ),
    ]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=30_000,
    )

    assert analytics.execute_ch_query.call_count == 3
    assert "summary" in analytics.execute_ch_query.call_args_list[0].args[1].values()
    assert (
        "customer-42" in analytics.execute_ch_query.call_args_list[1].args[1].values()
    )
    graph_query = analytics.execute_ch_query.call_args_list[2].args[0]
    assert "trace_id GLOBAL IN" in graph_query
    assert "graph_match_0 = 1" in graph_query
    assert "graph_match_1 = 1" in graph_query
    assert response["query_count"] == 3


@pytest.mark.unit
def test_transient_seed_estimate_failure_falls_back_to_one_pass(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        NetworkError("estimate transport unavailable"),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter(
            "customer_id",
            filter_type="text",
            value="customer-42",
        ),
    ]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=30_000,
    )

    graph_query = analytics.execute_ch_query.call_args_list[1].args[0]
    assert "trace_id GLOBAL IN" not in graph_query
    assert response["query_count"] == 2


@pytest.mark.unit
def test_negative_and_span_graph_filters_never_use_trace_seed(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    negative_filter = _span_attribute_filter(
        "customer_id",
        filter_type="text",
        value="customer-42",
    )
    negative_filter["filter_config"]["filter_op"] = "not_equals"

    for observe_type, row_filter in (
        ("trace", negative_filter),
        (
            "span",
            _span_attribute_filter(
                "customer_id",
                filter_type="text",
                value="customer-42",
            ),
        ),
    ):
        analytics = mock.Mock()
        analytics.execute_ch_query.return_value = _empty_graph_query_result()
        response = graph_dispatch._fetch_direct_raw_system_metric_graph(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-07-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ),
                row_filter,
            ],
            interval="day",
            metric_id="latency",
            observe_type=observe_type,
            timeout_ms=30_000,
        )

        analytics.execute_ch_query.assert_called_once()
        assert "EXPLAIN ESTIMATE" not in analytics.execute_ch_query.call_args.args[0]
        assert "trace_id GLOBAL IN" not in analytics.execute_ch_query.call_args.args[0]
        assert response["query_count"] == 1


@pytest.mark.unit
def test_filtered_graph_does_not_start_interactive_statement_deadline(monkeypatch):
    deadline_start = mock.Mock(side_effect=AssertionError("no synchronous graph read"))
    monkeypatch.setattr(graph_dispatch.ReadDeadline, "start", deadline_start)
    _assert_exact_route(monkeypatch, filters=[_attribute_filter()])
    deadline_start.assert_not_called()


@pytest.mark.unit
def test_filtered_graph_preserves_failed_snapshot_without_sample_fallback(monkeypatch):
    payload = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "failed",
        "query_sampled": False,
        "query_error_code": "query_failed",
    }
    _assert_exact_route(
        monkeypatch, surface="span", filters=[_attribute_filter()], payload=payload
    )


@pytest.mark.unit
def test_filtered_graph_poll_does_not_duplicate_running_background_read(monkeypatch):
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
    }
    cache_probe = mock.Mock(return_value=pending)
    direct_read = mock.Mock()
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        cache_probe,
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        direct_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=mock.Mock(),
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="day",
        metric_id="latency",
        observe_type="trace",
        organization_id="33333333-3333-4333-8333-333333333333",
    )

    assert response == pending
    direct_read.assert_not_called()
    assert cache_probe.call_count == 1
    assert cache_probe.call_args.kwargs["schedule_on_miss"] is True


@pytest.mark.unit
def test_filtered_graph_cold_miss_schedules_one_exact_read(monkeypatch):
    schedule = _assert_exact_route(monkeypatch, filters=[_attribute_filter()])
    assert schedule.call_args.kwargs["schedule_on_miss"] is True
    assert schedule.call_args.kwargs["refresh"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "end", "requested", "effective"),
    [
        (
            datetime(2026, 6, 1),
            datetime(2026, 8, 30),
            "day",
            "day",
        ),
        (
            datetime(2026, 5, 31),
            datetime(2026, 9, 1),
            "month",
            "week",
        ),
    ],
)
def test_exact_graph_forces_weekly_buckets_only_beyond_three_months(
    start, end, requested, effective
):
    assert (
        exact_graph_reads._effective_graph_interval(requested, start, end) == effective
    )


@pytest.mark.unit
def test_filtered_graph_dispatch_defect_is_not_disguised_as_degraded(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        mock.Mock(side_effect=AssertionError("malformed snapshot")),
    )
    with pytest.raises(AssertionError, match="malformed snapshot"):
        graph_dispatch.fetch_system_metric_graph_ch(
            analytics=mock.Mock(),
            project_id=PROJECT_ID,
            filters=[_attribute_filter()],
            interval="day",
            metric_id="latency",
            observe_type="span",
        )


@pytest.mark.unit
def test_legacy_trace_rollup_failure_propagates_without_exact_or_raw_fallback(
    monkeypatch,
):
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
        _legacy_rollup(
            analytics=analytics,
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
def test_legacy_session_rollup_failure_propagates_without_exact_or_raw_fallback(
    monkeypatch,
):
    analytics = mock.Mock()
    failure = NetworkError("private ClickHouse details")
    analytics.execute_ch_query.side_effect = failure
    exact_read = mock.Mock()
    monkeypatch.setattr(
        "tracer.services.clickhouse.session_graph.read_or_schedule_exact_snapshot",
        exact_read,
    )

    with pytest.raises(NetworkError) as raised:
        _legacy_rollup(
            surface="session",
            analytics=analytics,
            filters=[
                _date_filter(
                    "2026-08-01T00:00:00Z",
                    "2026-08-12T00:00:00Z",
                )
            ],
            interval="day",
            metric_id="session_count",
        )

    assert raised.value is failure
    exact_read.assert_not_called()
    query = analytics.execute_ch_query.call_args.args[0]
    assert "FROM spans_per_session AS sps" in query
    assert "trace_session_id_remap" not in query


@pytest.mark.unit
@pytest.mark.parametrize("surface", ["trace", "session"])
def test_legacy_rollup_schema_drift_fails_closed_instead_of_publishing_zero(
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
            return _legacy_rollup(
                analytics=analytics,
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
            return _legacy_rollup(
                surface="session",
                analytics=analytics,
                filters=[
                    _date_filter(
                        "2026-08-01T00:00:00Z",
                        "2026-08-12T00:00:00Z",
                    )
                ],
                interval="day",
                metric_id="session_count",
            )

    with pytest.raises(graph_dispatch.BoundedGraphReadError) as raised:
        invoke()

    assert raised.value.error_code == "query_failed"
    exact_read.assert_not_called()
