from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest
from clickhouse_driver.errors import ErrorCodes, NetworkError, ServerException
from django.conf import settings as django_settings

from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
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
def test_span_filtered_w1_w6_and_sparse_dense_eval_annotation_matrix_is_complete(
    monkeypatch,
    start,
    end,
    interval,
    row_filter,
):
    direct_read = mock.Mock(
        return_value={
            "metric_name": "latency",
            "data": [{"timestamp": start, "value": 42, "primary_traffic": 1}],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_exact": False,
            "query_provenance": "bounded_candidates",
        }
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        direct_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=mock.Mock(),
        project_id=PROJECT_ID,
        filters=[_date_filter(start, end), row_filter],
        interval=interval,
        metric_id="latency",
        observe_type="span",
    )

    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_sampled"] is False
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
    assert response["data"][0]["value"] == 42
    assert graph_dispatch.graph_payload_is_publishable(response, allow_sampled=False)
    direct_read.assert_called_once()
    assert direct_read.call_args.kwargs["filters"][-1] == row_filter
    assert direct_read.call_args.kwargs["observe_type"] == "span"


@pytest.mark.unit
def test_trace_filtered_system_graph_uses_direct_raw_reader(
    monkeypatch,
):
    analytics = mock.Mock()
    direct_payload = {
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
        "query_exact": False,
        "query_provenance": "bounded_candidates",
    }
    direct_read = mock.Mock(return_value=direct_payload)
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        direct_read,
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

    assert response["data"] == direct_payload["data"]
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
    direct_read.assert_called_once()
    assert direct_read.call_args.kwargs["project_id"] == PROJECT_ID
    assert direct_read.call_args.kwargs["filters"] == filters
    assert direct_read.call_args.kwargs["metric_id"] == "latency"
    assert direct_read.call_args.kwargs["observe_type"] == "trace"


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
def test_single_node_install_seeds_the_filtered_trace_graph(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        mock.Mock(data=[{"rows": 1_600_000, "marks": 259}], columns=[]),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter("account_id", filter_type="text", value="acct-1"),
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
    graph_query = graph_call.args[0]
    assert "trace_id IN (" in graph_query
    assert "GLOBAL IN" not in graph_query
    assert "cluster(" not in graph_query
    # The seed prunes candidates; the outer read still classifies each one.
    assert "graph_match_0 = 1" in graph_query
    assert "FINAL" not in graph_query.upper()
    assert response["query_count"] == 2


@pytest.mark.unit
def test_single_node_dense_witness_keeps_one_pass_trace_query(monkeypatch):
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        mock.Mock(data=[{"rows": 106_000_000, "marks": 14_612}], columns=[]),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter("prompt_slug", filter_type="text", value="summary"),
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
    assert "trace_id IN (" not in analytics.execute_ch_query.call_args_list[1].args[0]
    assert response["query_count"] == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        ErrorCodes.NOT_FOUND_COLUMN_IN_BLOCK,
        ErrorCodes.ILLEGAL_TYPE_OF_ARGUMENT,
        ErrorCodes.UNKNOWN_IDENTIFIER,
        ErrorCodes.TYPE_MISMATCH,
        ErrorCodes.NO_COMMON_TYPE,
    ],
)
def test_seed_probe_failure_degrades_to_the_unseeded_graph(monkeypatch, code):
    """A failed probe means "no candidate", never a failed graph request.

    None of these codes is classified as a read-budget or transport failure,
    so anything narrower than a blanket swallow would propagate them out of a
    request that returns a correct result with no probe at all.
    """
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "",
    )
    analytics = mock.Mock()
    analytics.execute_ch_query.side_effect = [
        ServerException("probe diagnostic", code=code),
        _empty_graph_query_result(),
    ]
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter("account_id", filter_type="text", value="acct-1"),
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
    probe_call, graph_call = analytics.execute_ch_query.call_args_list
    assert "EXPLAIN ESTIMATE" in probe_call.args[0]
    graph_query = graph_call.args[0]
    assert "EXPLAIN ESTIMATE" not in graph_query
    assert "trace_id IN (" not in graph_query
    assert "GLOBAL IN" not in graph_query
    assert "graph_match_0 = 1" in graph_query
    assert response["query_complete"] is True
    assert response["query_status"] == "complete"
    assert response["query_count"] == 2


@pytest.mark.unit
def test_seed_probe_leaves_the_main_read_a_wall_floor(monkeypatch):
    """An overrunning probe cannot cut the graph statement to a 1 ms wall."""
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "",
    )
    clock = [1_000.0]
    monkeypatch.setattr(graph_dispatch, "monotonic", lambda: clock[0])
    analytics = mock.Mock()

    def _overrunning_probe(query, params, **kwargs):
        if "EXPLAIN ESTIMATE" in query:
            clock[0] += 31.0
            return mock.Mock(data=[], columns=[])
        return _empty_graph_query_result()

    analytics.execute_ch_query.side_effect = _overrunning_probe
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        _span_attribute_filter("account_id", filter_type="text", value="acct-1"),
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
    graph_call = analytics.execute_ch_query.call_args_list[1]
    # 30 s wall minus the 2.5 s probe budget: the floor is stated, not the
    # leftover of whatever the probe actually spent.
    assert graph_call.kwargs["timeout_ms"] == 27_500
    assert "trace_id IN (" not in graph_call.args[0]
    assert response["query_count"] == 2


def _prior_release_probe_grants(
    timeout_ms: int,
    probe_cost_ms: int,
    candidates: int,
) -> list[int]:
    """The probe schedule this surface ran wherever the seed was already live.

    Transcribed from the pre-ungate release: a total budget of
    ``min(2500, wall - 25)`` ms, a probe launched while at least 100 ms of that
    budget is left, and a per-probe grant of ``min(1500, remaining)``. The
    oracle is written out here on purpose - deriving it from the production
    constants would make the test agree with whatever the code does.
    """

    budget_ms = max(0, min(2_500, timeout_ms - 25))
    if budget_ms < 100:
        return []
    grants: list[int] = []
    elapsed_ms = 0
    for _ in range(candidates):
        remaining_ms = budget_ms - elapsed_ms
        if remaining_ms < 100:
            break
        grants.append(min(1_500, remaining_ms))
        elapsed_ms += probe_cost_ms
    return grants


_SELECTIVE_ESTIMATE = {"rows": 1_600_000, "marks": 259}
_DENSE_ESTIMATE = {"rows": 106_000_000, "marks": 14_612}


@pytest.mark.unit
@pytest.mark.parametrize(
    "timeout_ms,probe_cost_ms,estimates,expected_grants",
    [
        # A: three candidates, 600 ms probes, only the third selective.
        (
            30_000,
            600,
            [_DENSE_ESTIMATE, _DENSE_ESTIMATE, _SELECTIVE_ESTIMATE],
            [1_500, 1_500, 1_300],
        ),
        # B: a wall short enough that a tenth of it would disable the probe.
        (900, 0, [_SELECTIVE_ESTIMATE], [875]),
        (30_000, 0, [_SELECTIVE_ESTIMATE], [1_500]),
        (5_000, 0, [_SELECTIVE_ESTIMATE], [1_500]),
        # A probe that ignores its grant and spends the whole wall: this path
        # still hands the graph statement the bare remainder, the way the
        # prior release did. It is the single-node floor, not this one, that
        # turns that 1 ms into 27,500 ms.
        (30_000, 31_000, [_SELECTIVE_ESTIMATE], [1_500]),
    ],
)
def test_cluster_env_keeps_the_prior_release_probe_schedule(
    monkeypatch,
    timeout_ms,
    probe_cost_ms,
    estimates,
    expected_grants,
):
    """Un-gating the seed may not move the read where the seed was already live.

    With the shard cluster set this surface probed before this change, so the
    schedule, the per-probe grant and the graph statement's requested timeout
    must stay exactly what that install runs today: no whole-cap launch rule
    (which would drop a third-candidate seed) and no wall floor (which would
    change a kwarg the install already receives).
    """
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "all-sharded",
    )
    clock = [1_000.0]
    monkeypatch.setattr(graph_dispatch, "monotonic", lambda: clock[0])
    analytics = mock.Mock()
    remaining_estimates = list(estimates)

    def _probe(query, params, **kwargs):
        if "EXPLAIN ESTIMATE" in query:
            clock[0] += probe_cost_ms / 1000
            row = remaining_estimates.pop(0) if remaining_estimates else _DENSE_ESTIMATE
            return mock.Mock(data=[row], columns=["rows", "marks"])
        return _empty_graph_query_result()

    analytics.execute_ch_query.side_effect = _probe
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        *[
            _span_attribute_filter(f"attr_{index}", filter_type="text", value="value")
            for index in range(len(estimates))
        ],
    ]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=timeout_ms,
    )

    calls = analytics.execute_ch_query.call_args_list
    probe_calls = [call for call in calls if "EXPLAIN ESTIMATE" in call.args[0]]
    assert [call.kwargs["timeout_ms"] for call in probe_calls] == expected_grants
    assert expected_grants == _prior_release_probe_grants(
        timeout_ms,
        probe_cost_ms,
        len(estimates),
    )
    graph_call = calls[-1]
    # The selective candidate is the last probed one in every case above, so
    # the seed is admitted and the cluster rendering is the one that ships.
    assert "trace_id GLOBAL IN (" in graph_call.args[0]
    assert "cluster('all-sharded'" in graph_call.args[0]
    # The plain remainder, not a floor: the same kwarg the install gets today.
    assert graph_call.kwargs["timeout_ms"] == max(
        1, timeout_ms - len(expected_grants) * probe_cost_ms
    )
    assert response["query_count"] == len(expected_grants) + 1


@pytest.mark.unit
def test_single_node_probe_spend_stays_inside_the_seed_budget(monkeypatch):
    """Honoured probe grants keep spend inside the budget, and the read keeps
    its floor.

    Six rejected candidates against probes that stop at the timeout they are
    handed: the grants are ``min(1500, remaining)``, so the third probe is cut
    to 500 ms and total spend lands exactly on the 2,500 ms budget. The graph
    statement is then asked for the 27,500 ms floor rather than the leftover.
    """
    monkeypatch.setattr(
        graph_dispatch.settings,
        "DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER",
        "",
    )
    clock = [1_000.0]
    monkeypatch.setattr(graph_dispatch, "monotonic", lambda: clock[0])
    analytics = mock.Mock()

    def _honouring_dense_probe(query, params, **kwargs):
        if "EXPLAIN ESTIMATE" in query:
            clock[0] += min(1.0, kwargs["timeout_ms"] / 1000)
            return mock.Mock(data=[_DENSE_ESTIMATE], columns=["rows", "marks"])
        return _empty_graph_query_result()

    analytics.execute_ch_query.side_effect = _honouring_dense_probe
    filters = [
        _date_filter("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"),
        *[
            _span_attribute_filter(f"attr_{index}", filter_type="text", value="value")
            for index in range(6)
        ],
    ]
    started = clock[0]

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=filters,
        interval="day",
        metric_id="latency",
        observe_type="trace",
        timeout_ms=30_000,
    )

    probe_calls = [
        call
        for call in analytics.execute_ch_query.call_args_list
        if "EXPLAIN ESTIMATE" in call.args[0]
    ]
    assert [call.kwargs["timeout_ms"] for call in probe_calls] == [1_500, 1_500, 500]
    assert (clock[0] - started) * 1000 == 2_500
    graph_call = analytics.execute_ch_query.call_args_list[-1]
    assert "trace_id IN (" not in graph_call.args[0]
    # 30 s wall minus the 2.5 s budget, floored rather than 27,500 by accident:
    # the plain remainder here is the same number, and the previous test pins
    # the floor against a probe that ignores its grant entirely.
    assert graph_call.kwargs["timeout_ms"] == 27_500
    # A rejected multi-candidate shape publishes 1 + probes, not 2.
    assert response["query_count"] == 4


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
def test_filtered_raw_graph_statement_uses_one_interactive_deadline(
    monkeypatch,
):
    analytics = mock.Mock()
    analytics.execute_ch_query.return_value = mock.Mock(data=[], columns=[])
    deadline = mock.Mock()
    deadline.remaining_ms.return_value = 9_300
    observed_analytics = []

    def direct_read(*, analytics, **_kwargs):
        observed_analytics.append(analytics)
        analytics.execute_ch_query(
            "SELECT raw aggregation",
            {},
            timeout_ms=60_000,
            settings={"max_rows_to_read": 1, "max_threads": 8},
        )
        return {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_exact": False,
            "query_provenance": "bounded_candidates",
        }

    deadline_start = mock.Mock(return_value=deadline)
    monkeypatch.setattr(graph_dispatch.ReadDeadline, "start", deadline_start)
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        direct_read,
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    assert response["query_status"] == "complete"
    deadline_start.assert_called_once_with(
        django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert len(observed_analytics) == 1
    assert deadline.remaining_ms.call_count == 1
    assert [
        call.kwargs["timeout_ms"] for call in analytics.execute_ch_query.call_args_list
    ] == [9_300]
    for call in analytics.execute_ch_query.call_args_list:
        read_settings = call.kwargs["settings"]
        assert "max_rows_to_read" not in read_settings
        assert (
            read_settings["max_threads"]
            == django_settings.DASHBOARD_TRACE_READ_MAX_THREADS
        )
        assert (
            read_settings["max_memory_usage"]
            == django_settings.OBSERVABILITY_LIST_MAX_MEMORY_BYTES
        )


@pytest.mark.unit
def test_filtered_graph_raw_budget_failure_fails_closed_without_sample(
    monkeypatch,
):
    direct_read = mock.Mock(
        side_effect=graph_dispatch.ExactGraphReadError("exact graph deadline exceeded")
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
        interval="month",
        metric_id="traffic",
        observe_type="span",
    )

    assert response["data"] == []
    assert response["query_status"] == "degraded"
    assert response["query_sampled"] is False
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
    direct_read.assert_called_once()


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
    assert cache_probe.call_args.kwargs["schedule_on_miss"] is False


@pytest.mark.unit
def test_filtered_graph_budget_failure_schedules_one_heavy_read(monkeypatch):
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
        "query_refreshing": True,
    }
    cache_calls = []

    def cache_read(*args, **kwargs):
        cache_calls.append((args, kwargs))
        if kwargs.get("schedule_on_miss") is False:
            return {**pending, "query_refreshing": False}
        return pending

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        cache_read,
    )
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        mock.Mock(
            side_effect=graph_dispatch.ExactGraphReadError(
                "exact graph deadline exceeded"
            )
        ),
    )

    response = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=mock.Mock(),
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="day",
        metric_id="latency",
        observe_type="trace",
        organization_id="33333333-3333-4333-8333-333333333333",
        workspace_id="44444444-4444-4444-8444-444444444444",
    )

    assert response == pending
    assert len(cache_calls) == 2
    assert cache_calls[0][1]["schedule_on_miss"] is False
    assert cache_calls[1][1]["schedule_on_miss"] is True
    assert cache_calls[1][1]["refresh"] is True
    assert cache_calls[1][0][1]["organization_id"] == (
        "33333333-3333-4333-8333-333333333333"
    )


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
def test_filtered_graph_programming_defect_is_not_disguised_as_degraded(
    monkeypatch,
):
    direct_read = mock.Mock(side_effect=AssertionError("malformed candidate row"))
    monkeypatch.setattr(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        direct_read,
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
