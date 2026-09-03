import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver.errors import ServerException
from django.core.cache import cache
from django.db import DatabaseError
from django.test import override_settings

from tracer.services.clickhouse.exact_graph_reads import (
    EXACT_GRAPH_MAX_BYTES_TO_READ,
    ExactGraphReadError,
    _annotation_label_ids_for_filters,
    _enumerate_exact_trace_ids,
    _filter_relation_requirements,
    _merge_exact_trace_contribution_rows,
    output_bucket_partitions,
    read_exact_all_system_metrics,
    read_exact_annotation_graph,
    read_exact_eval_graph,
    read_exact_session_system_graph,
    read_exact_system_graph,
    read_exact_user_system_graph,
)
from tracer.services.clickhouse.query_builders.dashboard import AGGREGATIONS
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DATASET_AGGREGATIONS,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.simulation_dashboard import (
    SIMULATION_AGGREGATIONS,
)
from tracer.services.exact_aggregation_cache import (
    EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS,
    EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS,
    EXACT_AGGREGATION_WORKFLOW_EXECUTION_TIMEOUT_SECONDS,
    EXACT_AGGREGATION_WORKFLOW_RUN_TIMEOUT_SECONDS,
    _exact_refresh_workflow_task_id,
    begin_exact_refresh,
    exact_payload_is_complete,
    exact_refresh_state,
    finish_exact_refresh,
    normalize_exact_observe_identity,
    publish_exact_snapshot,
    publish_exact_snapshot_for_refresh,
    read_exact_snapshot,
    read_or_schedule_exact_snapshot,
    refresh_claim_is_current,
    snapshot_cache_key,
)


def _time_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _combined_session_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        _time_filter(start, end),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
        {
            "column_id": "session_id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["11111111-1111-4111-8111-111111111111"],
            },
        },
        {
            "column_id": "duration",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than_or_equal",
                "filter_value": 5,
            },
        },
        {
            "column_id": "first_message",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "hello",
            },
        },
    ]


@pytest.mark.unit
def test_output_partitions_only_cut_on_bucket_boundaries():
    start = datetime(2026, 8, 1, 0, 17)
    end = datetime(2026, 8, 1, 8, 42)

    partitions = output_bucket_partitions(start, end, "hour", max_buckets=3)

    assert partitions == (
        (start, datetime(2026, 8, 1, 3, 0)),
        (datetime(2026, 8, 1, 3, 0), datetime(2026, 8, 1, 6, 0)),
        (datetime(2026, 8, 1, 6, 0), end),
    )


@pytest.mark.unit
def test_exact_span_scan_aligns_partial_window_to_storage_identity_hour():
    start = datetime(2026, 8, 1, 0, 2)
    end = datetime(2026, 8, 1, 0, 14)

    class Analytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, **_kwargs):
            self.calls.append((query, dict(params)))
            return SimpleNamespace(data=[], columns=["time_bucket"])

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    assert len(analytics.calls) == 1
    query, params = analytics.calls[0]
    assert params["graph_partition_start"] == datetime(2026, 8, 1, 0, 0)
    assert params["graph_partition_end"] == datetime(2026, 8, 1, 1, 0)
    assert params["graph_contribution_start"] == start
    assert params["graph_contribution_end"] == end
    prewhere = query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "graph_partition_start" in prewhere
    assert "graph_partition_end" in prewhere
    assert "graph_contribution_start" not in prewhere
    assert "graph_contribution_end" not in prewhere
    assert result["query_count"] == 1


@pytest.mark.unit
def test_exact_span_sparse_partitions_grow_without_gaps_or_duplicates():
    start = datetime(2026, 8, 1, 0, 2)
    end = datetime(2026, 8, 1, 3, 14)

    class Analytics:
        def __init__(self):
            self.attempts = []

        def execute_ch_query(self, _query, params, **_kwargs):
            self.attempts.append(
                (
                    params["graph_partition_start"],
                    params["graph_partition_end"],
                    params["graph_contribution_start"],
                    params["graph_contribution_end"],
                )
            )
            return SimpleNamespace(
                data=[],
                columns=["time_bucket"],
                query_time_ms=100.0,
            )

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    widths = [right - left for left, right, *_rest in analytics.attempts]
    assert widths == [
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=1),
    ]
    assert analytics.attempts[0] == (
        datetime(2026, 8, 1, 0, 0),
        datetime(2026, 8, 1, 1, 0),
        start,
        datetime(2026, 8, 1, 1, 0),
    )
    assert analytics.attempts[-1] == (
        datetime(2026, 8, 1, 3, 0),
        datetime(2026, 8, 1, 4, 0),
        datetime(2026, 8, 1, 3, 0),
        end,
    )
    assert all(
        left[1] == right[0]
        for left, right in zip(
            analytics.attempts,
            analytics.attempts[1:],
            strict=False,
        )
    )
    assert result["query_count"] == 3
    assert result["query_complete"] is True


@pytest.mark.unit
def test_exact_span_budget_retry_halves_same_cursor_and_learns_ceiling():
    start = datetime(2026, 8, 1)
    end = start + timedelta(hours=8)

    class Analytics:
        def __init__(self):
            self.attempts = []

        def execute_ch_query(self, _query, params, **_kwargs):
            attempt = (
                params["graph_partition_start"],
                params["graph_partition_end"],
            )
            self.attempts.append(attempt)
            if attempt[1] - attempt[0] > timedelta(hours=2):
                raise ServerException("private detail", code=159)
            return SimpleNamespace(
                data=[],
                columns=["time_bucket"],
                query_time_ms=100.0,
            )

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    assert [right - left for left, right in analytics.attempts] == [
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=2),
        timedelta(hours=2),
        timedelta(hours=1),
    ]
    # The failed four-hour attempt and its two-hour retry start at the same
    # cursor. Only successful intervals form the final exact cover.
    assert analytics.attempts[2][0] == analytics.attempts[3][0]
    successful = [
        attempt
        for attempt in analytics.attempts
        if attempt[1] - attempt[0] <= timedelta(hours=2)
    ]
    assert successful[0][0] == start
    assert successful[-1][1] == end
    assert all(
        left[1] == right[0]
        for left, right in zip(successful, successful[1:], strict=False)
    )
    assert result["query_count"] == 6


@pytest.mark.unit
def test_exact_span_adaptive_reader_does_not_retry_programming_errors():
    start = datetime(2026, 8, 1)
    end = start + timedelta(hours=2)

    class Analytics:
        def __init__(self):
            self.calls = 0

        def execute_ch_query(self, _query, _params, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ServerException("private detail", code=62)
            return SimpleNamespace(
                data=[],
                columns=["time_bucket"],
                query_time_ms=100.0,
            )

    analytics = Analytics()
    with pytest.raises(ServerException):
        read_exact_system_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(start, end),
            interval="minute",
            metric_id="traffic",
            observe_type="span",
        )

    assert analytics.calls == 2


@pytest.mark.unit
def test_annotation_completeness_labels_are_sorted_and_metadata_failure_is_retryable(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    has_annotation = {
        "column_id": "has_annotation",
        "filter_config": {"filter_op": "equals", "filter_value": True},
    }
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: [
            SimpleNamespace(id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            SimpleNamespace(id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ],
    )
    assert _annotation_label_ids_for_filters("project", [has_annotation]) == (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )

    def unavailable(_project_id):
        raise DatabaseError("private backend details")

    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        unavailable,
    )
    with pytest.raises(
        ExactGraphReadError,
        match="Annotation metadata is temporarily unavailable",
    ):
        _annotation_label_ids_for_filters("project", [has_annotation])


@pytest.mark.unit
def test_annotation_metadata_is_not_read_without_completeness_filter(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: pytest.fail("metadata should not be queried"),
    )
    assert (
        _annotation_label_ids_for_filters(
            "project",
            [
                _time_filter(
                    datetime(2026, 1, 1),
                    datetime(2026, 1, 2),
                )
            ],
        )
        is None
    )


@pytest.mark.unit
def test_annotation_completeness_preserves_authoritative_empty_label_set(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: [],
    )
    assert (
        _annotation_label_ids_for_filters(
            "project",
            [
                {
                    "column_id": "has_annotation",
                    "filter_config": {"filter_op": "equals", "filter_value": True},
                }
            ],
        )
        == ()
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "aggregations",
    [AGGREGATIONS, DATASET_AGGREGATIONS, SIMULATION_AGGREGATIONS],
)
def test_public_dashboard_operators_are_exact(aggregations):
    assert aggregations["median"].startswith("quantileExact(")
    assert aggregations["p95"].startswith("quantileExact(")
    assert aggregations["count_distinct"].startswith("uniqExact(")


@pytest.mark.unit
def test_exact_empty_payload_is_atomically_cacheable():
    cache.clear()
    payload = {
        "metric_name": "latency",
        "data": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    published = publish_exact_snapshot("test-empty", {"project": "p"}, payload)

    assert published["data"] == []
    assert published["query_cached"] is False
    assert published["query_completed_at"]


@pytest.mark.unit
@pytest.mark.parametrize("query_sampled", [None, True, "false", 0])
def test_exact_payload_requires_explicit_false_sampling_attestation(query_sampled):
    payload = {
        "data": [],
        "query_complete": True,
        "query_status": "complete",
    }
    if query_sampled is not None:
        payload["query_sampled"] = query_sampled

    assert exact_payload_is_complete(payload) is False


@pytest.mark.unit
def test_exact_payload_rejects_child_metric_without_sampling_attestation():
    payload = {
        "metrics": [
            {
                "data": [],
                "query_complete": True,
                "query_status": "complete",
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    assert exact_payload_is_complete(payload) is False


@pytest.mark.unit
def test_refresh_failure_serves_prior_exact_snapshot_without_replacing_it():
    cache.clear()
    identity = {"project": "p", "metric": "latency"}
    first = publish_exact_snapshot(
        "test-refresh",
        identity,
        {
            "metric_name": "latency",
            "data": [{"timestamp": "2026-08-01T00:00:00", "value": 4}],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )

    token = begin_exact_refresh("test-refresh", identity)
    assert token
    finish_exact_refresh(
        "test-refresh",
        identity,
        token,
        succeeded=False,
    )

    stale = read_or_schedule_exact_snapshot(
        "test-refresh",
        identity,
        refresh=False,
        pending_payload={
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
        },
    )

    assert stale["data"] == first["data"]
    assert stale["query_completed_at"] == first["query_completed_at"]
    assert stale["query_cached"] is True
    assert stale["query_refresh_failed"] is True
    assert stale["query_refreshing"] is False


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_cold_miss_is_pending_poll_dedupes_then_exact_publish_becomes_visible():
    cache.clear()
    identity = {"project": "p", "metric": "traffic"}
    pending = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        first = read_or_schedule_exact_snapshot(
            "test-cold", identity, refresh=False, pending_payload=pending
        )
        second = read_or_schedule_exact_snapshot(
            "test-cold", identity, refresh=False, pending_payload=pending
        )

    assert first["query_status"] == "pending"
    assert first["query_refreshing"] is True
    assert second["query_status"] == "pending"
    assert enqueue.call_count == 1
    task_kwargs = enqueue.call_args.kwargs["kwargs"]
    assert enqueue.call_args.kwargs["queue"] == "exact_aggregation"
    assert enqueue.call_args.kwargs["task_id"].startswith("exact-aggregation-")
    from temporalio.common import WorkflowIDConflictPolicy

    assert (
        enqueue.call_args.kwargs["id_conflict_policy"]
        == WorkflowIDConflictPolicy.USE_EXISTING
    )
    assert enqueue.call_args.kwargs["dispatch_timeout_seconds"] == 2.0

    exact = publish_exact_snapshot(
        "test-cold",
        identity,
        {
            "metric_name": "traffic",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )
    finish_exact_refresh(
        "test-cold",
        identity,
        task_kwargs["refresh_token"],
        succeeded=True,
    )
    polled = read_or_schedule_exact_snapshot(
        "test-cold", identity, refresh=False, pending_payload=pending
    )

    assert polled["query_status"] == "complete"
    assert polled["query_completed_at"] == exact["query_completed_at"]
    assert polled["query_refreshing"] is False


@pytest.mark.unit
def test_cache_only_probe_does_not_enqueue_a_true_cold_miss():
    cache.clear()
    identity = {"project": "p", "metric": "latency"}
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        result = read_or_schedule_exact_snapshot(
            "test-cache-probe",
            identity,
            refresh=False,
            pending_payload=pending,
            schedule_on_miss=False,
        )

    assert result["query_status"] == "pending"
    assert result["query_refreshing"] is False
    assert result["query_refresh_failed"] is False
    enqueue.assert_not_called()
    assert exact_refresh_state("test-cache-probe", identity) is None


@pytest.mark.unit
def test_observe_snapshot_survives_temporal_json_round_trip_and_poll_with_rows():
    """The worker's JSON identity must address the caller's original key.

    HTTP validation can leave datetime objects in graph filters, while the
    Temporal boundary carries the normalized identity as JSON strings.  A
    completed worker payload must therefore be visible to an ordinary poll
    made with the original typed request, including every graph point.
    """

    cache.clear()
    identity = {
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": [
            _time_filter(
                datetime.fromisoformat("2026-07-01T00:00:00+00:00"),
                datetime.fromisoformat("2026-08-01T00:00:00+00:00"),
            )
        ],
        "interval": "day",
        "metric_id": "latency",
        "observe_type": "trace",
    }
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        first = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=pending,
        )

    task_kwargs = enqueue.call_args.kwargs["kwargs"]
    wire_identity = json.loads(json.dumps(task_kwargs["identity"]))
    canonical_identity = normalize_exact_observe_identity(identity)
    assert wire_identity == canonical_identity
    assert snapshot_cache_key(
        "observe-system-graph", canonical_identity
    ) == snapshot_cache_key("observe-system-graph", wire_identity)
    assert first["query_status"] == "pending"

    points = [
        {
            "timestamp": "2026-07-31T00:00:00+00:00",
            "value": 1085.25,
            "primary_traffic": 14,
        }
    ]
    published = publish_exact_snapshot_for_refresh(
        "observe-system-graph",
        wire_identity,
        {
            "metric_name": "latency",
            "data": points,
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
        task_kwargs["refresh_token"],
    )
    assert published is not None

    polled = read_or_schedule_exact_snapshot(
        "observe-system-graph",
        identity,
        refresh=False,
        pending_payload=pending,
    )

    assert polled["data"] == points
    assert polled["query_complete"] is True
    assert polled["query_status"] == "complete"
    assert polled["query_sampled"] is False
    assert polled["query_cached"] is True
    assert polled["query_refreshing"] is False


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_repeated_observe_refresh_poll_keeps_worker_identity_until_publish():
    """Refresh polling must not outrun the worker's frozen cache identity."""

    cache.clear()
    identity = {
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": [],
        "interval": "day",
        "metric_id": "latency",
        "observe_type": "trace",
    }
    pending = {
        "metric_name": "latency",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    def frozen_identity(end: str) -> dict:
        return {
            **identity,
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": ["2026-07-01T00:00:00Z", end],
                    },
                }
            ],
        }

    frozen_first = frozen_identity("2026-08-01T00:00:00Z")
    frozen_next = frozen_identity("2026-08-01T00:00:01Z")
    points = [
        {
            "timestamp": "2026-07-31T00:00:00+00:00",
            "value": 1085.25,
            "primary_traffic": 14,
        }
    ]

    with (
        patch(
            "tracer.services.exact_aggregation_cache.normalize_exact_observe_identity",
            side_effect=[frozen_first, frozen_next, frozen_next],
        ),
        patch(
            "tracer.tasks.exact_aggregation."
            "refresh_exact_aggregation_snapshot.apply_async"
        ) as enqueue,
    ):
        first = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=pending,
        )
        repeated_poll = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=pending,
        )

        assert first["query_status"] == "pending"
        assert repeated_poll["query_status"] == "pending"
        assert enqueue.call_count == 1
        first_task_kwargs = enqueue.call_args_list[0].kwargs["kwargs"]
        assert first_task_kwargs["identity"] == frozen_first

        published = publish_exact_snapshot_for_refresh(
            "observe-system-graph",
            frozen_first,
            {
                "metric_name": "latency",
                "data": points,
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            },
            first_task_kwargs["refresh_token"],
        )
        assert published is not None
        finish_exact_refresh(
            "observe-system-graph",
            frozen_first,
            first_task_kwargs["refresh_token"],
            succeeded=True,
        )

        refreshing = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=pending,
        )

    assert enqueue.call_count == 2
    second_task_kwargs = enqueue.call_args_list[1].kwargs["kwargs"]
    assert second_task_kwargs["identity"] == frozen_next
    assert refreshing["data"] == points
    assert refreshing["query_status"] == "complete"
    assert refreshing["query_refreshing"] is True
    finish_exact_refresh(
        "observe-system-graph",
        frozen_next,
        second_task_kwargs["refresh_token"],
        succeeded=True,
    )


@pytest.mark.unit
def test_exact_system_graph_formats_nonempty_clickhouse_rows_without_loss():
    bucket = datetime.fromisoformat("2026-07-31T00:00:00+00:00")

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, *, timeout_ms, settings):
            assert timeout_ms > 0
            assert settings["max_result_rows"] > 0
            return SimpleNamespace(
                data=[
                    {
                        "time_bucket": bucket,
                        "avg_latency": 1085.25,
                        "total_tokens": 42,
                        "avg_cost": 0.02,
                        "traffic_count": 14,
                        "prompt_tokens": 24,
                        "completion_tokens": 18,
                        "error_rate": 0,
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

    payload = read_exact_system_graph(
        analytics=Analytics(),
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[
            _time_filter(
                datetime.fromisoformat("2026-07-30T00:00:00+00:00"),
                datetime.fromisoformat("2026-08-01T00:00:00+00:00"),
            )
        ],
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    observed = next(point for point in payload["data"] if point["value"])
    assert observed == {
        "timestamp": bucket.replace(tzinfo=None).isoformat(),
        "value": 1085.25,
        "primary_traffic": 14,
    }
    assert payload["query_rows_returned"] == 1
    assert payload["query_complete"] is True
    assert payload["query_sampled"] is False


@pytest.mark.unit
def test_dashboard_snapshot_poll_preserves_nested_metric_series_rows():
    """Snapshot decoration must not strip dashboard metric/series data."""

    cache.clear()
    identity = {
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "query_config": {
            "project_ids": ["22222222-2222-4222-8222-222222222222"],
            "granularity": "day",
            "time_range": {"preset": "30D"},
            "metrics": [
                {
                    "id": "latency",
                    "name": "latency",
                    "type": "system_metric",
                    "source": "traces",
                    "aggregation": "avg",
                }
            ],
        },
    }
    pending = {
        "metrics": [],
        "time_range": {
            "start": "2026-07-01T00:00:00+00:00",
            "end": "2026-08-01T00:00:00+00:00",
        },
        "granularity": "day",
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            "dashboard-query",
            identity,
            refresh=False,
            pending_payload=pending,
        )
    task_kwargs = enqueue.call_args.kwargs["kwargs"]
    point = {"timestamp": "2026-07-31T00:00:00+00:00", "value": 1085.25}
    metric = {
        "id": "latency",
        "name": "Latency",
        "aggregation": "avg",
        "unit": "ms",
        "series": [{"name": "total", "data": [point]}],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    published = publish_exact_snapshot_for_refresh(
        "dashboard-query",
        task_kwargs["identity"],
        {
            "metrics": [metric],
            "time_range": pending["time_range"],
            "granularity": "day",
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
        task_kwargs["refresh_token"],
    )
    assert published is not None

    polled = read_or_schedule_exact_snapshot(
        "dashboard-query",
        identity,
        refresh=False,
        pending_payload=pending,
    )

    assert polled["metrics"] == [metric]
    assert polled["metrics"][0]["series"][0]["data"] == [point]
    assert polled["query_complete"] is True
    assert polled["query_cached"] is True


@pytest.mark.unit
def test_eval_usage_chart_and_logs_pages_publish_and_poll_independently():
    """A chart probe must never satisfy a differently-sized logs page.

    Eval Usage returns aggregates and one requested table page in the same
    envelope.  ``page`` and ``page_size`` are consequently stable semantic
    identity fields, not incidental polling fields.
    """

    cache.clear()
    common = {
        "organization_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "template_id": "44444444-4444-4444-8444-444444444444",
        "period": "30d",
        "start_date": None,
        "end_date": None,
    }
    chart_identity = {**common, "page": 0, "page_size": 1}
    logs_identity = {**common, "page": 0, "page_size": 25}

    def pending(identity):
        return {
            "template_id": identity["template_id"],
            "is_composite": False,
            "completeness": "pending",
            "unavailable_fields": [],
            "stats": {
                "total_runs": 0,
                "runs_period": 0,
                "success_count": 0,
                "error_count": 0,
                "pass_rate": 0.0,
            },
            "chart": [],
            "table": [],
            "logs": {
                "total": 0,
                "page": identity["page"],
                "page_size": identity["page_size"],
            },
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
        }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            "eval-usage",
            chart_identity,
            refresh=False,
            pending_payload=pending(chart_identity),
        )
        read_or_schedule_exact_snapshot(
            "eval-usage",
            logs_identity,
            refresh=False,
            pending_payload=pending(logs_identity),
        )

    assert enqueue.call_count == 2
    chart_task = enqueue.call_args_list[0].kwargs["kwargs"]
    logs_task = enqueue.call_args_list[1].kwargs["kwargs"]
    assert snapshot_cache_key(
        "eval-usage", chart_task["identity"]
    ) != snapshot_cache_key("eval-usage", logs_task["identity"])

    def complete(identity, table):
        return {
            **pending(identity),
            "completeness": "complete",
            "stats": {
                "total_runs": 24,
                "runs_period": 24,
                "success_count": 24,
                "error_count": 0,
                "pass_rate": 100.0,
            },
            "chart": [{"timestamp": "2026-07-31T00:00:00+00:00", "calls": 24}],
            "table": table,
            "logs": {
                "total": 24,
                "page": identity["page"],
                "page_size": identity["page_size"],
            },
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    chart_row = {"row_id": "chart-probe-row"}
    log_rows = [{"row_id": f"log-row-{index}"} for index in range(24)]
    assert publish_exact_snapshot_for_refresh(
        "eval-usage",
        chart_task["identity"],
        complete(chart_identity, [chart_row]),
        chart_task["refresh_token"],
    )
    assert publish_exact_snapshot_for_refresh(
        "eval-usage",
        logs_task["identity"],
        complete(logs_identity, log_rows),
        logs_task["refresh_token"],
    )

    chart_poll = read_or_schedule_exact_snapshot(
        "eval-usage",
        chart_identity,
        refresh=False,
        pending_payload=pending(chart_identity),
    )
    logs_poll = read_or_schedule_exact_snapshot(
        "eval-usage",
        logs_identity,
        refresh=False,
        pending_payload=pending(logs_identity),
    )

    assert chart_poll["table"] == [chart_row]
    assert chart_poll["logs"] == {"total": 24, "page": 0, "page_size": 1}
    assert logs_poll["table"] == log_rows
    assert logs_poll["logs"] == {"total": 24, "page": 0, "page_size": 25}
    assert chart_poll["stats"] == logs_poll["stats"]
    assert chart_poll["chart"] == logs_poll["chart"]


@pytest.mark.unit
def test_concurrent_cold_requests_enqueue_only_one_refresh():
    cache.clear()
    identity = {"project": "p", "metric": "cost"}
    pending = {
        "metric_name": "cost",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _index: read_or_schedule_exact_snapshot(
                        "test-concurrent",
                        identity,
                        refresh=False,
                        pending_payload=pending,
                    ),
                    range(16),
                )
            )

    assert enqueue.call_count == 1
    assert all(result["query_status"] == "pending" for result in results)
    assert all(result["query_refreshing"] is True for result in results)


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_distinct_exact_identities_enqueue_independently_on_admitted_queue():
    """The worker queue, rather than identity coalescing, serializes unique reads."""

    cache.clear()
    pending = {
        "metric_name": "cost",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    identities = [
        {"project": "p", "metric": "cost"},
        {"project": "p", "metric": "latency"},
    ]

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        for identity in identities:
            read_or_schedule_exact_snapshot(
                "test-distinct",
                identity,
                refresh=False,
                pending_payload=pending,
            )

    assert enqueue.call_count == 2
    assert {call.kwargs["queue"] for call in enqueue.call_args_list} == {
        "exact_aggregation"
    }
    assert len({call.kwargs["task_id"] for call in enqueue.call_args_list}) == len(
        identities
    )


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="tasks_xl")
def test_exact_refresh_defaults_to_existing_xl_queue_without_dedicated_worker():
    cache.clear()
    pending = {
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            "test-default-queue",
            {"project": "p", "metric": "cost"},
            refresh=False,
            pending_payload=pending,
        )

    assert enqueue.call_args.kwargs["queue"] == "tasks_xl"


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="typo_unpolled_queue")
def test_invalid_exact_refresh_queue_fails_closed_without_claim_or_dispatch():
    from tracer.services import exact_aggregation_cache as cache_module

    cache.clear()
    identity = {"project": "p", "metric": "cost"}
    pending = {
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        result = read_or_schedule_exact_snapshot(
            "test-invalid-queue",
            identity,
            refresh=True,
            pending_payload=pending,
        )

    enqueue.assert_not_called()
    assert result["query_refresh_failed"] is True
    assert result["query_refreshing"] is False
    assert (
        cache.get(cache_module._refresh_lock_key("test-invalid-queue", identity))
        is None
    )


@pytest.mark.unit
def test_partial_fallback_claim_write_releases_only_its_own_lock(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class StateWriteFailsCache:
        def __init__(self):
            self.values = {}

        def add(self, key, value, *, timeout):
            del timeout
            if key in self.values:
                return False
            self.values[key] = value
            return True

        def set(self, key, value, *, timeout):
            del value, timeout
            if key.endswith(":refresh-state"):
                raise RuntimeError("state cache unavailable")

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            self.values.pop(key, None)

    failing_cache = StateWriteFailsCache()
    monkeypatch.setattr(cache_module, "cache", failing_cache)
    identity = {"project": "p", "metric": "latency"}

    assert cache_module.begin_exact_refresh("partial-claim", identity) is None
    assert failing_cache.values == {}


@pytest.mark.unit
def test_partial_fallback_claim_cleanup_never_deletes_replacement_owner(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class ReplacementWinsCache:
        def __init__(self):
            self.values = {}
            self.deleted = []
            self.lock_key = None

        def add(self, key, value, *, timeout):
            del timeout
            self.lock_key = key
            self.values[key] = value
            return True

        def set(self, key, value, *, timeout):
            del key, value, timeout
            # Model the original lease expiring while the state backend fails,
            # followed by a different process claiming the same identity.
            self.values[self.lock_key] = "replacement-token"
            raise RuntimeError("state cache unavailable")

        def get(self, key):
            return self.values.get(key)

        def delete(self, key):
            self.deleted.append(key)
            self.values.pop(key, None)

    racing_cache = ReplacementWinsCache()
    monkeypatch.setattr(cache_module, "cache", racing_cache)

    assert (
        cache_module.begin_exact_refresh(
            "replacement-race",
            {"project": "p", "metric": "traffic"},
        )
        is None
    )
    assert racing_cache.values[racing_cache.lock_key] == "replacement-token"
    assert racing_cache.deleted == []


@pytest.mark.unit
def test_redis_refresh_claim_creates_lock_and_state_in_one_lua_operation(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    captured = {}

    class RawClient:
        def eval(self, *args):
            captured["args"] = args
            return 1

    class RedisAdapter:
        def get_client(self, *, write):
            assert write is True
            return RawClient()

        def make_key(self, key):
            return f"redis:{key}"

        def encode(self, value):
            return value

    monkeypatch.setattr(
        cache_module,
        "_redis_cache_client",
        lambda: RedisAdapter(),
    )

    token = cache_module.begin_exact_refresh(
        "redis-atomic-claim",
        {"project": "p", "metric": "traffic"},
    )

    assert token
    script, key_count, lock_key, state_key, encoded_token, state, ttl_ms = captured[
        "args"
    ]
    assert script == cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT
    assert key_count == 2
    assert lock_key.endswith(":refresh-lock")
    assert state_key.endswith(":refresh-state")
    assert encoded_token == token
    assert state == {"status": "running", "token": token, "phase": "dispatch"}
    assert ttl_ms == cache_module._refresh_dispatch_seconds() * 1000


@pytest.mark.unit
def test_redis_refresh_claim_recovers_after_post_success_response_failure(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class RawClient:
        def __init__(self):
            self.values = {}
            self.claim_calls = 0

        def eval(self, script, key_count, *parts):
            assert script == cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT
            assert key_count == 2
            lock_key, state_key, token, state, _ttl_ms = parts
            self.values[lock_key] = token
            self.values[state_key] = state
            self.claim_calls += 1
            raise ConnectionError("response lost after Redis committed")

        def mget(self, *keys):
            return [self.values.get(key) for key in keys]

    class RedisAdapter:
        def __init__(self):
            self.raw = RawClient()

        def get_client(self, *, write):
            assert write is True
            return self.raw

        @staticmethod
        def make_key(key):
            return f"redis:{key}"

        @staticmethod
        def encode(value):
            return value

    adapter = RedisAdapter()
    monkeypatch.setattr(cache_module, "_redis_cache_client", lambda: adapter)

    token = cache_module.begin_exact_refresh(
        "redis-post-success",
        {"project": "p", "metric": "traffic"},
    )

    assert token
    assert adapter.raw.claim_calls == 1
    assert sorted(adapter.raw.values.values(), key=str) == sorted(
        [
            token,
            {"status": "running", "token": token, "phase": "dispatch"},
        ],
        key=str,
    )


@pytest.mark.unit
def test_redis_refresh_claim_fenced_cleanup_removes_partial_ambiguous_write(
    monkeypatch,
):
    from tracer.services import exact_aggregation_cache as cache_module

    class RawClient:
        def __init__(self):
            self.values = {}
            self.scripts = []

        def eval(self, script, key_count, *parts):
            self.scripts.append(script)
            assert key_count == 2
            lock_key, state_key = parts[:2]
            if script == cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT:
                token, _state, _ttl_ms = parts[2:]
                self.values[lock_key] = token
                raise ConnectionError("state write failed after lock write")
            assert script == cache_module._REDIS_FENCED_ROLLBACK_REFRESH_CLAIM_SCRIPT
            token, state = parts[2:]
            if self.values.get(state_key) == state:
                self.values.pop(state_key, None)
            if self.values.get(lock_key) == token:
                self.values.pop(lock_key, None)
            return 1

        def mget(self, *keys):
            return [self.values.get(key) for key in keys]

    class RedisAdapter:
        def __init__(self):
            self.raw = RawClient()

        def get_client(self, *, write):
            assert write is True
            return self.raw

        @staticmethod
        def make_key(key):
            return f"redis:{key}"

        @staticmethod
        def encode(value):
            return value

    adapter = RedisAdapter()
    monkeypatch.setattr(cache_module, "_redis_cache_client", lambda: adapter)

    token = cache_module.begin_exact_refresh(
        "redis-partial-ambiguous",
        {"project": "p", "metric": "traffic"},
    )

    assert token is None
    assert adapter.raw.values == {}
    assert adapter.raw.scripts == [
        cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT,
        cache_module._REDIS_FENCED_ROLLBACK_REFRESH_CLAIM_SCRIPT,
    ]


@pytest.mark.unit
def test_redis_refresh_claim_fenced_cleanup_preserves_replacement_owner(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    replacement_token = "replacement-token"

    class RawClient:
        def __init__(self):
            self.values = {}

        def eval(self, script, key_count, *parts):
            assert key_count == 2
            lock_key, state_key = parts[:2]
            if script == cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT:
                token, _state, _ttl_ms = parts[2:]
                self.values[lock_key] = token
                raise ConnectionError("state write failed after lock write")
            assert script == cache_module._REDIS_FENCED_ROLLBACK_REFRESH_CLAIM_SCRIPT
            token, state = parts[2:]
            if self.values.get(state_key) == state:
                self.values.pop(state_key, None)
            if self.values.get(lock_key) == token:
                self.values.pop(lock_key, None)
            return 0

        def mget(self, *keys):
            observed = [self.values.get(key) for key in keys]
            # The old lease expires and a replacement wins after readback but
            # before rollback. The Lua value fence must preserve its lock.
            self.values[keys[0]] = replacement_token
            return observed

    class RedisAdapter:
        def __init__(self):
            self.raw = RawClient()

        def get_client(self, *, write):
            assert write is True
            return self.raw

        @staticmethod
        def make_key(key):
            return f"redis:{key}"

        @staticmethod
        def encode(value):
            return value

    adapter = RedisAdapter()
    monkeypatch.setattr(cache_module, "_redis_cache_client", lambda: adapter)

    token = cache_module.begin_exact_refresh(
        "redis-replacement-race",
        {"project": "p", "metric": "traffic"},
    )

    assert token is None
    assert list(adapter.raw.values.values()) == [replacement_token]


@pytest.mark.unit
def test_redis_refresh_claim_readback_outage_leaves_bounded_claim(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class RawClient:
        def __init__(self):
            self.values = {}
            self.ttl_ms = None

        def eval(self, script, key_count, *parts):
            assert script == cache_module._REDIS_ATOMIC_REFRESH_CLAIM_SCRIPT
            assert key_count == 2
            lock_key, state_key, token, state, self.ttl_ms = parts
            self.values[lock_key] = token
            self.values[state_key] = state
            raise ConnectionError("response lost after Redis committed")

        def mget(self, *_keys):
            raise ConnectionError("Redis still unavailable")

    class RedisAdapter:
        def __init__(self):
            self.raw = RawClient()

        def get_client(self, *, write):
            assert write is True
            return self.raw

        @staticmethod
        def make_key(key):
            return f"redis:{key}"

        @staticmethod
        def encode(value):
            return value

    adapter = RedisAdapter()
    monkeypatch.setattr(cache_module, "_redis_cache_client", lambda: adapter)

    token = cache_module.begin_exact_refresh(
        "redis-readback-outage",
        {"project": "p", "metric": "traffic"},
    )

    assert token is None
    assert len(adapter.raw.values) == 2
    assert adapter.raw.ttl_ms == cache_module._refresh_dispatch_seconds() * 1000


@pytest.mark.unit
def test_refresh_claim_uses_queue_dispatch_lease_then_activity_promotes_it(
    monkeypatch,
):
    """Only an activity that actually starts may hold the running lease."""

    from tracer.services import exact_aggregation_cache as cache_module

    class RecordingCache:
        def __init__(self):
            self.values = {}
            self.timeouts = []

        def add(self, key, value, *, timeout):
            if key in self.values:
                return False
            self.values[key] = value
            self.timeouts.append(("add", key, timeout))
            return True

        def set(self, key, value, *, timeout):
            self.values[key] = value
            self.timeouts.append(("set", key, timeout))

        def get(self, key):
            return self.values.get(key)

    recording_cache = RecordingCache()
    monkeypatch.setattr(cache_module, "cache", recording_cache)
    dispatch_seconds = 13 * 60 * 60
    running_seconds = EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS + 5 * 60
    monkeypatch.setattr(
        cache_module,
        "_refresh_dispatch_seconds",
        lambda: dispatch_seconds,
    )
    monkeypatch.setattr(
        cache_module,
        "_refresh_lock_seconds",
        lambda: running_seconds,
    )
    identity = {"project": "p", "metric": "latency"}

    token = cache_module.begin_exact_refresh("observe-lease-test", identity)

    assert token
    assert [timeout for _op, _key, timeout in recording_cache.timeouts] == [
        dispatch_seconds,
        dispatch_seconds,
    ]
    assert cache_module.activate_exact_refresh("observe-lease-test", identity, token)
    assert [timeout for _op, _key, timeout in recording_cache.timeouts[-2:]] == [
        running_seconds,
        running_seconds,
    ]


@pytest.mark.unit
def test_exact_refresh_lease_settings_cannot_undercut_temporal_timeouts(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    monkeypatch.setattr(
        cache_module.settings,
        "EXACT_AGGREGATION_REFRESH_DISPATCH_SECONDS",
        10 * 60,
        raising=False,
    )
    monkeypatch.setattr(
        cache_module.settings,
        "EXACT_AGGREGATION_REFRESH_LOCK_SECONDS",
        EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS,
        raising=False,
    )

    assert (
        cache_module._refresh_dispatch_seconds()
        > EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS
    )
    assert (
        cache_module._refresh_lock_seconds()
        > EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS
    )


@pytest.mark.unit
def test_dispatch_lease_survives_queue_delay_then_promotes_to_running(monkeypatch):
    """A healthy exact queue backlog must not expire the pre-start claim."""

    from tracer.services import exact_aggregation_cache as cache_module

    class ExpiringCache:
        def __init__(self):
            self.now = 0
            self.values = {}

        def _live_value(self, key):
            stored = self.values.get(key)
            if stored is None:
                return None
            value, expires_at = stored
            if expires_at is not None and expires_at <= self.now:
                self.values.pop(key, None)
                return None
            return value

        def add(self, key, value, *, timeout):
            if self._live_value(key) is not None:
                return False
            self.set(key, value, timeout=timeout)
            return True

        def set(self, key, value, *, timeout):
            expires_at = None if timeout is None else self.now + timeout
            self.values[key] = (value, expires_at)

        def get(self, key):
            return self._live_value(key)

        def delete(self, key):
            self.values.pop(key, None)

        def advance(self, seconds):
            self.now += seconds

    expiring_cache = ExpiringCache()
    monkeypatch.setattr(cache_module, "cache", expiring_cache)
    dispatch_seconds = 13 * 60 * 60
    running_seconds = EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS + 5 * 60
    monkeypatch.setattr(
        cache_module,
        "_refresh_dispatch_seconds",
        lambda: dispatch_seconds,
    )
    monkeypatch.setattr(
        cache_module,
        "_refresh_lock_seconds",
        lambda: running_seconds,
    )
    namespace = "observe-delayed-start"
    identity = {"project": "p", "metric": "latency"}

    token = cache_module.begin_exact_refresh(namespace, identity)
    assert token
    assert cache_module.record_exact_refresh_dispatch(
        namespace,
        identity,
        token,
        "task-exact-delayed",
    )

    # More than ten minutes, and nearly the full Temporal schedule-to-start
    # window, remains a valid queued claim.
    expiring_cache.advance(11 * 60 * 60)
    assert cache_module.activate_exact_refresh(namespace, identity, token)

    # Promotion owns an independent lease longer than the activity timeout.
    expiring_cache.advance(EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS)
    assert cache_module.refresh_claim_is_current(namespace, identity, token)


@pytest.mark.unit
def test_expired_dispatch_replacement_fences_the_delayed_old_activity(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class ExpiringCache:
        def __init__(self):
            self.now = 0
            self.values = {}

        def _live_value(self, key):
            stored = self.values.get(key)
            if stored is None:
                return None
            value, expires_at = stored
            if expires_at <= self.now:
                self.values.pop(key, None)
                return None
            return value

        def add(self, key, value, *, timeout):
            if self._live_value(key) is not None:
                return False
            self.set(key, value, timeout=timeout)
            return True

        def set(self, key, value, *, timeout):
            self.values[key] = (value, self.now + timeout)

        def get(self, key):
            return self._live_value(key)

        def delete(self, key):
            self.values.pop(key, None)

        def advance(self, seconds):
            self.now += seconds

    expiring_cache = ExpiringCache()
    monkeypatch.setattr(cache_module, "cache", expiring_cache)
    monkeypatch.setattr(cache_module, "_refresh_dispatch_seconds", lambda: 600)
    monkeypatch.setattr(cache_module, "_refresh_lock_seconds", lambda: 3600)
    namespace = "observe-expired-fence"
    identity = {"project": "p", "metric": "traffic"}

    old_token = cache_module.begin_exact_refresh(namespace, identity)
    assert old_token
    expiring_cache.advance(601)
    new_token = cache_module.begin_exact_refresh(namespace, identity)

    assert new_token and new_token != old_token
    assert cache_module.activate_exact_refresh(namespace, identity, old_token) is False
    assert cache_module.activate_exact_refresh(namespace, identity, new_token) is True
    assert cache_module.refresh_claim_is_current(namespace, identity, new_token)


@pytest.mark.unit
def test_running_lease_margin_prevents_timeout_overlap_and_fences_old_token(
    monkeypatch,
):
    """No replacement is admitted while a timed-out sync query may unwind."""

    from tracer.services import exact_aggregation_cache as cache_module

    class ExpiringCache:
        def __init__(self):
            self.now = 0
            self.values = {}

        def _live_value(self, key):
            stored = self.values.get(key)
            if stored is None:
                return None
            value, expires_at = stored
            if expires_at is not None and expires_at <= self.now:
                self.values.pop(key, None)
                return None
            return value

        def add(self, key, value, *, timeout):
            if self._live_value(key) is not None:
                return False
            self.set(key, value, timeout=timeout)
            return True

        def set(self, key, value, *, timeout):
            self.values[key] = (value, self.now + timeout)

        def get(self, key):
            return self._live_value(key)

        def delete(self, key):
            self.values.pop(key, None)

        def advance(self, seconds):
            self.now += seconds

    expiring_cache = ExpiringCache()
    running_seconds = EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS + 5 * 60
    monkeypatch.setattr(cache_module, "cache", expiring_cache)
    monkeypatch.setattr(
        cache_module,
        "_refresh_dispatch_seconds",
        lambda: 13 * 60 * 60,
    )
    monkeypatch.setattr(
        cache_module,
        "_refresh_lock_seconds",
        lambda: running_seconds,
    )
    namespace = "observe-running-timeout-fence"
    identity = {"project": "p", "metric": "traffic"}

    old_token = cache_module.begin_exact_refresh(namespace, identity)
    assert old_token
    assert cache_module.activate_exact_refresh(namespace, identity, old_token)

    expiring_cache.advance(EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS)
    assert cache_module.refresh_claim_is_current(namespace, identity, old_token)
    assert cache_module.begin_exact_refresh(namespace, identity) is None

    expiring_cache.advance(5 * 60 + 1)
    new_token = cache_module.begin_exact_refresh(namespace, identity)
    assert new_token and new_token != old_token
    assert cache_module.activate_exact_refresh(namespace, identity, old_token) is False
    cache_module.finish_exact_refresh(
        namespace,
        identity,
        old_token,
        succeeded=False,
    )
    assert cache_module.refresh_claim_is_current(namespace, identity, new_token)


@pytest.mark.unit
def test_running_lease_keepalive_covers_the_actual_sync_query_lifetime(monkeypatch):
    """A timed-out wrapper cannot outlive the lease protecting its CH thread."""

    from tracer.tasks import exact_aggregation as task_module

    renewals = []

    class StopAfterTwoRenewals:
        def wait(self, timeout):
            assert timeout == task_module.EXACT_AGGREGATION_LEASE_RENEW_INTERVAL_SECONDS
            return len(renewals) >= 2

    monkeypatch.setattr(
        task_module,
        "activate_exact_refresh",
        lambda namespace, identity, token: (
            renewals.append((namespace, identity, token)) or True
        ),
    )

    task_module._renew_exact_refresh_lease_until_stopped(
        namespace="observe-sync-overrun",
        identity={"project": "p", "metric": "traffic"},
        refresh_token="current-token",
        stop_event=StopAfterTwoRenewals(),
    )

    assert renewals == [
        (
            "observe-sync-overrun",
            {"project": "p", "metric": "traffic"},
            "current-token",
        ),
        (
            "observe-sync-overrun",
            {"project": "p", "metric": "traffic"},
            "current-token",
        ),
    ]


@pytest.mark.unit
@pytest.mark.django_db
def test_keeper_start_failure_still_releases_the_fenced_refresh_claim(monkeypatch):
    from tracer.tasks import exact_aggregation as task_module

    finished = []

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

        def join(self, *, timeout):
            pytest.fail(f"an unstarted keeper must not be joined ({timeout=})")

    monkeypatch.setattr(task_module, "Thread", ThreadThatCannotStart)
    monkeypatch.setattr(task_module, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(task_module, "activate_exact_refresh", lambda *_args: True)
    monkeypatch.setattr(
        task_module,
        "finish_exact_refresh",
        lambda namespace, identity, token, *, succeeded: finished.append(
            (namespace, identity, token, succeeded)
        ),
    )

    with pytest.raises(RuntimeError, match="exact aggregation refresh failed"):
        task_module.refresh_exact_aggregation_snapshot.run_sync(
            namespace="observe-keeper-start-failure",
            identity={"project": "p", "metric": "traffic"},
            refresh_token="current-token",
        )

    assert finished == [
        (
            "observe-keeper-start-failure",
            {"project": "p", "metric": "traffic"},
            "current-token",
            False,
        )
    ]


@pytest.mark.unit
def test_terminal_dispatch_is_replaced_immediately_from_temporal_evidence():
    """An incompatible worker failure need not wait for lease expiry."""

    from tracer.tasks.exact_aggregation import refresh_exact_aggregation_snapshot

    cache.clear()
    namespace = "observe-terminal-dispatch"
    identity = {"project": "p", "metric": "traffic"}
    pending = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    with (
        patch.object(
            refresh_exact_aggregation_snapshot,
            "apply_async",
            side_effect=[
                SimpleNamespace(id="task-exact-old"),
                SimpleNamespace(id="task-exact-new"),
            ],
        ) as enqueue,
        patch(
            "tfc.temporal.common.client.get_workflow_status_sync",
            return_value={"status": "3", "status_name": "FAILED"},
        ) as workflow_status,
    ):
        first = read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        first_token = enqueue.call_args.kwargs["kwargs"]["refresh_token"]
        second = read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        second_token = enqueue.call_args.kwargs["kwargs"]["refresh_token"]

    assert first["query_refreshing"] is True
    assert second["query_refreshing"] is True
    assert enqueue.call_count == 2
    assert first_token != second_token
    workflow_status.assert_called_once_with(
        "task-exact-old",
        timeout_seconds=0.5,
    )
    assert refresh_claim_is_current(namespace, identity, first_token) is False
    assert refresh_claim_is_current(namespace, identity, second_token) is True


@pytest.mark.unit
def test_running_dispatch_is_not_replaced_by_poll_reconciliation():
    from tracer.tasks.exact_aggregation import refresh_exact_aggregation_snapshot

    cache.clear()
    namespace = "observe-running-dispatch"
    identity = {"project": "p", "metric": "traffic"}
    pending = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    with (
        patch.object(
            refresh_exact_aggregation_snapshot,
            "apply_async",
            return_value=SimpleNamespace(id="task-exact-running"),
        ) as enqueue,
        patch(
            "tfc.temporal.common.client.get_workflow_status_sync",
            return_value={"status": "1", "status_name": "RUNNING"},
        ) as workflow_status,
    ):
        read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        token = enqueue.call_args.kwargs["kwargs"]["refresh_token"]
        polled = read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )

    assert enqueue.call_count == 1
    workflow_status.assert_called_once_with(
        "task-exact-running",
        timeout_seconds=0.5,
    )
    assert polled["query_refreshing"] is True
    assert refresh_claim_is_current(namespace, identity, token) is True


@pytest.mark.unit
def test_terminal_status_racing_with_activity_promotion_cannot_clear_running_claim():
    from tracer.services import exact_aggregation_cache as cache_module
    from tracer.tasks.exact_aggregation import refresh_exact_aggregation_snapshot

    cache.clear()
    namespace = "observe-promotion-race"
    identity = {"project": "p", "metric": "traffic"}
    pending = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    with patch.object(
        refresh_exact_aggregation_snapshot,
        "apply_async",
        return_value=SimpleNamespace(id="task-exact-race"),
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        token = enqueue.call_args.kwargs["kwargs"]["refresh_token"]

        def promote_then_report_terminal(*_args, **_kwargs):
            assert cache_module.activate_exact_refresh(namespace, identity, token)
            return {"status": "2", "status_name": "COMPLETED"}

        with patch(
            "tfc.temporal.common.client.get_workflow_status_sync",
            side_effect=promote_then_report_terminal,
        ):
            polled = read_or_schedule_exact_snapshot(
                namespace,
                identity,
                refresh=False,
                pending_payload=pending,
            )

    assert enqueue.call_count == 1
    assert polled["query_refreshing"] is True
    assert refresh_claim_is_current(namespace, identity, token) is True


@pytest.mark.unit
@pytest.mark.django_db
def test_expired_unstarted_dispatch_is_reclaimed_by_an_ordinary_poll(monkeypatch):
    """A terminal Temporal pre-activity failure is reclaimed without TTL wait."""

    from tracer.services import exact_aggregation_cache as cache_module
    from tracer.tasks import exact_aggregation as task_module

    cache.clear()
    namespace = "observe-unstarted-reclaim"
    identity = {"project": "p", "metric": "traffic"}
    pending = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        first = read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        first_task = enqueue.call_args

        # Model the dispatch lease expiring because a mixed-version
        # worker rejected the unknown activity before this function ran.
        cache.delete(cache_module._refresh_lock_key(namespace, identity))
        cache.delete(cache_module._refresh_state_key(namespace, identity))

        second = read_or_schedule_exact_snapshot(
            namespace,
            identity,
            refresh=False,
            pending_payload=pending,
        )
        second_task = enqueue.call_args

    first_token = first_task.kwargs["kwargs"]["refresh_token"]
    second_token = second_task.kwargs["kwargs"]["refresh_token"]
    assert enqueue.call_count == 2
    assert first_token != second_token
    assert first_task.kwargs["task_id"] != second_task.kwargs["task_id"]
    assert first["query_refreshing"] is True
    assert second["query_refreshing"] is True

    monkeypatch.setattr(
        task_module,
        "_load_exact_payload",
        lambda *_args: pytest.fail("expired activity must not query ClickHouse"),
    )
    task_module.refresh_exact_aggregation_snapshot.run_sync(
        namespace=namespace,
        identity=identity,
        refresh_token=first_token,
    )
    assert refresh_claim_is_current(namespace, identity, second_token) is True


@pytest.mark.unit
def test_cold_miss_without_a_persisted_claim_fails_closed_instead_of_spinning(
    monkeypatch,
):
    cache.clear()
    pending = {
        "metric_name": "cost",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }
    monkeypatch.setattr(
        "tracer.services.exact_aggregation_cache.begin_exact_refresh",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "tracer.services.exact_aggregation_cache.exact_refresh_state",
        lambda *_args: None,
    )

    result = read_or_schedule_exact_snapshot(
        "test-unavailable-cache",
        {"project": "p", "metric": "cost"},
        refresh=False,
        pending_payload=pending,
    )

    assert result["query_refresh_failed"] is True
    assert result["query_refreshing"] is False


@pytest.mark.unit
def test_cold_miss_enqueue_failure_releases_claim_and_fails_closed():
    cache.clear()
    identity = {"project": "p", "metric": "cost"}
    pending = {
        "metric_name": "cost",
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async",
        side_effect=TimeoutError("Temporal unavailable"),
    ):
        result = read_or_schedule_exact_snapshot(
            "test-enqueue-failure",
            identity,
            refresh=False,
            pending_payload=pending,
        )

    assert result["query_refresh_failed"] is True
    assert result["query_refreshing"] is False
    assert exact_refresh_state("test-enqueue-failure", identity) == "failed"


@pytest.mark.unit
@pytest.mark.django_db
def test_background_worker_publishes_only_after_complete_loader(monkeypatch):
    from tracer.tasks import exact_aggregation as task_module

    cache.clear()
    identity = {"project": "p", "metric": "tokens"}
    token = begin_exact_refresh("observe-test-worker", identity)
    assert token
    monkeypatch.setattr(
        task_module,
        "_load_exact_payload",
        lambda *_args: {
            "metric_name": "tokens",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )

    task_module.refresh_exact_aggregation_snapshot.run_sync(
        namespace="observe-test-worker",
        identity=identity,
        refresh_token=token,
    )

    polled = read_or_schedule_exact_snapshot(
        "observe-test-worker",
        identity,
        refresh=False,
        pending_payload={},
    )
    assert polled["query_status"] == "complete"
    assert exact_refresh_state("observe-test-worker", identity) is None


@pytest.mark.unit
@pytest.mark.django_db
def test_background_worker_failure_leaves_cache_unpublished_and_retryable(monkeypatch):
    from tracer.tasks import exact_aggregation as task_module

    cache.clear()
    identity = {"project": "p", "metric": "errors"}
    token = begin_exact_refresh("observe-test-worker-failure", identity)
    assert token

    def fail(*_args):
        raise RuntimeError("private query detail")

    monkeypatch.setattr(task_module, "_load_exact_payload", fail)
    with pytest.raises(RuntimeError, match="exact aggregation refresh failed"):
        task_module.refresh_exact_aggregation_snapshot.run_sync(
            namespace="observe-test-worker-failure",
            identity=identity,
            refresh_token=token,
        )

    assert exact_refresh_state("observe-test-worker-failure", identity) == "failed"
    failed = read_or_schedule_exact_snapshot(
        "observe-test-worker-failure",
        identity,
        refresh=False,
        pending_payload={
            "metric_name": "errors",
            "data": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
        },
    )
    assert failed["query_refresh_failed"] is True
    assert failed["query_refreshing"] is False


@pytest.mark.unit
def test_exact_refresh_has_a_dedicated_minimal_temporal_queue():
    from tfc.temporal.common.registry import (
        TEMPORAL_ACTIVITY_MODULES,
        get_activities_for_queue,
        get_workflows_for_queue,
    )
    from tfc.temporal.drop_in.decorator import (
        _ACTIVITY_REGISTRY,
        _ACTIVITY_WRAPPERS,
    )
    from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow
    from tracer.services import exact_aggregation_cache as cache_module
    from tracer.tasks.exact_aggregation import (
        EXACT_AGGREGATION_TASK_QUEUE,
        refresh_exact_aggregation_snapshot,
    )

    metadata = _ACTIVITY_REGISTRY[refresh_exact_aggregation_snapshot.name]
    assert metadata["queue"] == EXACT_AGGREGATION_TASK_QUEUE
    assert metadata["time_limit"] == EXACT_AGGREGATION_ACTIVITY_TIMEOUT_SECONDS
    assert metadata["max_retries"] == 0
    assert (
        metadata["schedule_to_start_timeout"]
        == EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS
    )
    assert (
        metadata["workflow_run_timeout"]
        == EXACT_AGGREGATION_WORKFLOW_RUN_TIMEOUT_SECONDS
    )
    assert (
        metadata["workflow_execution_timeout"]
        == EXACT_AGGREGATION_WORKFLOW_EXECUTION_TIMEOUT_SECONDS
    )
    assert metadata["workflow_run_timeout"] > (
        metadata["schedule_to_start_timeout"] + metadata["time_limit"]
    )
    assert "tracer.tasks" in TEMPORAL_ACTIVITY_MODULES
    assert get_workflows_for_queue(EXACT_AGGREGATION_TASK_QUEUE) == [TaskRunnerWorkflow]
    assert get_activities_for_queue(EXACT_AGGREGATION_TASK_QUEUE) == [
        _ACTIVITY_WRAPPERS[refresh_exact_aggregation_snapshot.name]
    ]
    # Keep the activity registered on the former generic queue during rollout
    # so already-scheduled workflows can finish on an old queue worker. New
    # dispatches are asserted above to target only the admitted queue.
    assert _ACTIVITY_WRAPPERS[
        refresh_exact_aggregation_snapshot.name
    ] in get_activities_for_queue("tasks_xl")
    for generic_queue in (
        "default",
        "tasks_s",
        "tasks_l",
        "agent_compass",
        "trace_ingestion",
    ):
        assert _ACTIVITY_WRAPPERS[
            refresh_exact_aggregation_snapshot.name
        ] not in get_activities_for_queue(generic_queue)
    assert (
        cache_module._refresh_dispatch_seconds()
        > EXACT_AGGREGATION_SCHEDULE_TO_START_TIMEOUT_SECONDS
    )
    assert cache_module._refresh_lock_seconds() > metadata["time_limit"]


@pytest.mark.unit
def test_exact_refresh_workflow_id_is_deterministic_and_opaque_per_claim():
    token = "do-not-expose-this-refresh-token"

    first = _exact_refresh_workflow_task_id(token)
    second = _exact_refresh_workflow_task_id(token)

    assert first == second
    assert first.startswith("exact-aggregation-")
    assert token not in first
    assert first != _exact_refresh_workflow_task_id(f"{token}-next")


@pytest.mark.unit
@pytest.mark.django_db
def test_redelivered_exact_refresh_cannot_publish_after_claim_finished(monkeypatch):
    from tracer.tasks import exact_aggregation as task_module

    cache.clear()
    identity = {"project": "p", "metric": "errors"}
    token = begin_exact_refresh("observe-test-redelivery", identity)
    assert token
    finish_exact_refresh(
        "observe-test-redelivery",
        identity,
        token,
        succeeded=True,
    )
    monkeypatch.setattr(
        task_module,
        "_load_exact_payload",
        lambda *_args: pytest.fail("a stale activity must not query ClickHouse"),
    )

    task_module.refresh_exact_aggregation_snapshot.run_sync(
        namespace="observe-test-redelivery",
        identity=identity,
        refresh_token=token,
    )


@pytest.mark.unit
def test_old_worker_cannot_publish_or_clear_a_newer_refresh_claim():
    cache.clear()
    namespace = "observe-test-token-fence"
    identity = {"project": "p", "metric": "latency"}
    old_token = begin_exact_refresh(namespace, identity)
    assert old_token
    finish_exact_refresh(namespace, identity, old_token, succeeded=False)
    new_token = begin_exact_refresh(namespace, identity)
    assert new_token and new_token != old_token
    payload = {
        "metric_name": "latency",
        "data": [{"timestamp": "2026-08-01T00:00:00", "value": 9}],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    assert (
        publish_exact_snapshot_for_refresh(
            namespace,
            identity,
            payload,
            old_token,
        )
        is None
    )
    finish_exact_refresh(namespace, identity, old_token, succeeded=True)

    assert refresh_claim_is_current(namespace, identity, new_token) is True
    assert read_exact_snapshot(namespace, identity) is None

    published = publish_exact_snapshot_for_refresh(
        namespace,
        identity,
        payload,
        new_token,
    )
    assert published is not None
    assert published["data"] == payload["data"]
    assert refresh_claim_is_current(namespace, identity, new_token) is False


@pytest.mark.unit
def test_redis_lua_fence_rejects_old_token_and_atomically_publishes_new(monkeypatch):
    import pickle

    from tracer.services import exact_aggregation_cache as cache_module

    class FakeRawRedis:
        def __init__(self):
            self.values = {}
            self.calls = []

        def eval(self, script, numkeys, *parts):
            keys = parts[:numkeys]
            args = parts[numkeys:]
            self.calls.append((script, keys, args))
            if script == cache_module._REDIS_FENCED_PUBLISH_SCRIPT:
                lock_key, snapshot_key, state_key = keys
                token, stored, _ttl_ms = args
                if self.values.get(lock_key) != token:
                    return 0
                self.values[snapshot_key] = stored
                self.values.pop(state_key, None)
                self.values.pop(lock_key, None)
                return 1
            if script == cache_module._REDIS_FENCED_FINISH_SCRIPT:
                lock_key, state_key = keys
                token, succeeded, failed_state, _ttl_ms = args
                if self.values.get(lock_key) != token:
                    return 0
                if str(succeeded) == "1":
                    self.values.pop(state_key, None)
                else:
                    self.values[state_key] = failed_state
                self.values.pop(lock_key, None)
                return 1
            if script == cache_module._REDIS_FENCED_ACTIVATE_SCRIPT:
                lock_key, state_key = keys
                token, _ttl_ms, running_state = args
                if self.values.get(lock_key) != token:
                    return 0
                self.values[lock_key] = token
                self.values[state_key] = running_state
                return 1
            raise AssertionError("unexpected Redis script")

    class FakeRedisAdapter:
        def __init__(self):
            self.raw = FakeRawRedis()

        def get_client(self, *, write):
            assert write is True
            return self.raw

        @staticmethod
        def make_key(key):
            return f"futureagi:1:{key}"

        @staticmethod
        def encode(value):
            return pickle.dumps(value)

    adapter = FakeRedisAdapter()
    monkeypatch.setattr(cache_module, "cache", SimpleNamespace(client=adapter))
    namespace = "observe-test-redis-token-fence"
    identity = {"project": "p", "metric": "traffic"}
    old_token = "old-token"
    new_token = "new-token"
    lock_key = adapter.make_key(cache_module._refresh_lock_key(namespace, identity))
    state_key = adapter.make_key(cache_module._refresh_state_key(namespace, identity))
    snapshot_key = adapter.make_key(
        cache_module.snapshot_cache_key(namespace, identity)
    )
    adapter.raw.values[lock_key] = adapter.encode(new_token)
    adapter.raw.values[state_key] = adapter.encode(
        {"status": "running", "token": new_token}
    )
    payload = {
        "metric_name": "traffic",
        "data": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    assert (
        cache_module.publish_exact_snapshot_for_refresh(
            namespace,
            identity,
            payload,
            old_token,
        )
        is None
    )
    cache_module.finish_exact_refresh(
        namespace,
        identity,
        old_token,
        succeeded=False,
    )
    assert adapter.raw.values[lock_key] == adapter.encode(new_token)
    assert snapshot_key not in adapter.raw.values

    assert cache_module.activate_exact_refresh(namespace, identity, old_token) is False
    assert cache_module.activate_exact_refresh(namespace, identity, new_token) is True

    published = cache_module.publish_exact_snapshot_for_refresh(
        namespace,
        identity,
        payload,
        new_token,
    )

    assert published is not None
    assert lock_key not in adapter.raw.values
    assert state_key not in adapter.raw.values
    assert pickle.loads(adapter.raw.values[snapshot_key])["payload"] == payload
    assert [call[0] for call in adapter.raw.calls] == [
        cache_module._REDIS_FENCED_PUBLISH_SCRIPT,
        cache_module._REDIS_FENCED_FINISH_SCRIPT,
        cache_module._REDIS_FENCED_ACTIVATE_SCRIPT,
        cache_module._REDIS_FENCED_ACTIVATE_SCRIPT,
        cache_module._REDIS_FENCED_PUBLISH_SCRIPT,
    ]


@pytest.mark.unit
def test_snapshot_key_changes_when_exact_query_contract_version_changes(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    identity = {"project": "p", "metric": "latency"}
    current_key = cache_module.snapshot_cache_key("observe-system-graph", identity)

    monkeypatch.setattr(cache_module, "_CACHE_VERSION", 1)
    legacy_key = cache_module.snapshot_cache_key("observe-system-graph", identity)

    assert current_key.startswith("exact-aggregation:v3:")
    assert legacy_key.startswith("exact-aggregation:v1:")
    assert current_key != legacy_key


@pytest.mark.unit
def test_snapshot_key_fails_closed_for_unknown_identity_types():
    with pytest.raises(TypeError, match="unsupported snapshot identity type"):
        snapshot_cache_key("test", {"bad": object()})


@pytest.mark.unit
def test_cache_outage_does_not_hide_a_fresh_exact_result(monkeypatch):
    from tracer.services import exact_aggregation_cache as cache_module

    class BrokenCache:
        def set(self, *_args, **_kwargs):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(cache_module, "cache", BrokenCache())
    published = publish_exact_snapshot(
        "test-outage",
        {"project": "p"},
        {
            "metric_name": "traffic",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )

    assert published["query_complete"] is True
    assert published["query_cached"] is False
    assert published["query_completed_at"]


class _ConcurrentArrivalAnalytics:
    def __init__(self):
        self.partition_calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "now64" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        self.partition_calls.append((query, dict(params), dict(settings)))
        # Pretend a newer physical version arrives after the first partition.
        # The service must keep using the original ceiling for every partition.
        return SimpleNamespace(data=[], columns=["time_bucket"])


class _BudgetSplittingAnalytics:
    def __init__(self, *, error_code: int = 159):
        self.error_code = error_code
        self.partition_calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "now64" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        self.partition_calls.append((query, dict(params), timeout_ms, dict(settings)))
        if "exact_graph_candidate_limit" in params:
            raise ServerException("private detail", code=self.error_code)
        if (params["end_date"] - params["start_date"]).total_seconds() > 3600:
            raise ServerException("private detail", code=self.error_code)
        return SimpleNamespace(data=[], columns=["time_bucket"])


def _exact_multi_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        _time_filter(start, end),
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["Rechazado"],
            },
        },
        {
            "column_id": "confidence",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "greater_than_or_equal",
                "filter_value": 0.8,
            },
        },
    ]


def _exact_reported_sparse_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        _time_filter(start, end),
        {
            "column_id": "annotator",
            "filter_config": {
                "filter_type": "annotator",
                "filter_op": "is_null",
                "filter_value": None,
            },
        },
        {
            "column_id": "total_tokens",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 1,
            },
        },
        {
            "column_id": "ai_interruption_count",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "number",
                "filter_op": "greater_than",
                "filter_value": 3,
            },
        },
    ]


@pytest.mark.unit
def test_exact_span_partition_boundary_does_not_revive_a_winning_tombstone():
    start = datetime(2026, 8, 1)
    end = start + timedelta(minutes=12)
    physical_rows = [
        # ``start_time`` comes from the producer and the stateless collector can
        # receive a corrected value on a newer re-poll. These two rows share the
        # table's actual RMT identity because its time component is the hour.
        # A five-minute PREWHERE split would revive v1; an hour-aligned scan
        # resolves the tombstone before applying the requested output bounds.
        {
            "id": "moved-across-five-minute-cut",
            "trace_id": "trace-deleted",
            "start_time": start + timedelta(minutes=4, seconds=59),
            "version": 1,
            "is_deleted": 0,
            "final_status": "Rechazado",
        },
        {
            "id": "moved-across-five-minute-cut",
            "trace_id": "trace-deleted",
            "start_time": start + timedelta(minutes=5, seconds=1),
            "version": 2,
            "is_deleted": 1,
            "final_status": "Rechazado",
        },
        # A mutable predicate changes from non-match to match; only v2 counts.
        {
            "id": "latest-match",
            "trace_id": "trace-match",
            "start_time": start + timedelta(minutes=1),
            "version": 1,
            "is_deleted": 0,
            "final_status": "Pendiente",
        },
        {
            "id": "latest-match",
            "trace_id": "trace-match",
            "start_time": start + timedelta(minutes=1),
            "version": 2,
            "is_deleted": 0,
            "final_status": "Rechazado",
        },
        {
            "id": "tail-match",
            "trace_id": "trace-tail",
            "start_time": start + timedelta(minutes=11),
            "version": 1,
            "is_deleted": 0,
            "final_status": "Rechazado",
        },
        # The request end is exclusive.
        {
            "id": "outside-end",
            "trace_id": "trace-outside",
            "start_time": end,
            "version": 1,
            "is_deleted": 0,
            "final_status": "Rechazado",
        },
    ]
    columns = [
        "time_bucket",
        "latency_sum",
        "total_tokens",
        "cost_sum",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_count",
    ]

    class Analytics:
        def __init__(self):
            self.physical_ids_by_partition = []

        def execute_ch_query(self, query, params, **_kwargs):
            assert "argMax(" in query
            assert "tupleElement(graph_latest_row, 8) = 0" in query
            partition_rows = [
                row
                for row in physical_rows
                if params["graph_partition_start"]
                <= row["start_time"]
                < params["graph_partition_end"]
            ]
            self.physical_ids_by_partition.append([row["id"] for row in partition_rows])
            latest = {}
            for row in partition_rows:
                identity = (
                    row["trace_id"],
                    row["id"],
                    row["start_time"].replace(minute=0, second=0, microsecond=0),
                )
                if (
                    identity not in latest
                    or row["version"] > latest[identity]["version"]
                ):
                    latest[identity] = row
            matches = [
                row
                for row in latest.values()
                if not row["is_deleted"]
                and row["final_status"] == "Rechazado"
                and params["graph_contribution_start"]
                <= row["start_time"]
                < params["graph_contribution_end"]
            ]
            data = [
                {
                    "time_bucket": row["start_time"].replace(second=0, microsecond=0),
                    "latency_sum": 10,
                    "total_tokens": 1,
                    "cost_sum": Decimal("0.01"),
                    "traffic_count": 1,
                    "prompt_tokens": 1,
                    "completion_tokens": 0,
                    "error_count": 0,
                }
                for row in matches
            ]
            return SimpleNamespace(data=data, columns=columns)

    filters = [
        _time_filter(start, end),
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["Rechazado"],
            },
        },
    ]
    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=filters,
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    assert analytics.physical_ids_by_partition == [
        [
            "moved-across-five-minute-cut",
            "moved-across-five-minute-cut",
            "latest-match",
            "latest-match",
            "tail-match",
            "outside-end",
        ]
    ]
    assert sum(point["value"] for point in result["data"]) == 2
    assert result["query_count"] == 1
    assert result["query_sampled"] is False


def _exact_structured_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        _time_filter(start, end),
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rechazado",
            },
        },
        {
            "column_id": "tags",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "array",
                "filter_op": "contains",
                "filter_value": ["vip", 7, True],
            },
        },
        {
            "column_id": "profile",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "map",
                "filter_op": "contains",
                "filter_value": {"tier": "gold", "enabled": True},
            },
        },
        {
            "column_id": "legacy_payload",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "json",
                "filter_op": "contains",
                "filter_value": {"kind": "customer"},
            },
        },
    ]


@pytest.mark.unit
def test_exact_trace_candidate_probe_is_all_time_but_classifier_stays_authoritative():
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2026, 7, 24, 2, 43, 12)
    end = datetime(2026, 7, 31, 6, 59, 59)
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(start, end),
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    probe_sql, probe_params = builder.build_exact_graph_candidate_witness_probe(
        limit=201
    )
    classify_sql, classify_params = (
        builder.build_filter_identity_match_query_from_seed_rows(
            [{"trace_id": "candidate-from-late-or-stale-child"}]
        )
    )

    assert "LIMIT 1 BY trace_id" in probe_sql
    assert "GROUP BY trace_id" not in probe_sql
    assert "ORDER BY trace_id ASC" in probe_sql
    assert "LIMIT %(exact_graph_candidate_limit)s" in probe_sql
    assert "start_time >=" not in probe_sql
    assert "start_time <" not in probe_sql
    assert "latest_filter_key_0" in probe_sql
    assert "latest_filter_key_1" not in probe_sql
    assert "latest_filter_key_2" not in probe_sql
    assert "latest_filter_key_3" not in probe_sql
    assert probe_params["latest_filter_key_0"] == "final_status"
    assert probe_params["latest_filter_param_0"] == "rechazado"
    assert probe_params["exact_graph_candidate_limit"] == 201
    # The existing classifier, not the raw witness, retains every scalar and
    # structured leaf plus the canonical root request window.
    assert "latest_attr_exists_0" in classify_sql
    assert "latest_json_array_exists_1" in classify_sql
    assert "latest_json_map_exists_2" in classify_sql
    assert "latest_json_map_exists_3" in classify_sql
    assert classify_params["candidate_trace_ids"] == (
        "candidate-from-late-or-stale-child",
    )
    assert classify_params["candidate_start_date"] == start
    assert classify_params["candidate_end_date"] == end
    assert "candidate_witness_start_date_us" not in classify_params
    assert "candidate_witness_end_date_us" not in classify_params


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_type", "filter_op", "filter_value"),
    (
        ("number", "greater_than", -1),
        ("number", "equals", 0),
        ("number", "less_than", 1),
        ("number", "between", [-1, 1]),
        ("boolean", "equals", False),
    ),
)
def test_exact_trace_candidate_probe_keeps_key_only_when_map_default_can_match(
    filter_type,
    filter_op,
    filter_value,
):
    """A tied missing-key row must not make the candidate narrower than argMax."""

    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2025, 8, 19)
    end = datetime(2026, 8, 19)
    filters = _exact_reported_sparse_filters(start, end)
    filters[-1]["filter_config"].update(
        filter_type=filter_type,
        filter_op=filter_op,
        filter_value=filter_value,
    )
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=filters,
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    probe_sql, probe_params = builder.build_exact_graph_candidate_witness_probe(
        limit=1_001
    )
    compact_probe_sql = " ".join(probe_sql.split())

    assert "%(latest_filter_key_1)s" in compact_probe_sql
    assert "latest_filter_param_1" not in probe_params
    assert probe_params["latest_filter_key_1"] == "ai_interruption_count"


@pytest.mark.unit
def test_exact_trace_candidate_probe_uses_value_superset_for_positive_comparison():
    """Regress the DEV shape that hit the key-only 1,001-row sentinel.

    The post-deploy 2026-08-25 cold proof took 12.10s and 259 CH statements for
    ``ai_interruption_count > 3``. The optimization must therefore retain the
    positive raw-row comparison without sampling or weakening the unchanged
    exact classifier.
    """

    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2025, 8, 19)
    end = datetime(2026, 8, 19)
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_reported_sparse_filters(start, end),
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    probe_sql, probe_params = builder.build_exact_graph_candidate_witness_probe(
        limit=1_001
    )
    classify_sql, classify_params = (
        builder.build_filter_identity_match_query_from_seed_rows(
            [{"trace_id": "key-present-candidate"}]
        )
    )
    compact_probe_sql = " ".join(probe_sql.split())
    compact_classify_sql = " ".join(classify_sql.split())

    # Discovery is an all-time, unsampled raw-row value superset. Every current
    # >3 match necessarily has a physical live row satisfying this comparison;
    # stale historical matches are removed by the classifier below.
    assert "has(attrs_number.keys, %(latest_filter_key_1)s)" in compact_probe_sql
    assert (
        "attrs_number[%(latest_filter_key_1)s] > %(latest_filter_param_1)s"
        in compact_probe_sql
    )
    assert probe_params["latest_filter_param_1"] == 3.0
    assert "start_time >=" not in compact_probe_sql
    assert "start_time <" not in compact_probe_sql
    assert "is_deleted = 0" not in compact_probe_sql
    assert "modulo(" not in compact_probe_sql
    assert "LIMIT 1 BY trace_id" in compact_probe_sql
    assert "LIMIT %(exact_graph_candidate_limit)s" in compact_probe_sql
    assert probe_params["latest_filter_key_1"] == "ai_interruption_count"
    assert probe_params["exact_graph_candidate_limit"] == 1_001

    # The existing classifier remains the sole authority for the annotation
    # absence, root token comparison, latest attribute value, and root window.
    assert "model_hub_score AS s FINAL" in compact_classify_sql
    assert "trace_id NOT IN" in compact_classify_sql
    assert "latest_column_value_0 > %(latest_filter_param_0)s" in compact_classify_sql
    assert "latest_attr_value_1 > %(latest_filter_param_1)s" in compact_classify_sql
    assert classify_params["latest_filter_param_0"] == 1.0
    assert classify_params["latest_filter_param_1"] == 3.0
    assert classify_params["candidate_trace_ids"] == ("key-present-candidate",)
    assert classify_params["candidate_start_date"] == start
    assert classify_params["candidate_end_date"] == end


@pytest.mark.unit
def test_exact_trace_candidate_probe_keeps_long_typed_picker_value():
    """A retained prompt must narrow the graph by key and value, not key alone."""

    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2025, 8, 19)
    end = datetime(2026, 8, 19)
    long_prompt = (
        "Metadata-backed prompt: this retained conversation value is intentionally "
        "long enough to exercise the production picker path without truncating the "
        "bound comparison. It must remain an exact, case-insensitive value witness "
        "for the graph candidate query even across a twelve-month request window. "
        "The latest-state classifier remains authoritative."
    )
    assert len(long_prompt) > 256
    attribute_key = "metadata.conversation.transcript.0.message.content"
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[
            _time_filter(start, end),
            {
                "column_id": attribute_key,
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": [long_prompt],
                    "attribute_value_types": ["string"],
                },
            },
        ],
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    probe_sql, probe_params = builder.build_exact_graph_candidate_witness_probe(
        limit=1_001
    )
    compact_probe_sql = " ".join(probe_sql.split())

    assert "has(attrs_string.keys, %(latest_filter_key_0)s)" in compact_probe_sql
    assert (
        "lowerUTF8(toString(attrs_string[%(latest_filter_key_0)s])) "
        "IN %(latest_filter_param_0_string)s" in compact_probe_sql
    )
    assert probe_params["latest_filter_key_0"] == attribute_key
    assert probe_params["latest_filter_param_0_string"] == (long_prompt.lower(),)
    assert probe_params["exact_graph_candidate_limit"] == 1_001


@pytest.mark.unit
def test_exact_trace_value_superset_candidate_probe_rejects_sampling():
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2025, 8, 19)
    end = datetime(2026, 8, 19)
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_reported_sparse_filters(start, end),
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
        bounded_sampling_salt="sampling-cannot-prove-exactness",
        bounded_sampling_rate=50,
    )

    assert builder.build_exact_graph_candidate_witness_probe(limit=1_001) == ("", {})


@pytest.mark.unit
def test_exact_reported_sparse_shape_uses_two_statement_candidate_lane():
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    start = datetime(2025, 8, 19)
    end = datetime(2026, 8, 19)
    calls = []

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **kwargs):
            calls.append((query, dict(params), dict(kwargs)))
            if "exact_graph_candidate_limit" in params:
                assert "has(attrs_number.keys, %(latest_filter_key_1)s)" in query
                assert (
                    "attrs_number[%(latest_filter_key_1)s]"
                    " > %(latest_filter_param_1)s" in query
                )
                assert params["latest_filter_param_1"] == 3.0
                return SimpleNamespace(
                    data=[
                        {"trace_id": "current-match"},
                        {"trace_id": "stale-raw-value-only"},
                    ],
                    columns=["trace_id"],
                    query_time_ms=1,
                )
            assert params["candidate_trace_ids"] == (
                "current-match",
                "stale-raw-value-only",
            )
            assert "latest_column_value_0 > %(latest_filter_param_0)s" in query
            assert "latest_attr_value_1 > %(latest_filter_param_1)s" in query
            assert "model_hub_score AS s FINAL" in query
            assert "trace_id NOT IN" in query
            # The key witness deliberately includes a stale/non-matching
            # candidate; only latest-state replay may admit a graph member.
            return SimpleNamespace(
                data=[{"trace_id": "current-match"}],
                columns=["trace_id"],
                query_time_ms=1,
            )

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_reported_sparse_filters(start, end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == ["current-match"]
    assert query_count == 2
    assert rows_returned == 3
    assert len(calls) == 2


@pytest.mark.unit
def test_exact_trace_candidate_probe_rejects_structured_only_filter():
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 2)
    structured_filters = _exact_structured_filters(start, end)
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[structured_filters[0], structured_filters[2]],
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    assert builder.build_exact_graph_candidate_witness_probe(limit=201) == ("", {})


@pytest.mark.unit
def test_exact_trace_candidate_probe_uses_negative_annotator_relation():
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2025, 8, 18, 7, 38, 38)
    end = datetime(2026, 8, 19, 7)
    annotator_id = "00000000-0000-4000-8000-000000000099"
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[
            _time_filter(start, end),
            {
                "column_id": "duration",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 30,
                },
            },
            {
                "column_id": "annotator",
                "filter_config": {
                    "filter_type": "annotator",
                    "filter_op": "not_equals",
                    "filter_value": annotator_id,
                },
            },
        ],
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
    )

    probe_sql, probe_params = builder.build_exact_graph_candidate_witness_probe(
        limit=201
    )
    classify_sql, classify_params = (
        builder.build_filter_identity_match_query_from_seed_rows(
            [{"trace_id": "annotated-candidate"}]
        )
    )
    compact_probe_sql = " ".join(probe_sql.split())
    compact_classify_sql = " ".join(classify_sql.split())

    assert "model_hub_score AS s FINAL" in compact_probe_sql
    assert "s.annotator_id IN (toUUID(%(uid_1)s))" in compact_probe_sql
    assert "trace_id NOT IN" in compact_probe_sql
    assert "ORDER BY start_time DESC, trace_id DESC" in compact_probe_sql
    assert probe_params["uid_1"] == annotator_id
    assert probe_params["filter_slice_start"] == start
    assert probe_params["filter_slice_end"] == end
    assert probe_params["filter_seed_limit"] == 201
    # The relation probe is only a finite candidate selector. The ordinary
    # latest-state classifier remains authoritative for both the negative
    # annotator exclusion and every scalar filter before graph publication.
    assert "model_hub_score AS s FINAL" in compact_classify_sql
    assert "trace_id NOT IN" in compact_classify_sql
    assert classify_params["candidate_trace_ids"] == ("annotated-candidate",)


@pytest.mark.unit
def test_exact_trace_candidate_probe_rejects_sampling():
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 2)
    builder = TraceListQueryBuilderV2(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(start, end),
        page_number=0,
        page_size=200,
        bounded_internal_scan=True,
        bounded_identity_only=True,
        bounded_bulk_scan=True,
        bounded_include_filter_witnesses=False,
        bounded_global_span_witnesses=True,
        bounded_sampling_salt="exactness-forbids-this",
        bounded_sampling_rate=50,
    )

    assert builder.build_exact_graph_candidate_witness_probe(limit=201) == ("", {})


def _combined_relation_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        _time_filter(start, end),
        {
            "column_id": "has_eval",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
        {
            "column_id": "has_annotation",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "boolean",
                "filter_op": "equals",
                "filter_value": True,
            },
        },
        {
            "column_id": "user_id",
            "filter_config": {
                "col_type": "TRACE_END_USER",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "customer-42",
            },
        },
    ]


def _stub_annotation_label_ids(monkeypatch, exact_module) -> None:
    """Keep SQL-shape tests independent of the PostgreSQL label catalog."""

    monkeypatch.setattr(
        exact_module,
        "_annotation_label_ids_for_filters",
        lambda _project_id, _filters: ("55555555-5555-4555-8555-555555555555",),
    )


class _RelationSnapshotAnalytics:
    def __init__(self, *, fail_table: str | None = None):
        self.fail_table = fail_table
        self.capture_calls: list[str] = []
        self.main_calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "toUnixTimestamp64Nano(now64" in query:
            self.capture_calls.append("spans")
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        if "max(_peerdb_version)" in query and "FROM tracer_eval_logger" in query:
            self.capture_calls.append("tracer_eval_logger")
            if self.fail_table == "tracer_eval_logger":
                raise RuntimeError("eval ceiling unavailable")
            return SimpleNamespace(
                data=[{"version_ceiling": 701}],
                columns=["version_ceiling"],
            )
        if "max(_peerdb_version)" in query and "FROM model_hub_score" in query:
            self.capture_calls.append("model_hub_score")
            if self.fail_table == "model_hub_score":
                raise RuntimeError("score ceiling unavailable")
            return SimpleNamespace(
                data=[{"version_ceiling": 801}],
                columns=["version_ceiling"],
            )
        if "max(toUnixTimestamp64Micro(version))" in query:
            table = next(
                (
                    name
                    for name in (
                        "end_user_id_remap",
                        "trace_session_id_remap",
                        "end_users",
                    )
                    if f"FROM {name}" in query
                ),
                "unknown_datetime_relation",
            )
            self.capture_calls.append(table)
            if self.fail_table == table:
                raise RuntimeError(f"{table} ceiling unavailable")
            return SimpleNamespace(
                data=[{"version_ceiling": 901}],
                columns=["version_ceiling"],
            )
        self.main_calls.append((query, dict(params), dict(settings)))
        if params.get("candidate_trace_ids"):
            return SimpleNamespace(
                data=[
                    {"trace_id": trace_id} for trace_id in params["candidate_trace_ids"]
                ],
                columns=["trace_id"],
            )
        if params.get("candidate_span_ids"):
            return SimpleNamespace(
                data=[
                    {"id": span_id, "identity_count": 1, "matched": 1}
                    for span_id in params["candidate_span_ids"]
                ],
                columns=["id", "identity_count", "matched"],
            )
        return SimpleNamespace(data=[], columns=["time_bucket"])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("item", "expected_eval", "expected_score", "expected_end_users"),
    [
        (
            {
                "column_id": "eval-config",
                "filter_config": {"col_type": "EVAL_METRIC"},
            },
            True,
            False,
            False,
        ),
        (
            {"columnId": "has_eval", "filterConfig": {"colType": "NORMAL"}},
            True,
            False,
            False,
        ),
        (
            {
                "column_id": "annotation-label",
                "filter_config": {"col_type": "ANNOTATION"},
            },
            False,
            True,
            False,
        ),
        (
            {"column_id": "has_annotation", "filter_config": {}},
            False,
            True,
            False,
        ),
        (
            {"column_id": "my_annotations", "filter_config": {}},
            False,
            True,
            False,
        ),
        (
            {
                "column_id": "user_id",
                "filter_config": {"col_type": "TRACE_END_USER"},
            },
            False,
            False,
            True,
        ),
    ],
)
def test_filter_relation_snapshot_plan_detects_every_relational_filter(
    item,
    expected_eval,
    expected_score,
    expected_end_users,
):
    requirements = _filter_relation_requirements([item])

    assert requirements.eval_logger is expected_eval
    assert requirements.score is expected_score
    assert requirements.end_users is expected_end_users


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["span"])
def test_exact_system_graph_compiles_relations_in_one_project_scoped_statement(
    monkeypatch,
    observe_type,
):
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    # This is a SQL-shape contract, not a partition-volume test. Keep it to one
    # storage-identity hour so a mock without execution timing cannot expand a
    # multi-month window into thousands of deterministic base statements.
    end = start + timedelta(minutes=5)

    class _ProjectConfigs:
        @staticmethod
        def values_list(*_args, **_kwargs):
            return ("33333333-3333-4333-8333-333333333333",)

    monkeypatch.setattr(
        CustomEvalConfig.objects,
        "filter",
        lambda **_kwargs: _ProjectConfigs(),
    )

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_combined_relation_filters(start, end),
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 1
    query, params, settings = analytics.main_calls[0]
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "FROM tracer_eval_logger" in query
    assert "AS eval_scan" in query
    assert "FROM model_hub_score AS s FINAL" in query
    assert "FROM end_users AS eu FINAL" in query
    assert "tracer_project_id = toUUID(" in query
    assert "eu.project_id = toUUID(" in query
    assert "additional_table_filters" not in settings
    assert params["graph_filter_1_project_eval_config_ids"] == (
        "33333333-3333-4333-8333-333333333333",
    )
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_user_id_relation_filter_uses_one_spans_source_and_curated_remap():
    analytics = _RelationSnapshotAnalytics()
    start = datetime(2026, 1, 1)
    end = start + timedelta(minutes=5)
    user_filter = _combined_relation_filters(start, end)[-1]

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[_time_filter(start, end), user_filter],
        interval="day",
        metric_id="traffic",
        observe_type="span",
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 1
    query, _params, _settings = analytics.main_calls[0]
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert query.count("FROM end_users AS eu FINAL") == 1
    assert query.count("FROM end_user_id_remap FINAL") == 1
    assert "graph_relation_end_user_id" in query
    assert result["query_complete"] is True


@pytest.mark.unit
def test_system_graph_does_not_issue_separate_relation_snapshot_queries(
    monkeypatch,
):
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics(fail_table="model_hub_score")
    _stub_annotation_label_ids(monkeypatch, exact_module)

    class _ProjectConfigs:
        @staticmethod
        def values_list(*_args, **_kwargs):
            return ("33333333-3333-4333-8333-333333333333",)

    monkeypatch.setattr(
        CustomEvalConfig.objects,
        "filter",
        lambda **_kwargs: _ProjectConfigs(),
    )

    read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_combined_relation_filters(
            datetime(2026, 1, 1),
            datetime(2026, 1, 1, 0, 5),
        ),
        interval="day",
        metric_id="traffic",
        observe_type="span",
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["span"])
def test_exact_system_graph_combines_scalar_array_map_and_legacy_json(observe_type):
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 8, 1)
    end = start + timedelta(minutes=12)

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(start, end),
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )

    assert len(analytics.partition_calls) == 1
    query, params, settings = analytics.partition_calls[0]
    assert "attrs_string" in query
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert "toString(JSONType(attributes_extra" in query
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert params["graph_partition_start"] == start
    assert params["graph_partition_end"] == start + timedelta(hours=1)
    assert params["graph_contribution_start"] == start
    assert params["graph_contribution_end"] == end
    assert params["graph_filter_2_latest_filter_key_2"] == "tags"
    assert params["graph_filter_3_latest_filter_key_3"] == "profile"
    assert params["graph_filter_4_latest_filter_key_4"] == "legacy_payload"
    assert "additional_table_filters" not in settings
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "toUInt8(is_deleted)" in query
    assert "tupleElement(graph_latest_row, 8) = 0" in query
    assert "PREWHERE project_id = %(project_id)s" in query
    prewhere = query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "project_id" in prewhere
    assert "start_time" in prewhere
    assert "attrs_" not in prewhere
    assert "is_deleted" not in prewhere
    assert "snapshot_version_ceiling" not in params
    assert "AS latest_spans" not in query
    assert "SELECT DISTINCT trace_id" not in query
    assert "OVER (PARTITION BY trace_id) AS graph_match_" not in query
    assert "graph_bucket_match_" not in query
    assert "AS latency_sum" in query
    assert "AS cost_sum" in query
    assert "AS error_count" in query
    assert result["query_count"] == 1
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_trace_membership_prefers_authoritative_anchor_before_candidate(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 7, 1)
    request_end = datetime(2026, 8, 1)
    calls: list[str] = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_exact_graph_candidate_witness_probe(**_kwargs):
            raise AssertionError(
                "the finite candidate classifier must not run after anchor proof"
            )

    def enumerate_anchor(**kwargs):
        assert isinstance(kwargs["builder"], Builder)
        assert kwargs["request_start"] == request_start
        assert kwargs["request_end"] == request_end
        calls.append("anchor")
        return ["trace-from-anchor"], 4, 5

    class Analytics:
        @staticmethod
        def execute_ch_query(*_args, **_kwargs):
            raise AssertionError("the stubbed authoritative route owns all reads")

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(
        exact_module,
        "_enumerate_authoritative_anchor_trace_ids",
        enumerate_anchor,
    )

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert calls == ["anchor"]
    assert trace_ids == ["trace-from-anchor"]
    assert query_count == 4
    assert rows_returned == 5


@pytest.mark.unit
def test_exact_trace_membership_sparse_candidate_probe_avoids_root_fanout(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 7, 24, 2, 43, 12)
    request_end = datetime(2026, 7, 31, 6, 59, 59)
    constructor_kwargs = {}
    calls = []

    class Builder:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_exact_graph_candidate_witness_probe(*, limit):
            return "CANDIDATE", {"limit": limit}

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            pytest.fail("a closed sparse candidate probe must skip root enumeration")

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {"ids": tuple(row["trace_id"] for row in rows)}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **kwargs):
            calls.append((query, dict(params), dict(kwargs["settings"])))
            if query == "CANDIDATE":
                return SimpleNamespace(
                    data=[
                        {"trace_id": "late-child-valid-root"},
                        {"trace_id": "stale-or-root-outside"},
                    ],
                    columns=["trace_id"],
                    query_time_ms=1,
                )
            assert query == "CLASSIFY"
            # Latest-state replay is authoritative: it accepts the trace whose
            # matching child was written after the root window and rejects a
            # stale raw match or a trace whose canonical root is out of range.
            assert params["ids"] == (
                "late-child-valid-root",
                "stale-or-root-outside",
            )
            return SimpleNamespace(
                data=[{"trace_id": "late-child-valid-root"}],
                columns=["trace_id"],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert constructor_kwargs["bounded_global_span_witnesses"] is True
    assert trace_ids == ["late-child-valid-root"]
    assert query_count == 2
    assert rows_returned == 3
    assert calls[0][0] == "CANDIDATE"
    assert calls[0][1]["limit"] == exact_module.EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL
    assert (
        calls[0][2]["max_result_rows"]
        == exact_module.EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL
    )


@pytest.mark.unit
def test_exact_trace_membership_candidate_probe_read_budget_falls_back_without_rows(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    root_start = request_end - timedelta(minutes=1)
    classifier_batches = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_exact_graph_candidate_witness_probe(*, limit, **_cursor):
            return "CANDIDATE", {"limit": limit}

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "ROOT", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, _params, **_kwargs):
            if query == "CANDIDATE":
                raise ServerException("private bounded probe cap", code=158)
            if query == "ROOT":
                return SimpleNamespace(
                    data=[{"trace_id": "root-proven", "start_time": root_start}],
                    columns=["trace_id", "start_time"],
                    query_time_ms=1,
                )
            assert query == "CLASSIFY"
            return SimpleNamespace(
                data=[{"trace_id": "root-proven"}],
                columns=["trace_id"],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL", 3)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == ["root-proven"]
    assert classifier_batches == [("root-proven",)]
    assert query_count == 3
    assert rows_returned == 2


@pytest.mark.unit
def test_exact_trace_membership_exhausts_broad_candidate_keyset_without_root_fanout(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    candidate_cursors = []
    candidate_limits = []
    classifier_batches = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_exact_graph_candidate_witness_probe(
            *, limit, after_trace_id=None, **_cursor
        ):
            candidate_cursors.append(after_trace_id)
            candidate_limits.append(limit)
            return "CANDIDATE", {"limit": limit, "after": after_trace_id}

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            pytest.fail("an exhaustible candidate keyset must skip root enumeration")

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "CANDIDATE":
                values = (
                    ["trace-1", "trace-2", "trace-3"]
                    if params["after"] is None
                    else ["trace-4", "trace-5", "trace-6", "trace-7"]
                )
                return SimpleNamespace(
                    data=[{"trace_id": value} for value in values],
                    columns=["trace_id"],
                    query_time_ms=1,
                )
            assert query == "CLASSIFY"
            return SimpleNamespace(
                data=[{"trace_id": value} for value in params["ids"]],
                columns=["trace_id"],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL", 3)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 5)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == [
        "trace-1",
        "trace-2",
        "trace-3",
        "trace-4",
        "trace-5",
        "trace-6",
        "trace-7",
    ]
    assert candidate_cursors == [None, "trace-3"]
    assert candidate_limits == [3, 5]
    assert classifier_batches == [
        ("trace-1", "trace-2", "trace-3"),
        ("trace-4", "trace-5", "trace-6", "trace-7"),
    ]
    assert query_count == 4
    assert rows_returned == 14


@pytest.mark.unit
def test_exact_trace_membership_candidate_probe_programming_error_fails_closed(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_exact_graph_candidate_witness_probe(*, limit):
            return "CANDIDATE", {"limit": limit}

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            pytest.fail("a programming error must not fall back or publish")

    class Analytics:
        @staticmethod
        def execute_ch_query(*_args, **_kwargs):
            raise RuntimeError("candidate compiler defect")

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    with pytest.raises(RuntimeError, match="candidate compiler defect"):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )


@pytest.mark.unit
def test_exact_trace_membership_exhausts_witness_cursor_and_classifies_each_trace_once(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    t4 = datetime(2026, 8, 4)
    t3 = datetime(2026, 8, 3)
    t2 = datetime(2026, 8, 2)
    t1 = datetime(2026, 8, 1)
    seed_calls = []
    seed_pages = iter(
        [
            [
                {"trace_id": "trace-3", "start_time": t3},
                {"trace_id": "trace-2", "start_time": t2},
            ],
            [
                {"trace_id": "trace-1", "start_time": t1},
            ],
        ]
    )

    class Builder:
        def __init__(self, **kwargs):
            assert kwargs["bounded_internal_scan"] is True
            assert kwargs["bounded_identity_only"] is True
            assert kwargs["bounded_bulk_scan"] is True
            assert kwargs["bounded_include_filter_witnesses"] is False

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return t1, t4

        @staticmethod
        def exact_graph_filter_witness_range():
            return t1, t4

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            seed_calls.append(
                (
                    kwargs["before_start_time"],
                    kwargs["before_id"],
                )
            )
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {"ids": tuple(row["trace_id"] for row in rows)}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "WITNESS":
                return SimpleNamespace(data=next(seed_pages), columns=[])
            assert query == "CLASSIFY"
            return SimpleNamespace(
                data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                columns=["trace_id"],
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 2)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 2)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_INITIAL_SLICE", t4 - t1)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(t1, t4),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == ["trace-3", "trace-2", "trace-1"]
    assert query_count == 4
    assert rows_returned == 6
    assert seed_calls == [
        (None, None),
        (t2, "trace-2"),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["classifier_error", "no_progress"])
def test_exact_trace_membership_fails_closed_on_unproven_witness(monkeypatch, failure):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = datetime(2026, 8, 3)
    checkpoint = datetime(2026, 8, 2)
    repeated = [
        {
            "trace_id": "trace-2",
            "matched_span_id": "span-2",
            "start_time": checkpoint,
        }
    ]

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["matched_span_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["matched_span_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {"ids": tuple(row["trace_id"] for row in rows)}

    class Analytics:
        def __init__(self):
            self.seed_calls = 0

        def execute_ch_query(self, query, params, **_kwargs):
            if query == "CLASSIFY":
                if failure == "classifier_error":
                    raise RuntimeError("classifier failed")
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                    columns=["trace_id"],
                )
            self.seed_calls += 1
            return SimpleNamespace(data=repeated, columns=[])

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 1)
    monkeypatch.setattr(
        exact_module,
        "EXACT_GRAPH_TRACE_INITIAL_SLICE",
        request_end - request_start,
    )
    expected_error = (
        RuntimeError if failure == "classifier_error" else ExactGraphReadError
    )
    with pytest.raises(expected_error):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )


@pytest.mark.unit
def test_exact_trace_membership_widens_empty_slices_logarithmically(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=20)
    slices = []
    timeouts = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            slices.append((kwargs["slice_start"], kwargs["slice_end"]))
            return "WITNESS", {}

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, **kwargs):
            timeouts.append(kwargs["timeout_ms"])
            # Alternate executors may not expose QueryResult.query_time_ms;
            # the monotonic wall-time fallback must retain adaptive growth.
            return SimpleNamespace(data=[], columns=[])

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == []
    assert query_count == 3
    assert rows_returned == 0
    assert [end - start for start, end in slices] == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=5),
    ]
    assert slices[0][1] == request_end
    assert all(
        previous[0] == following[1]
        for previous, following in zip(slices, slices[1:], strict=False)
    )
    assert slices[-1][0] == request_start
    assert all(
        0 < timeout <= exact_module.EXACT_GRAPH_TRACE_WITNESS_QUERY_TIMEOUT_MS
        for timeout in timeouts
    )


@pytest.mark.unit
def test_exact_trace_membership_keeps_expensive_successful_slices_narrow(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=20)
    slices = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            slices.append((kwargs["slice_start"], kwargs["slice_end"]))
            return "WITNESS", {}

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, **_kwargs):
            return SimpleNamespace(
                data=[],
                columns=[],
                query_time_ms=(exact_module.EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS + 1),
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == []
    assert query_count == 4
    assert rows_returned == 0
    assert [end - start for start, end in slices] == [timedelta(minutes=5)] * 4


@pytest.mark.unit
def test_exact_trace_membership_retries_same_upper_bound_then_recovers_below_failure(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=30)
    attempted_slices = []
    successful_slices = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

    class Analytics:
        failed_interval = None

        @staticmethod
        def execute_ch_query(_query, params, **_kwargs):
            interval = (params["slice_start"], params["slice_end"])
            attempted_slices.append(interval)
            if (
                interval[1] - interval[0] == timedelta(minutes=10)
                and Analytics.failed_interval is None
            ):
                Analytics.failed_interval = interval
                raise ServerException("bounded read exceeded", code=159)
            successful_slices.append(interval)
            return SimpleNamespace(data=[], columns=[], query_time_ms=1)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == []
    assert query_count == 6
    assert rows_returned == 0
    assert [end - start for start, end in attempted_slices] == [
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=5),
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=5),
    ]
    assert attempted_slices[1][1] == attempted_slices[2][1]
    assert successful_slices[0][1] == request_end
    assert all(
        previous[0] == following[1]
        for previous, following in zip(
            successful_slices,
            successful_slices[1:],
            strict=False,
        )
    )
    assert successful_slices[-1][0] == request_start
    assert Analytics.failed_interval not in successful_slices


@pytest.mark.unit
def test_exact_trace_membership_can_fallback_below_five_minutes(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=10)
    attempted_widths = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

    class Analytics:
        failed = False

        @staticmethod
        def execute_ch_query(_query, params, **_kwargs):
            width = params["slice_end"] - params["slice_start"]
            attempted_widths.append(width)
            if not Analytics.failed:
                Analytics.failed = True
                raise ServerException("bounded read exceeded", code=159)
            return SimpleNamespace(data=[], columns=[], query_time_ms=1)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == []
    assert query_count == 4
    assert rows_returned == 0
    assert attempted_widths == [
        timedelta(minutes=5),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=5),
    ]


@pytest.mark.unit
def test_exact_trace_membership_recovers_from_hot_newest_to_sparse_old_history(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 7, 1)
    request_end = request_start + timedelta(days=30)
    attempted_slices = []
    successful_slices = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

    class Analytics:
        failed = False
        failed_interval = None

        @staticmethod
        def execute_ch_query(_query, params, **_kwargs):
            interval = (params["slice_start"], params["slice_end"])
            attempted_slices.append(interval)
            if not Analytics.failed:
                Analytics.failed = True
                Analytics.failed_interval = interval
                raise ServerException("hot newest partition", code=159)
            successful_slices.append(interval)
            return SimpleNamespace(data=[], columns=[], query_time_ms=1)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    attempted_widths = [end - start for start, end in attempted_slices]
    assert trace_ids == []
    assert rows_returned == 0
    assert query_count == len(attempted_slices) < 30
    assert attempted_widths[:6] == [
        timedelta(minutes=5),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=5),
        timedelta(minutes=10),
        timedelta(minutes=20),
    ]
    assert max(attempted_widths) == exact_module.EXACT_GRAPH_TRACE_MAX_SLICE
    assert Analytics.failed_interval not in successful_slices
    assert successful_slices[0][1] == request_end
    assert all(
        previous[0] == following[1]
        for previous, following in zip(
            successful_slices,
            successful_slices[1:],
            strict=False,
        )
    )
    assert successful_slices[-1][0] == request_start


@pytest.mark.unit
def test_exact_trace_membership_fails_closed_at_minimum_slice(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    attempted_widths = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, params, **_kwargs):
            attempted_widths.append(params["slice_end"] - params["slice_start"])
            raise ServerException("bounded read exceeded", code=159)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    with pytest.raises(ServerException, match="bounded read exceeded"):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )

    assert attempted_widths == [
        timedelta(minutes=5),
        timedelta(minutes=2, seconds=30),
        timedelta(minutes=1, seconds=15),
        timedelta(seconds=37, milliseconds=500),
        exact_module.EXACT_GRAPH_TRACE_MIN_SLICE,
    ]
    assert min(attempted_widths) == exact_module.EXACT_GRAPH_TRACE_MIN_SLICE


@pytest.mark.unit
def test_exact_trace_membership_enforces_whole_refresh_deadline(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=20)
    clock = [0.0]
    calls = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, params, **kwargs):
            calls.append((dict(params), kwargs["timeout_ms"]))
            clock[0] += 0.06
            return SimpleNamespace(data=[], columns=[], query_time_ms=60)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 100)
    monkeypatch.setattr(exact_module, "monotonic", lambda: clock[0])

    with pytest.raises(ExactGraphReadError, match="bounded deadline"):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=0.0,
        )

    assert len(calls) == 2
    assert [timeout for _params, timeout in calls] == [100, 40]


@pytest.mark.unit
def test_exact_trace_membership_seeds_in_window_root_and_classifies_child_over_day_late(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=10)
    root_start = request_end - timedelta(minutes=1)
    child_start = request_end + timedelta(days=3)
    seed_slices = []
    constructor_kwargs = {}

    class Builder:
        def __init__(self, **kwargs):
            constructor_kwargs.update(kwargs)

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            seed_slices.append((kwargs["slice_start"], kwargs["slice_end"]))
            return "WITNESS", {
                "slice_start": kwargs["slice_start"],
                "slice_end": kwargs["slice_end"],
            }

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {
                "ids": tuple(row["trace_id"] for row in rows),
                "root_start": root_start,
                "child_start": child_start,
            }

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "CLASSIFY":
                assert request_start <= params["root_start"] < request_end
                assert params["child_start"] > request_end + timedelta(days=1)
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                    columns=["trace_id"],
                    query_time_ms=1,
                )
            if not params["slice_start"] <= root_start < params["slice_end"]:
                return SimpleNamespace(data=[], columns=[], query_time_ms=1)
            return SimpleNamespace(
                data=[
                    {
                        "trace_id": "trace-root-inside-child-three-days-late",
                        "start_time": root_start,
                    }
                ],
                columns=[],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert constructor_kwargs["bounded_global_span_witnesses"] is True
    assert trace_ids == ["trace-root-inside-child-three-days-late"]
    assert query_count == len(seed_slices) + 1
    assert rows_returned == 2
    assert seed_slices[0][1] == request_end
    assert all(
        previous[0] == following[1]
        for previous, following in zip(seed_slices, seed_slices[1:], strict=False)
    )
    assert seed_slices[-1][0] == request_start
    assert all(
        request_start <= slice_start < slice_end <= request_end
        for slice_start, slice_end in seed_slices
    )


@pytest.mark.unit
def test_exact_trace_membership_rejects_seed_when_latest_live_root_is_outside_window(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    stale_root_start = request_start + timedelta(minutes=1)
    moved_root_start = request_end + timedelta(days=2)

    class Builder:
        def __init__(self, **kwargs):
            assert kwargs["bounded_global_span_witnesses"] is True

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "ROOT_SEED", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {
                "ids": tuple(row["trace_id"] for row in rows),
                "moved_root_start": moved_root_start,
            }

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "ROOT_SEED":
                return SimpleNamespace(
                    data=[
                        {
                            "trace_id": "trace-root-moved-out",
                            "start_time": stale_root_start,
                        }
                    ],
                    columns=[],
                    query_time_ms=1,
                )
            assert query == "CLASSIFY"
            assert params["moved_root_start"] >= request_end
            # The latest-state classifier is authoritative: a stale in-window
            # raw root is only a candidate, never publishable membership.
            return SimpleNamespace(data=[], columns=["trace_id"], query_time_ms=1)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == []
    assert query_count == 2
    assert rows_returned == 1


@pytest.mark.unit
def test_exact_trace_membership_exhausts_5k_identity_classifier_boundary(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    checkpoint = request_end - timedelta(minutes=1)
    ordered_ids = [f"trace-{index:030d}" for index in range(10_000, -1, -1)]
    seed_pages = iter(
        [
            [
                {"trace_id": trace_id, "start_time": checkpoint}
                for trace_id in ordered_ids[:5_000]
            ],
            [
                {"trace_id": trace_id, "start_time": checkpoint}
                for trace_id in ordered_ids[5_000:10_000]
            ],
            [{"trace_id": ordered_ids[-1], "start_time": checkpoint}],
        ]
    )
    classifier_batches = []
    classifier_timeouts = []
    classifier_settings = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **kwargs):
            if query == "WITNESS":
                return SimpleNamespace(data=next(seed_pages), columns=[])
            classifier_timeouts.append(kwargs["timeout_ms"])
            classifier_settings.append(kwargs["settings"])
            return SimpleNamespace(
                data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                columns=["trace_id"],
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert exact_module.EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE == 5_000
    assert exact_module.EXACT_GRAPH_TRACE_CANDIDATE_SENTINEL == 1_001
    assert exact_module.EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE == 5_000
    assert (
        exact_module.EXACT_GRAPH_TRACE_CLASSIFIER_QUERY_TIMEOUT_MS
        == exact_module.EXACT_GRAPH_WALL_DEADLINE_MS
    )
    assert exact_module.EXACT_GRAPH_READ_SETTINGS["max_threads"] == 1
    assert "max_rows_to_read" not in exact_module.EXACT_GRAPH_READ_SETTINGS
    assert exact_module.EXACT_GRAPH_READ_SETTINGS["max_bytes_to_read"] == (
        exact_module.EXACT_GRAPH_MAX_BYTES_TO_READ
    )
    assert exact_module.EXACT_GRAPH_MAX_BYTES_TO_READ == 1024**4
    assert exact_module.EXACT_GRAPH_TRACE_CLASSIFIER_READ_SETTINGS["max_threads"] == 8
    assert trace_ids == ordered_ids
    assert [len(batch) for batch in classifier_batches] == [5_000, 5_000, 1]
    assert len(classifier_timeouts) == 3
    assert all(
        0 < timeout <= exact_module.EXACT_GRAPH_TRACE_CLASSIFIER_QUERY_TIMEOUT_MS
        for timeout in classifier_timeouts
    )
    assert [settings["max_threads"] for settings in classifier_settings] == [8, 8, 8]
    assert [settings["max_result_rows"] for settings in classifier_settings] == [
        5_000,
        5_000,
        1,
    ]
    assert query_count == 6
    assert rows_returned == 20_002


@pytest.mark.unit
@pytest.mark.parametrize(
    "classifier_error",
    [
        ServerException("private bounded classifier timeout", code=159),
        ServerException("Max query size exceeded at position 262133", code=62),
    ],
)
def test_exact_trace_membership_bisects_retryable_classifier_without_gaps(
    monkeypatch,
    classifier_error,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    checkpoint = request_end - timedelta(minutes=1)
    ordered_ids = [f"trace-{index}" for index in reversed(range(4))]
    classifier_batches = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        seed_returned = False

        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "WITNESS":
                if Analytics.seed_returned:
                    return SimpleNamespace(data=[], columns=[], query_time_ms=1)
                Analytics.seed_returned = True
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id, "start_time": checkpoint}
                        for trace_id in ordered_ids
                    ],
                    columns=[],
                    query_time_ms=1,
                )
            if len(params["ids"]) > 1:
                raise classifier_error
            return SimpleNamespace(
                data=[{"trace_id": params["ids"][0]}],
                columns=["trace_id"],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 10)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 4)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == ordered_ids
    assert classifier_batches == [
        tuple(ordered_ids),
        tuple(ordered_ids[:2]),
        (ordered_ids[0],),
        (ordered_ids[1],),
        (ordered_ids[2],),
        (ordered_ids[3],),
    ]
    assert query_count == 7
    assert rows_returned == 8


@pytest.mark.unit
def test_exact_trace_membership_learns_classifier_ceiling_per_refresh(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    checkpoint = request_end - timedelta(minutes=1)
    ordered_ids = [f"trace-{index}" for index in reversed(range(8))]
    classifier_batches = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        def __init__(self):
            self.seed_pages = iter(
                [
                    [
                        {"trace_id": trace_id, "start_time": checkpoint}
                        for trace_id in ordered_ids[:4]
                    ],
                    [
                        {"trace_id": trace_id, "start_time": checkpoint}
                        for trace_id in ordered_ids[4:]
                    ],
                    [],
                ]
            )

        def execute_ch_query(self, query, params, **_kwargs):
            if query == "WITNESS":
                return SimpleNamespace(
                    data=next(self.seed_pages), columns=[], query_time_ms=1
                )
            if len(params["ids"]) > 2:
                raise ServerException("private bounded classifier timeout", code=159)
            return SimpleNamespace(
                data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                columns=["trace_id"],
                query_time_ms=1,
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 4)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 4)

    expected_batch_sizes = [4, 2, 2, 2, 2]
    for _refresh in range(2):
        classifier_batches.clear()
        trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )

        assert trace_ids == ordered_ids
        assert [len(batch) for batch in classifier_batches] == expected_batch_sizes
        assert query_count == 8
        assert rows_returned == 16


@pytest.mark.unit
def test_exact_trace_membership_fails_closed_when_one_identity_exceeds_classifier_budget(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=5)
    checkpoint = request_end - timedelta(minutes=1)

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**_kwargs):
            return "WITNESS", {}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            return "CLASSIFY", {"ids": tuple(row["trace_id"] for row in rows)}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, _params, **_kwargs):
            if query == "WITNESS":
                return SimpleNamespace(
                    data=[{"trace_id": "trace-1", "start_time": checkpoint}],
                    columns=[],
                    query_time_ms=1,
                )
            raise ServerException("private bounded classifier timeout", code=159)

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 10)

    with pytest.raises(ServerException, match="private bounded classifier timeout"):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )


@pytest.mark.unit
def test_exact_trace_membership_deduplicates_across_slices_and_classifies_latest_state(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=20)
    classified_batches = []
    ordered_seed_calls = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            ordered_seed_calls.append((kwargs["slice_start"], kwargs["slice_end"]))
            return "WITNESS", {"slice_end": kwargs["slice_end"]}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classified_batches.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "CLASSIFY":
                # ``trace-stale`` is a raw necessary witness whose latest row
                # is tombstoned or fails another filter. The exact classifier,
                # not the seed, remains the membership authority.
                return SimpleNamespace(
                    data=[
                        {"trace_id": trace_id}
                        for trace_id in params["ids"]
                        if trace_id != "trace-stale"
                    ],
                    columns=["trace_id"],
                )
            minute = int((params["slice_end"] - request_start).total_seconds() / 60)
            pages = {
                20: [
                    {"trace_id": "trace-stale", "start_time": request_end},
                    {"trace_id": "trace-kept", "start_time": request_end},
                ],
                15: [
                    {"trace_id": "trace-stale", "start_time": request_end},
                    {"trace_id": "trace-other", "start_time": request_end},
                ],
                10: [],
                5: [{"trace_id": "trace-kept", "start_time": request_start}],
            }
            return SimpleNamespace(
                data=pages[minute],
                columns=[],
                query_time_ms=(exact_module.EXACT_GRAPH_TRACE_GROWTH_QUERY_TIME_MS + 1),
            )

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 10)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CLASSIFY_BATCH_SIZE", 10)

    trace_ids, query_count, rows_returned = _enumerate_exact_trace_ids(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(request_start, request_end),
        annotation_label_ids=None,
        started=exact_module.monotonic(),
    )

    assert trace_ids == ["trace-kept", "trace-other"]
    assert classified_batches == [
        ("trace-stale", "trace-kept"),
        ("trace-other",),
    ]
    assert query_count == 6
    assert rows_returned == 7
    assert len(ordered_seed_calls) == 4
    assert all(
        request_start <= slice_start < slice_end <= request_end
        for slice_start, slice_end in ordered_seed_calls
    )


@pytest.mark.unit
def test_exact_trace_membership_fails_closed_after_partial_deduplicated_walk(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    request_start = datetime(2026, 8, 1)
    request_end = request_start + timedelta(minutes=10)
    classifier_calls = []

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def supports_bounded_filter_scan():
            return True

        @staticmethod
        def parse_time_range(_filters):
            return request_start, request_end

        @staticmethod
        def exact_graph_filter_witness_range():
            return request_start, request_end

        @staticmethod
        def bounded_filter_seed_identity(row):
            return row["trace_id"]

        @staticmethod
        def bounded_filter_seed_order_token(row):
            return row["trace_id"]

        @staticmethod
        def build_filter_ordered_seed_page(**kwargs):
            return "WITNESS", {"slice_end": kwargs["slice_end"]}

        @staticmethod
        def build_filter_identity_match_query_from_seed_rows(rows):
            ids = tuple(row["trace_id"] for row in rows)
            classifier_calls.append(ids)
            return "CLASSIFY", {"ids": ids}

    class Analytics:
        @staticmethod
        def execute_ch_query(query, params, **_kwargs):
            if query == "CLASSIFY":
                return SimpleNamespace(
                    data=[{"trace_id": trace_id} for trace_id in params["ids"]],
                    columns=["trace_id"],
                )
            if params["slice_end"] == request_end:
                return SimpleNamespace(
                    data=[{"trace_id": "trace-new", "start_time": request_end}],
                    columns=[],
                )
            raise RuntimeError("later witness failed")

    monkeypatch.setattr(exact_module, "TraceListQueryBuilderV2", Builder)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_SELECTOR_PAGE_SIZE", 10)

    with pytest.raises(RuntimeError, match="later witness failed"):
        _enumerate_exact_trace_ids(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(request_start, request_end),
            annotation_label_ids=None,
            started=exact_module.monotonic(),
        )

    # A proven early batch must never be returned/published when a later
    # required slice fails.
    assert classifier_calls == [("trace-new",)]


@pytest.mark.unit
def test_exact_trace_graph_zero_membership_issues_no_contribution_query(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(
        exact_module,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: ([], 2, 0),
    )

    class Analytics:
        @staticmethod
        def execute_ch_query(*_args, **_kwargs):
            pytest.fail("zero membership must not issue a contribution query")

    result = read_exact_system_graph(
        analytics=Analytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(datetime(2026, 8, 1), datetime(2026, 8, 4)),
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )

    assert result["query_count"] == 2
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert all(point["value"] == 0 for point in result["data"])


@pytest.mark.unit
def test_exact_trace_graph_merges_all_contribution_batches_before_averages(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    bucket = datetime(2026, 8, 1)
    selected = [
        {"trace_id": "trace-3", "start_time": datetime(2026, 8, 3)},
        {"trace_id": "trace-2", "start_time": datetime(2026, 8, 2)},
        {"trace_id": "trace-1", "start_time": datetime(2026, 8, 1)},
    ]
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 2)
    monkeypatch.setattr(
        exact_module,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: ([row["trace_id"] for row in selected], 4, len(selected)),
    )

    class Analytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, dict(params), timeout_ms, dict(settings)))
            count = len(params["graph_candidate_trace_ids"])
            return SimpleNamespace(
                data=[
                    {
                        "time_bucket": bucket,
                        "latency_sum": 10 * count,
                        "total_tokens": 7 * count,
                        "cost_sum": Decimal("0.10") * count,
                        "traffic_count": count,
                        "prompt_tokens": 4 * count,
                        "completion_tokens": 3 * count,
                        "error_count": 1,
                    }
                ],
                columns=[
                    "time_bucket",
                    "latency_sum",
                    "total_tokens",
                    "cost_sum",
                    "traffic_count",
                    "prompt_tokens",
                    "completion_tokens",
                    "error_count",
                ],
            )

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(datetime(2026, 8, 1), datetime(2026, 8, 4)),
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    assert len(analytics.calls) == 2
    assert [call[1]["graph_candidate_trace_ids"] for call in analytics.calls] == [
        ("trace-3", "trace-2"),
        ("trace-1",),
    ]
    assert all(
        "trace_id IN %(graph_candidate_trace_ids)s" in call[0]
        for call in analytics.calls
    )
    assert all(
        call[2] <= exact_module.EXACT_GRAPH_TRACE_CONTRIBUTION_QUERY_TIMEOUT_MS
        for call in analytics.calls
    )
    assert all(
        call[3]["max_bytes_to_read"]
        == exact_module.EXACT_GRAPH_TRACE_CONTRIBUTION_MAX_BYTES_TO_READ
        for call in analytics.calls
    )
    # final_status and confidence establish trace membership on possibly
    # different siblings; they are intentionally absent from the all-live-span
    # contribution scan once that identity membership has been proven.
    assert all("attrs_string" not in call[0] for call in analytics.calls)
    observed = next(point for point in result["data"] if point["value"])
    assert observed["value"] == 10
    assert observed["primary_traffic"] == 3
    assert result["query_count"] == 6
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_trace_contribution_builder_accepts_5k_and_rejects_larger_batch():
    from tracer.services.clickhouse.query_builders.time_series import (
        TimeSeriesQueryBuilder,
    )

    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 2)
    builder = TimeSeriesQueryBuilder(
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="day",
        exact_snapshot=True,
        observe_type="trace",
        start_date=start,
        end_date=end,
    )
    trace_ids = [f"trace-{index:04d}" for index in range(5_001)]

    query, params = builder.build_exact_trace_contribution_batch(trace_ids[:5_000])

    assert query
    assert "toDecimal128(toString(ifNull(cost, 0.0)), 18)" in query
    assert len(params["graph_candidate_trace_ids"]) == 5_000
    with pytest.raises(ValueError, match="exceeds 5000 identities"):
        builder.build_exact_trace_contribution_batch(trace_ids)


@pytest.mark.unit
@pytest.mark.parametrize(
    "contribution_error",
    [
        ServerException("private detail", code=159),
        ServerException("Max query size exceeded at position 262133", code=62),
    ],
)
def test_exact_trace_graph_bisects_retryable_contribution_without_gaps(
    monkeypatch,
    contribution_error,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    bucket = datetime(2026, 8, 1)
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 4)
    monkeypatch.setattr(
        exact_module,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: ([f"trace-{index}" for index in range(4)], 0, 4),
    )

    class Analytics:
        def __init__(self):
            self.batch_sizes = []

        def execute_ch_query(self, _query, params, **_kwargs):
            batch_size = len(params["graph_candidate_trace_ids"])
            self.batch_sizes.append(batch_size)
            if batch_size > 2:
                raise contribution_error
            return SimpleNamespace(
                data=[
                    {
                        "time_bucket": bucket,
                        "traffic_count": batch_size,
                    }
                ],
                columns=["time_bucket", "traffic_count"],
            )

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(bucket, bucket + timedelta(days=1)),
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )

    assert analytics.batch_sizes == [4, 2, 2]
    assert result["query_count"] == 3
    assert next(point for point in result["data"] if point["value"])["value"] == 4


@pytest.mark.unit
def test_exact_trace_decimal_cost_merge_is_batch_size_and_order_invariant():
    bucket = datetime(2026, 8, 1)
    columns = ["time_bucket", "cost_sum", "traffic_count"]
    costs = [
        Decimal("0.100000000000000001"),
        Decimal("0.200000000000000002"),
        Decimal("0.300000000000000003"),
    ]
    one_batch = [
        (
            [
                {
                    "time_bucket": bucket,
                    "cost_sum": sum(costs, Decimal(0)),
                    "traffic_count": len(costs),
                }
            ],
            columns,
        )
    ]
    reversed_singleton_batches = [
        (
            [
                {
                    "time_bucket": bucket,
                    "cost_sum": cost,
                    "traffic_count": 1,
                }
            ],
            columns,
        )
        for cost in reversed(costs)
    ]

    merged_one, merged_columns = _merge_exact_trace_contribution_rows(one_batch)
    merged_many, many_columns = _merge_exact_trace_contribution_rows(
        reversed_singleton_batches
    )

    assert merged_one == merged_many
    assert merged_columns == many_columns
    assert merged_one[0]["avg_cost"] == float(sum(costs, Decimal(0)) / len(costs))


@pytest.mark.unit
def test_exact_trace_graph_delegates_scalar_array_map_and_json_membership(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    captured_filters = []

    def enumerate_ids(**kwargs):
        captured_filters.extend(kwargs["filters"])
        return ["trace-1"], 1, 1

    monkeypatch.setattr(exact_module, "_enumerate_exact_trace_ids", enumerate_ids)

    class Analytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, dict(params), timeout_ms, dict(settings)))
            return SimpleNamespace(data=[], columns=[])

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(datetime(2026, 8, 1), datetime(2026, 8, 4)),
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )

    assert [item["column_id"] for item in captured_filters] == [
        "final_status",
        "tags",
        "profile",
        "legacy_payload",
        "start_time",
    ]
    assert [item["filter_config"]["filter_type"] for item in captured_filters[:-1]] == [
        "text",
        "array",
        "map",
        "json",
    ]
    query, params, _timeout, _settings = analytics.calls[0]
    assert params["graph_candidate_trace_ids"] == ("trace-1",)
    assert "trace_id IN %(graph_candidate_trace_ids)s" in query
    assert "attrs_string" not in query
    assert "attrs_number" not in query
    assert "attrs_bool" not in query
    assert "attributes_extra" not in query
    assert result["query_count"] == 2
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_trace_contribution_merge_preserves_additive_cost_and_error_state():
    bucket = datetime(2026, 8, 1)
    rows, columns = _merge_exact_trace_contribution_rows(
        [
            (
                [
                    {
                        "time_bucket": bucket,
                        "latency_sum": 30,
                        "total_tokens": 9,
                        "cost_sum": Decimal("0.30"),
                        "traffic_count": 2,
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "error_count": 1,
                    }
                ],
                [
                    "time_bucket",
                    "latency_sum",
                    "total_tokens",
                    "cost_sum",
                    "traffic_count",
                    "prompt_tokens",
                    "completion_tokens",
                    "error_count",
                ],
            ),
            (
                [
                    {
                        "time_bucket": bucket,
                        "latency_sum": 30,
                        "total_tokens": 6,
                        "cost_sum": Decimal("0.15"),
                        "traffic_count": 1,
                        "prompt_tokens": 4,
                        "completion_tokens": 2,
                        "error_count": 1,
                    }
                ],
                [
                    "time_bucket",
                    "latency_sum",
                    "total_tokens",
                    "cost_sum",
                    "traffic_count",
                    "prompt_tokens",
                    "completion_tokens",
                    "error_count",
                ],
            ),
        ]
    )

    assert columns[-1] == "error_rate"
    assert rows == [
        {
            "time_bucket": bucket,
            "avg_latency": 20,
            "total_tokens": 15,
            "avg_cost": 0.15,
            "traffic_count": 3,
            "prompt_tokens": 9,
            "completion_tokens": 6,
            "error_rate": pytest.approx(200 / 3),
        }
    ]


@pytest.mark.unit
def test_exact_contribution_merge_fails_closed_above_final_row_limit(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_MAX_RESULT_ROWS", 1)
    columns = [
        "time_bucket",
        "latency_sum",
        "total_tokens",
        "cost_sum",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_count",
    ]
    rows = [
        {
            "time_bucket": datetime(2026, 8, 1, hour),
            "traffic_count": 1,
        }
        for hour in (0, 1)
    ]

    with pytest.raises(ExactGraphReadError, match="bounded row limit"):
        _merge_exact_trace_contribution_rows([(rows, columns)])


@pytest.mark.unit
def test_exact_contribution_merge_fails_closed_above_final_byte_limit(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_MAX_RESULT_BYTES", 1)
    columns = [
        "time_bucket",
        "latency_sum",
        "total_tokens",
        "cost_sum",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_count",
    ]

    with pytest.raises(ExactGraphReadError, match="bounded byte limit"):
        _merge_exact_trace_contribution_rows(
            [
                (
                    [{"time_bucket": datetime(2026, 8, 1), "traffic_count": 1}],
                    columns,
                )
            ]
        )


@pytest.mark.unit
def test_exact_trace_graph_discards_all_batches_when_any_contribution_fails(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    monkeypatch.setattr(exact_module, "EXACT_GRAPH_TRACE_CONTRIBUTION_BATCH_SIZE", 1)
    monkeypatch.setattr(
        exact_module,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: (["trace-2", "trace-1"], 1, 2),
    )

    class Analytics:
        def __init__(self):
            self.calls = 0

        def execute_ch_query(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ServerException("private detail", code=159)
            return SimpleNamespace(data=[], columns=[])

    with pytest.raises(ServerException):
        read_exact_system_graph(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(datetime(2026, 8, 1), datetime(2026, 8, 4)),
            interval="day",
            metric_id="traffic",
            observe_type="trace",
        )


@pytest.mark.unit
def test_exact_span_graph_merges_additive_partitions_only_after_all_succeed():
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    start = datetime(2026, 8, 1)
    end = start + timedelta(minutes=12)
    bucket = start
    columns = [
        "time_bucket",
        "latency_sum",
        "total_tokens",
        "cost_sum",
        "traffic_count",
        "prompt_tokens",
        "completion_tokens",
        "error_count",
    ]

    class Analytics:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            self.calls.append((query, dict(params), timeout_ms, dict(settings)))
            return SimpleNamespace(
                data=[
                    {
                        "time_bucket": bucket,
                        "latency_sum": 30,
                        "total_tokens": 9,
                        "cost_sum": Decimal("0.30"),
                        "traffic_count": 2,
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "error_count": 1,
                    }
                ],
                columns=columns,
            )

    analytics = Analytics()
    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(start, end),
        interval="minute",
        metric_id="latency",
        observe_type="span",
    )

    assert len(analytics.calls) == 1
    assert [
        (
            call[1]["graph_partition_start"],
            call[1]["graph_partition_end"],
        )
        for call in analytics.calls
    ] == [
        (start, start + timedelta(hours=1)),
    ]
    assert all(
        0 < call[2] <= exact_module.EXACT_GRAPH_SPAN_PARTITION_QUERY_TIMEOUT_MS
        for call in analytics.calls
    )
    assert all(call[3]["max_threads"] == 1 for call in analytics.calls)
    assert all("max_rows_to_read" not in call[3] for call in analytics.calls)
    assert all(
        call[3]["max_bytes_to_read"] == exact_module.EXACT_GRAPH_MAX_BYTES_TO_READ
        for call in analytics.calls
    )
    nonzero = [point for point in result["data"] if point["value"]]
    assert nonzero == [
        {
            "timestamp": start.isoformat(),
            "value": 15.0,
            "primary_traffic": 2,
        }
    ]
    assert result["query_count"] == 1
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_span_graph_fails_closed_before_merging_a_partial_partition(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    start = datetime(2026, 8, 1)
    end = start + timedelta(hours=2)

    class Analytics:
        calls = 0

        @classmethod
        def execute_ch_query(cls, *_args, **_kwargs):
            cls.calls += 1
            if cls.calls == 2:
                raise ServerException("private detail", code=159)
            return SimpleNamespace(data=[], columns=[])

    monkeypatch.setattr(
        exact_module,
        "_merge_exact_trace_contribution_rows",
        lambda _batches: pytest.fail("partial partitions must not be merged"),
    )

    with pytest.raises(ServerException):
        read_exact_system_graph(
            analytics=Analytics(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(start, end),
            interval="minute",
            metric_id="traffic",
            observe_type="span",
        )

    assert Analytics.calls == 2


@pytest.mark.unit
def test_exact_all_system_metrics_uses_one_readonly_statement():
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 5)

    result = read_exact_all_system_metrics(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="day",
    )

    query, params, settings = analytics.partition_calls[0]
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "AS latest_spans" not in query
    assert "OVER (PARTITION BY trace_id)" not in query
    assert "attrs_string" in query and "attrs_number" in query
    assert "snapshot_version_ceiling" not in params
    assert "additional_table_filters" not in settings
    assert len(analytics.partition_calls) == 1
    assert result["query_count"] == 1
    assert "query_snapshot_version_ceiling" not in result
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_system_graph_empty_datetime_domain_issues_no_clickhouse_query(
    observe_type,
):
    analytics = _ConcurrentArrivalAnalytics()

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "is_null",
                    "filter_value": None,
                },
            }
        ],
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )

    assert analytics.partition_calls == []
    assert result["query_count"] == 0
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_type", "filter_op", "filter_value", "expected_type", "negated"),
    [
        ("array", "is_null", None, "Array", True),
        ("array", "is_not_null", None, "Array", False),
        ("map", "is_null", None, "Object", True),
        ("map", "is_not_null", None, "Object", False),
        ("json", "is_null", None, "Object", True),
    ],
)
def test_exact_structured_null_domain_covers_missing_null_and_type_mismatch(
    filter_type,
    filter_op,
    filter_value,
    expected_type,
    negated,
):
    # A legacy json null filter is value-sensitive. Use an object-shaped value
    # hint so the compatibility path selects the map domain.
    if filter_type == "json":
        filter_value = {}
    clause, params = compile_exact_graph_filter_predicates(
        [
            {
                "column_id": "payload",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": filter_type,
                    "filter_op": filter_op,
                    "filter_value": filter_value,
                },
            }
        ],
        project_id="11111111-1111-4111-8111-111111111111",
        observe_type="span",
    )

    assert "JSONHas(attributes_extra" in clause
    assert f"= '{expected_type}'" in clause
    assert ("NOT (" in clause) is negated
    assert params["latest_filter_key_0"] == "payload"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("wants_complete", "expected_clause"),
    [(True, "(1 = 1)"), (False, "(0 = 1)")],
)
def test_exact_membership_preserves_known_empty_annotation_label_set(
    wants_complete,
    expected_clause,
):
    clause, params = compile_exact_graph_filter_predicates(
        [
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": wants_complete,
                },
            }
        ],
        project_id="11111111-1111-4111-8111-111111111111",
        observe_type="trace",
        annotation_label_ids=[],
    )

    assert clause == expected_clause
    assert params == {}


@pytest.mark.unit
def test_exact_graph_budget_failure_does_not_publish_or_split_contribution_batch(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _BudgetSplittingAnalytics()
    start = datetime(2026, 8, 1, 0, 0)
    end = datetime(2026, 8, 1, 4, 0)
    monkeypatch.setattr(
        exact_module,
        "_enumerate_exact_trace_ids",
        lambda **_kwargs: (["trace-1"], 0, 0),
    )

    with pytest.raises(ServerException):
        read_exact_system_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(start, end),
            interval="hour",
            metric_id="traffic",
            observe_type="trace",
        )

    assert len(analytics.partition_calls) == 1
    query, params, timeout, settings = analytics.partition_calls[0]
    assert (params["start_date"], params["end_date"]) == (start, end)
    assert "attrs_string" not in query and "attrs_number" not in query
    assert "trace_id IN %(graph_candidate_trace_ids)s" in query
    assert "snapshot_version_ceiling" not in params
    assert "additional_table_filters" not in settings
    assert 0 < timeout <= exact_module.EXACT_GRAPH_TRACE_CONTRIBUTION_QUERY_TIMEOUT_MS
    assert (
        settings["max_bytes_to_read"]
        == exact_module.EXACT_GRAPH_TRACE_CONTRIBUTION_MAX_BYTES_TO_READ
    )


@pytest.mark.unit
def test_public_filtered_graph_runs_direct_raw_reader_inline_without_scheduling():
    from tracer.services.clickhouse import graph_dispatch

    cache.clear()

    start = datetime(2026, 8, 1, 0, 0)
    end = datetime(2026, 8, 8, 0, 0)
    exact_calls = []

    def direct_read(**kwargs):
        exact_calls.append(kwargs)
        return {
            "metric_name": "traffic",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
            "query_exact": False,
            "query_provenance": "bounded_candidates",
        }

    with patch.object(
        graph_dispatch,
        "_fetch_direct_raw_system_metric_graph",
        side_effect=direct_read,
    ):
        result = graph_dispatch.fetch_system_metric_graph_ch(
            analytics=object(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(start, end),
            interval="day",
            metric_id="traffic",
            observe_type="trace",
            timeout_ms=30_000,
        )

    assert result["query_status"] == "complete"
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert result["query_exact"] is False
    assert result["query_provenance"] == "bounded_candidates"
    assert exact_calls[0]["filters"] == _exact_multi_filters(start, end)


@pytest.mark.unit
def test_exact_graph_does_not_retry_programming_errors():
    analytics = _BudgetSplittingAnalytics(error_code=62)

    with pytest.raises(ServerException):
        read_exact_system_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(
                datetime(2026, 8, 1, 0, 0),
                datetime(2026, 8, 1, 4, 0),
            ),
            interval="hour",
            metric_id="traffic",
            observe_type="trace",
        )

    assert len(analytics.partition_calls) == 1


@pytest.mark.unit
def test_filtered_exact_span_window_uses_hour_aligned_additive_statements():
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 1, 1)
    end = start + timedelta(minutes=12)

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[
            _time_filter(start, end),
            {
                "column_id": "model",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4",
                    "col_type": "SYSTEM_METRIC",
                },
            },
        ],
        interval="day",
        metric_id="traffic",
        observe_type="span",
    )

    assert len(analytics.partition_calls) == 1
    query, params, settings = analytics.partition_calls[0]
    assert "additional_table_filters" not in settings
    assert "snapshot_version_ceiling" not in params
    assert "trace_id IN" not in query
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "OVER (PARTITION BY trace_id) AS graph_match_" not in query
    assert "AS graph_bucket_match_" not in query
    assert (params["start_date"], params["end_date"]) == (start, end)
    assert (params["graph_partition_start"], params["graph_partition_end"]) == (
        start,
        start + timedelta(hours=1),
    )
    assert (params["graph_contribution_start"], params["graph_contribution_end"]) == (
        start,
        end,
    )
    assert "AS latency_sum" in query
    assert "AS cost_sum" in query
    assert settings["max_threads"] == 1
    assert "max_rows_to_read" not in settings
    assert settings["max_bytes_to_read"] == EXACT_GRAPH_MAX_BYTES_TO_READ
    assert result["query_count"] == 1
    assert "query_snapshot_version_ceiling" not in result
    assert result["query_complete"] is True
    assert result["query_status"] == "complete"
    assert result["query_sampled"] is False


class _ExactEntityAnalytics:
    def __init__(self):
        self.main_calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "toUnixTimestamp64Nano(now64" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        if "max(toUnixTimestamp64Micro(version))" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 901}],
                columns=["version_ceiling"],
            )
        self.main_calls.append((query, dict(params), dict(settings)))
        return SimpleNamespace(
            data=[],
            columns=["time_bucket", "value", "primary_traffic"],
        )


class _EntityBudgetSplittingAnalytics:
    def __init__(self, *, always_fail=False):
        self.always_fail = always_fail
        self.main_calls = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "toUnixTimestamp64Nano(now64" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        if "max(toUnixTimestamp64Micro(version))" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 901}],
                columns=["version_ceiling"],
            )
        if "max(_peerdb_version)" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 701}],
                columns=["version_ceiling"],
            )
        self.main_calls.append((query, dict(params), timeout_ms, dict(settings)))
        width = (params["end_date"] - params["start_date"]).total_seconds()
        if self.always_fail or width > 3600:
            raise ServerException("private budget detail", code=159)
        if "uniqExact(end_user_id) AS active_users" in query:
            row = {
                "time_bucket": params["start_date"],
                "avg_latency": 1,
                "total_tokens": 1,
                "avg_cost": 1,
                "traffic_count": 1,
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "error_rate": 0,
                "active_users": 1,
                "total_cost_sum": 1,
                "avg_cost_per_user": 1,
                "avg_traces_per_user": 1,
                "total_tokens_sum": 1,
            }
        else:
            row = {
                "time_bucket": params["start_date"],
                "value": 1,
                "primary_traffic": 1,
            }
        return SimpleNamespace(
            data=[row],
            columns=list(row),
        )


def _assert_entity_output_partitions(calls, start, end):
    """Assert one current-state statement covers the complete entity window."""

    assert len(calls) == 1
    _query, params, settings = calls[0]
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert "additional_table_filters" not in settings


@pytest.mark.unit
def test_direct_exact_graph_readers_share_deadline_and_fence_final_publication(
    monkeypatch,
):
    import inspect

    from tracer.services.clickhouse import exact_graph_reads as exact_module

    direct_readers = (
        exact_module.read_exact_system_graph,
        exact_module.read_exact_agent_graph,
        exact_module.read_exact_all_system_metrics,
        exact_module.read_exact_eval_graph,
        exact_module.read_exact_annotation_graph,
        exact_module.read_exact_user_system_graph,
        exact_module.read_exact_session_system_graph,
    )
    for reader in direct_readers:
        assert "_finalize_exact_graph_payload" in inspect.getsource(reader)

    clock = {"value": 0.0}
    observed_timeouts: list[int] = []

    class SlowPoints:
        def __iter__(self):
            clock["value"] = exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS / 1000 + 0.001
            return iter(())

    class Builder:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def build():
            clock["value"] = 5.0
            return "SELECT 1", {}

        @staticmethod
        def format_result(_rows, _columns):
            return {"latency": SlowPoints(), "traffic": []}

    class Analytics:
        @staticmethod
        def execute_ch_query(_query, _params, *, timeout_ms, settings):
            del settings
            observed_timeouts.append(timeout_ms)
            return SimpleNamespace(data=[], columns=[])

    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    monkeypatch.setattr(exact_module, "TimeSeriesQueryBuilder", Builder)
    monkeypatch.setattr(exact_module, "monotonic", lambda: clock["value"])

    with pytest.raises(ExactGraphReadError, match="deadline exceeded"):
        read_exact_system_graph(
            analytics=Analytics(),
            project_id="22222222-2222-4222-8222-222222222222",
            filters=[_time_filter(start, end)],
            interval="day",
            metric_id="latency",
            observe_type="span",
        )

    assert observed_timeouts == [exact_module.EXACT_GRAPH_WALL_DEADLINE_MS - 5_000]


@pytest.mark.unit
@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_system_graph_does_not_stitch_budget_failed_statements(
    aggregation_context,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _EntityBudgetSplittingAnalytics()
    start = datetime(2026, 8, 1, 0)
    end = datetime(2026, 8, 1, 4)
    common = {
        "analytics": analytics,
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": [_time_filter(start, end)],
        "interval": "hour",
    }

    with pytest.raises(ServerException, match="private budget detail"):
        if aggregation_context == "session":
            read_exact_session_system_graph(**common, metric_id="session_count")
        else:
            read_exact_user_system_graph(**common, metric_id="active_users")

    assert len(analytics.main_calls) == 1
    _query, params, timeout_ms, settings = analytics.main_calls[0]
    assert (params["start_date"], params["end_date"]) == (start, end)
    assert 0 < timeout_ms <= exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS
    assert "additional_table_filters" not in settings


@pytest.mark.unit
@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_system_graph_indivisible_budget_failure_is_fail_closed(
    aggregation_context,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _EntityBudgetSplittingAnalytics(always_fail=True)
    start = datetime(2026, 8, 1, 0)
    end = datetime(2026, 8, 1, 1)
    common = {
        "analytics": analytics,
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": [_time_filter(start, end)],
        "interval": "hour",
    }

    with pytest.raises(ServerException):
        if aggregation_context == "session":
            read_exact_session_system_graph(**common, metric_id="session_count")
        else:
            read_exact_user_system_graph(**common, metric_id="active_users")

    assert len(analytics.main_calls) == 1
    assert 0 < analytics.main_calls[0][2] <= exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS


@pytest.mark.unit
def test_exact_session_graph_combines_native_session_and_aggregate_filters():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    session_id = "11111111-1111-4111-8111-111111111111"
    filters = [
        _time_filter(start, end),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
        {
            "column_id": "session_id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": [session_id],
            },
        },
        {
            "column_id": "duration",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "greater_than_or_equal",
                "filter_value": 5,
            },
        },
        {
            "column_id": "total_cost",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "less_than",
                "filter_value": 10,
            },
        },
        {
            "column_id": "first_message",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "hello",
            },
        },
        {
            "column_id": "last_message",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "is_not_null",
                "filter_value": None,
            },
        },
    ]

    result = read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        metric_id="session_count",
    )

    # The full exact range is evaluated by one current-state statement.
    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, _settings = analytics.main_calls[0]
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert params["exact_session_id_1"] == (session_id,)
    assert params["session_having_1"] == 5
    assert params["session_having_2"] == 10
    assert params["session_having_3"] == "%hello%"
    assert "session_duration >= %(session_having_1)s" in query
    assert "session_start >= %(start_date)s" in query
    assert "candidate_physical_session_ids AS" in query
    assert "candidate_session_remap_target_new_ids AS" in query
    assert "candidate_sessions AS" in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "session_total_cost < %(session_having_2)s" in query
    assert "argMin(rs.input, rs.start_time) AS first_message" in query
    assert "argMax(rs.input, rs.start_time) AS last_message" in query
    assert "first_message ILIKE %(session_having_3)s" in query
    assert "(last_message IS NOT NULL AND last_message != '')" in query
    assert "span_attr_str['first_message']" not in query
    assert "span_attr_str['last_message']" not in query
    assert "rs.trace_session_id, ts_remap.survivor_id) IN" in query
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_session_scalar_filters_intersect_after_session_membership():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1, 0, 2)
    end = datetime(2026, 3, 15, 3, 4)
    filters = [
        _time_filter(start, end),
        {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "ERROR",
            },
        },
        {
            "column_id": "customer_tier",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "gold",
            },
        },
        {
            "column_id": "region",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "west",
            },
        },
    ]

    read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        metric_id="total_tokens",
    )

    query, params, _settings = analytics.main_calls[0]
    candidate_sql = query.split("candidate_sessions AS (", 1)[1].split(
        "),\n    latest_session_filter_spans AS (", 1
    )[0]
    matching_sql = query.split("latest_session_filter_spans AS (", 1)[1].split(
        "),\n    selected_sessions AS (", 1
    )[0]
    hydration_sql = query.split("selected_sessions AS (", 1)[1].split(
        "\n    SELECT\n", 1
    )[1]
    hydration_sql = hydration_sql.split(") AS exact_sessions", 1)[0]

    # Candidate discovery is entity-safe but filter-free. Each leaf is tested
    # independently over all traces in the candidate session, so three
    # different sibling traces can establish the three-way intersection.
    assert "session_scalar_" not in candidate_sql
    assert matching_sql.count("countIf(") == 3
    assert "GROUP BY session_id" in matching_sql
    assert "HAVING countIf(" in matching_sql
    assert "SELECT session_id FROM selected_sessions" in hydration_sql

    # Hydration is deliberately free of the selection predicates: the metric
    # sums every live root in the selected session, including non-matching
    # sibling traces. Per-leaf namespacing also prevents equal compiler-local
    # parameter names from overwriting each other.
    assert "session_scalar_" not in hydration_sql
    assert params["session_scalar_latest_filter_param_0"] == "error"
    assert params["session_scalar_latest_filter_param_1"] == "gold"
    assert params["session_scalar_latest_filter_param_2"] == "west"
    assert params["snapshot_scan_start_date"] == datetime(2026, 1, 1)
    assert params["snapshot_scan_end_date"] == datetime(2026, 3, 15, 4)
    assert "start_time >= %(snapshot_scan_start_date)s" in matching_sql
    assert "start_time < %(snapshot_scan_end_date)s" in matching_sql
    assert "latest_start_time >= %(snapshot_start_date)s" in matching_sql
    assert "latest_start_time < %(snapshot_end_date)s" in matching_sql
    assert "%(start_date)s" not in matching_sql
    assert "%(end_date)s" not in matching_sql


@pytest.mark.unit
def test_exact_session_system_graph_supports_array_map_and_legacy_json_filters():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)

    result = read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=_exact_structured_filters(start, end),
        interval="day",
        metric_id="session_count",
    )

    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, settings = analytics.main_calls[0]
    assert "latest_session_filter_spans AS" in query
    assert query.count("countIf(") >= 4
    assert "argMax(start_time, _version) AS latest_start_time" in query
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in query
    assert "WHERE latest_is_deleted = 0" in query
    assert (
        "GROUP BY\n            project_id,\n            observation_type,\n"
        "            service_name,\n            toStartOfHour(start_time),\n"
        "            trace_id,\n            id" in query
    )
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_session_graph_freezes_combined_filter_relations(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger", "deleted = 0"),
    )

    result = read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=_combined_relation_filters(start, end),
        interval="day",
        metric_id="session_count",
    )

    assert analytics.capture_calls == []
    _assert_entity_output_partitions(analytics.main_calls, start, end)
    assert "additional_table_filters" not in analytics.main_calls[0][2]
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column_id", "filter_op", "filter_value", "expected_sql", "expected_param"),
    [
        (
            "first_message",
            "equals",
            "hello",
            "first_message = %(session_having_1)s",
            "hello",
        ),
        (
            "first_message",
            "not_equals",
            "hello",
            "first_message != %(session_having_1)s",
            "hello",
        ),
        (
            "first_message",
            "contains",
            "hello",
            "first_message ILIKE %(session_having_1)s",
            "%hello%",
        ),
        (
            "last_message",
            "not_contains",
            "bye",
            "last_message NOT ILIKE %(session_having_1)s",
            "%bye%",
        ),
        (
            "first_message",
            "starts_with",
            "hello",
            "first_message ILIKE %(session_having_1)s",
            "hello%",
        ),
        (
            "last_message",
            "ends_with",
            "bye",
            "last_message ILIKE %(session_having_1)s",
            "%bye",
        ),
        (
            "first_message",
            "is_null",
            None,
            "(first_message IS NULL OR first_message = '')",
            None,
        ),
        (
            "last_message",
            "is_not_null",
            None,
            "(last_message IS NOT NULL AND last_message != '')",
            None,
        ),
        # Keep the same fail-closed behavior as SessionListQueryBuilderV2 for
        # message operators it does not support.
        ("first_message", "in", ["hello", "bye"], "0 = 1", None),
    ],
)
def test_exact_session_message_filters_match_session_list_having_semantics(
    column_id,
    filter_op,
    filter_value,
    expected_sql,
    expected_param,
):
    analytics = _ExactEntityAnalytics()
    filters = [
        _time_filter(datetime(2026, 1, 1), datetime(2026, 1, 2)),
        {
            "column_id": column_id,
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": filter_op,
                "filter_value": filter_value,
            },
        },
    ]

    read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="hour",
        metric_id="session_count",
    )

    query, params, _settings = analytics.main_calls[0]
    assert expected_sql in query
    assert "argMin(rs.input, rs.start_time) AS first_message" in query
    assert "argMax(rs.input, rs.start_time) AS last_message" in query
    assert "span_attr_str['first_message']" not in query
    assert "span_attr_str['last_message']" not in query
    if expected_param is None:
        assert "session_having_1" not in params
    else:
        assert params["session_having_1"] == expected_param


@pytest.mark.unit
def test_exact_session_graph_uses_weekly_buckets_beyond_three_months():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 5, 1)

    read_exact_session_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[_time_filter(start, end)],
        interval="day",
        metric_id="session_count",
    )

    query, _params, _settings = analytics.main_calls[0]
    assert "toMonday(session_start) AS time_bucket" in query


class _SessionContextAnalytics(_ExactEntityAnalytics):
    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "toUnixTimestamp64Nano(now64" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 900}],
                columns=["version_ceiling"],
            )
        if "max(toUnixTimestamp64Micro(version))" in query:
            return SimpleNamespace(
                data=[{"version_ceiling": 901}],
                columns=["version_ceiling"],
            )
        self.main_calls.append((query, dict(params), dict(settings)))
        if "SELECT DISTINCT trace_id" in query and params.get("candidate_trace_ids"):
            return SimpleNamespace(
                data=[
                    {"trace_id": trace_id} for trace_id in params["candidate_trace_ids"]
                ],
                columns=["trace_id"],
            )
        return SimpleNamespace(
            data=[],
            columns=["time_bucket", "value", "primary_traffic"],
        )


def _assert_session_membership_sql(query, params, start, end):
    assert "SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id" in query
    assert "AS snapshot_members" in query
    assert "start_time >= %(snapshot_scan_start_date)s" in query
    assert "start_time < %(snapshot_scan_end_date)s" in query
    assert "snapshot_members.start_time >= %(snapshot_start_date)s" in query
    assert "snapshot_members.start_time < %(snapshot_end_date)s" in query
    assert "FROM (" in query and "AS selected_sessions" in query
    assert "argMin(rs.input, rs.start_time) AS first_message" in query
    assert "session_duration >= %(session_having_1)s" in query
    assert "first_message ILIKE %(session_having_2)s" in query
    assert "rs.trace_session_id, ts_remap.survivor_id) IN" in query
    assert "span_attr_str['first_message']" not in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert params["snapshot_scan_start_date"] == start
    assert params["snapshot_scan_end_date"] == end
    assert params["session_having_1"] == 5
    assert params["session_having_2"] == "%hello%"


@pytest.mark.unit
def test_session_eval_graph_partitions_candidates_and_hydrates_full_sessions(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    eval_config_id = "33333333-3333-4333-8333-333333333333"
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    config_qs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        exact_module.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: config_qs,
    )
    # Avoid an unrelated legacy-CDC ceiling query in this SQL contract test.
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger_v2", "is_deleted = 0"),
    )

    filters = [
        *_combined_session_filters(start, end),
        *_exact_structured_filters(start, end)[2:],
    ]
    result = read_exact_eval_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        req_data_config={"id": eval_config_id, "output_type": "SCORE"},
        observe_type="trace",
        aggregation_context="session",
    )

    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, settings = analytics.main_calls[0]
    _assert_session_membership_sql(query, params, start, end)
    assert "candidate_member_session_remap_target_new_ids AS" in query
    assert "candidate_session_remap_target_new_ids AS" in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert "candidate_eval.created_at >= %(start_date)s" in query
    assert "candidate_eval.created_at < %(end_date)s" in query
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_eval_graph_does_not_stitch_budget_failed_statements(
    monkeypatch,
    aggregation_context,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _EntityBudgetSplittingAnalytics()
    start = datetime(2026, 8, 1, 0)
    end = datetime(2026, 8, 1, 4)
    eval_config_id = "33333333-3333-4333-8333-333333333333"
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    config_qs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        exact_module.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: config_qs,
    )

    with pytest.raises(ServerException, match="private budget detail"):
        read_exact_eval_graph(
            analytics=analytics,
            project_id="22222222-2222-4222-8222-222222222222",
            filters=[_time_filter(start, end)],
            interval="hour",
            req_data_config={"id": eval_config_id, "output_type": "SCORE"},
            observe_type="trace",
            aggregation_context=aggregation_context,
        )

    assert len(analytics.main_calls) == 1
    query, params, timeout_ms, settings = analytics.main_calls[0]
    assert (params["start_date"], params["end_date"]) == (start, end)
    assert "candidate_eval.created_at >= %(start_date)s" in query
    assert "candidate_eval.created_at < %(end_date)s" in query
    assert "SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id" in query
    assert 0 < timeout_ms <= exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS
    assert "additional_table_filters" not in settings


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_eval_graph_supports_combined_structured_filters(
    monkeypatch,
    observe_type,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 10)
    eval_config_id = "33333333-3333-4333-8333-333333333333"
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    config_qs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        exact_module.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: config_qs,
    )
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger_v2", "is_deleted = 0"),
    )

    result = read_exact_eval_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=_exact_structured_filters(start, end),
        interval="day",
        req_data_config={"id": eval_config_id, "output_type": "SCORE"},
        observe_type=observe_type,
    )

    assert len(analytics.main_calls) == 1
    query, params, settings = analytics.main_calls[0]
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_eval_reader_uses_one_current_state_statement(
    monkeypatch,
    observe_type,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 4, 15)
    eval_config_id = "33333333-3333-4333-8333-333333333333"
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    config_qs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        exact_module.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: config_qs,
    )
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger", "deleted = 0"),
    )

    result = read_exact_eval_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_combined_relation_filters(start, end),
        interval="day",
        req_data_config={"id": eval_config_id, "output_type": "SCORE"},
        observe_type=observe_type,
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 1
    assert "additional_table_filters" not in analytics.main_calls[0][2]
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


class _ScoreRows:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *_args):
        return self

    def values(self, *_args):
        return self

    def iterator(self, *, chunk_size):
        assert chunk_size > 0
        return iter(self.rows)


class _ScoreManager:
    def __init__(self, row):
        self.row = row

    def filter(self, **kwargs):
        created_at = self.row["created_at"]
        rows = (
            [self.row]
            if kwargs["created_at__gte"] <= created_at < kwargs["created_at__lt"]
            else []
        )
        return _ScoreRows(rows)


class _ScoreListManager:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, **kwargs):
        return _ScoreRows(
            [
                row
                for row in self.rows
                if kwargs["created_at__gte"]
                <= row["created_at"]
                < kwargs["created_at__lt"]
            ]
        )


@pytest.mark.unit
def test_annotation_reader_sets_postgres_readonly_snapshot_and_remaining_timeout(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    statements: list[str] = []
    transaction_state = {"active": False}

    class Atomic:
        def __enter__(self):
            transaction_state["active"] = True

        def __exit__(self, exc_type, exc, traceback):
            transaction_state["active"] = False

    class Cursor:
        def __enter__(self):
            assert transaction_state["active"] is True
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement):
            assert transaction_state["active"] is True
            statements.append(statement)

    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    score = {
        "trace_id": "44444444-4444-4444-8444-444444444441",
        "observation_span_id": None,
        "created_at": datetime(2026, 1, 1, 1),
        "value": {"rating": 4},
    }
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreManager(score)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", lambda: Atomic())
    monkeypatch.setattr(
        exact_module,
        "connection",
        SimpleNamespace(vendor="postgresql", cursor=lambda: Cursor()),
    )
    clock = iter((0.0, 1.25))
    monkeypatch.setattr(exact_module, "monotonic", lambda: next(clock, 1.25))

    result = read_exact_annotation_graph(
        analytics=_RelationSnapshotAnalytics(),
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[_time_filter(start, end)],
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type="trace",
    )

    remaining_timeout_ms = exact_module.EXACT_GRAPH_WALL_DEADLINE_MS - 1_250
    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        f"SET LOCAL statement_timeout = '{remaining_timeout_ms}ms'",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        f"SET LOCAL statement_timeout = '{remaining_timeout_ms}ms'",
    ]
    assert transaction_state["active"] is False
    assert result["query_complete"] is True


@pytest.mark.unit
def test_annotation_slow_empty_postgres_partition_exhausts_shared_deadline(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    label = SimpleNamespace(name="quality", type="numeric")
    clock = {"value": 0.0}
    statements: list[str] = []

    class SlowEmptyRows(_ScoreRows):
        def iterator(self, *, chunk_size):
            assert chunk_size > 0
            clock["value"] = exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS / 1000 + 0.001
            return iter(())

    class SlowEmptyManager:
        @staticmethod
        def filter(**_kwargs):
            return SlowEmptyRows([])

    cursor = SimpleNamespace(execute=lambda statement: statements.append(statement))
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=SlowEmptyManager()),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        exact_module,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            cursor=lambda: nullcontext(cursor),
        ),
    )
    monkeypatch.setattr(exact_module, "monotonic", lambda: clock["value"])

    with pytest.raises(ExactGraphReadError, match="deadline exceeded"):
        read_exact_annotation_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=[_time_filter(start, end)],
            interval="day",
            req_data_config={
                "id": "55555555-5555-4555-8555-555555555555",
                "output_type": "float",
            },
            observe_type="trace",
        )

    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        (
            "SET LOCAL statement_timeout = "
            f"'{exact_module.EXACT_GRAPH_WALL_DEADLINE_MS}ms'"
        ),
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        (
            "SET LOCAL statement_timeout = "
            f"'{exact_module.EXACT_GRAPH_WALL_DEADLINE_MS}ms'"
        ),
    ]
    assert analytics.main_calls == []


@pytest.mark.unit
def test_annotation_slow_label_discovery_exhausts_deadline_before_score_work(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 2)
    label = SimpleNamespace(name="quality", type="numeric")
    clock = {"value": 0.0}
    statements: list[str] = []
    score_filters: list[dict] = []

    class ForbiddenScoreManager:
        @staticmethod
        def filter(**kwargs):
            score_filters.append(kwargs)
            raise AssertionError("Score partitions must not start after label timeout")

    class SlowLabels:
        @staticmethod
        def get(**_kwargs):
            clock["value"] = exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS / 1000 + 0.001
            return label

    cursor = SimpleNamespace(execute=lambda statement: statements.append(statement))
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=ForbiddenScoreManager()),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SlowLabels(),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        exact_module,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            cursor=lambda: nullcontext(cursor),
        ),
    )
    monkeypatch.setattr(exact_module, "monotonic", lambda: clock["value"])

    with pytest.raises(ExactGraphReadError, match="deadline exceeded"):
        read_exact_annotation_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=[_time_filter(start, end)],
            interval="day",
            req_data_config={
                "id": "55555555-5555-4555-8555-555555555555",
                "output_type": "float",
            },
            observe_type="trace",
        )

    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        (
            "SET LOCAL statement_timeout = "
            f"'{exact_module.EXACT_GRAPH_WALL_DEADLINE_MS}ms'"
        ),
    ]
    assert score_filters == []
    assert analytics.main_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_annotation_membership_batches_use_current_state_without_ceilings(
    monkeypatch,
    observe_type,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 3)
    scores = [
        {
            "trace_id": "44444444-4444-4444-8444-444444444441",
            "observation_span_id": "span-1" if observe_type == "span" else None,
            "created_at": datetime(2026, 1, 2, 1),
            "value": {"rating": 4},
        },
        {
            "trace_id": "44444444-4444-4444-8444-444444444442",
            "observation_span_id": "span-2" if observe_type == "span" else None,
            "created_at": datetime(2026, 1, 2, 2),
            "value": {"rating": 5},
        },
    ]
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreListManager(scores)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(exact_module, "connection", SimpleNamespace(vendor="sqlite"))
    monkeypatch.setattr(exact_module, "EXACT_GRAPH_MEMBERSHIP_BATCH_SIZE", 1)
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger", "deleted = 0"),
    )

    result = read_exact_annotation_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_combined_relation_filters(start, end),
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type=observe_type,
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 2
    assert all(
        "additional_table_filters" not in call_settings
        for _query, _params, call_settings in analytics.main_calls
    )
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_annotation_membership_batches_share_one_whole_refresh_deadline(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 3)
    score = {
        "trace_id": "44444444-4444-4444-8444-444444444441",
        "observation_span_id": None,
        "created_at": datetime(2026, 1, 2, 1),
        "value": {"rating": 4},
    }
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreManager(score)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(exact_module, "connection", SimpleNamespace(vendor="sqlite"))
    clock = iter((0.0, exact_module.EXACT_GRAPH_QUERY_TIMEOUT_MS / 1000 + 0.001))
    monkeypatch.setattr(exact_module, "monotonic", lambda: next(clock))

    with pytest.raises(ExactGraphReadError, match="deadline exceeded"):
        read_exact_annotation_graph(
            analytics=analytics,
            project_id="11111111-1111-4111-8111-111111111111",
            filters=[_time_filter(start, end)],
            interval="day",
            req_data_config={
                "id": "55555555-5555-4555-8555-555555555555",
                "output_type": "float",
            },
            observe_type="trace",
        )

    assert analytics.main_calls == []


@pytest.mark.unit
def test_session_annotation_graph_uses_full_window_session_membership(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    trace_id = "44444444-4444-4444-8444-444444444444"
    score = {
        "trace_id": trace_id,
        "observation_span_id": None,
        "created_at": datetime(2026, 2, 10),
        "value": {"rating": 4},
    }
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreManager(score)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        exact_module,
        "connection",
        SimpleNamespace(vendor="sqlite"),
    )

    filters = [
        *_combined_session_filters(start, end),
        *_exact_structured_filters(start, end)[2:],
    ]
    result = read_exact_annotation_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type="trace",
        aggregation_context="session",
    )

    membership_calls = [
        call for call in analytics.main_calls if "SELECT DISTINCT trace_id" in call[0]
    ]
    assert len(membership_calls) == 1
    query, params, settings = membership_calls[0]
    _assert_session_membership_sql(query, params, start, end)
    assert "candidate_member_session_remap_target_new_ids AS" in query
    assert "candidate_session_remap_target_new_ids AS" in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert params["candidate_trace_ids"] == (trace_id,)
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_user_annotation_graph_bounds_remaps_to_candidate_groups(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    trace_id = "44444444-4444-4444-8444-444444444444"
    score = {
        "trace_id": trace_id,
        "observation_span_id": None,
        "created_at": datetime(2026, 2, 10),
        "value": {"rating": 4},
    }
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreManager(score)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(exact_module, "connection", SimpleNamespace(vendor="sqlite"))

    result = read_exact_annotation_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[_time_filter(start, end)],
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type="trace",
        aggregation_context="user",
    )

    membership_queries = [
        query
        for query, _params, _settings in analytics.main_calls
        if "candidate_member_end_user_remap_target_new_ids AS" in query
    ]
    assert len(membership_queries) == 1
    query = membership_queries[0]
    assert "candidate_end_user_remap_target_new_ids AS" in query
    assert "candidate_user_session_remap_target_new_ids AS" in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_annotation_graph_supports_combined_structured_filters(
    monkeypatch,
    observe_type,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 10)
    trace_id = "44444444-4444-4444-8444-444444444444"
    span_id = "span-1" if observe_type == "span" else None
    score = {
        "trace_id": trace_id,
        "observation_span_id": span_id,
        "created_at": datetime(2026, 1, 5),
        "value": {"rating": 4},
    }
    label = SimpleNamespace(name="quality", type="numeric")
    monkeypatch.setattr(
        exact_module,
        "Score",
        SimpleNamespace(no_workspace_objects=_ScoreManager(score)),
    )
    monkeypatch.setattr(
        exact_module,
        "get_annotation_labels_for_project",
        lambda _project_id: SimpleNamespace(get=lambda **_kwargs: label),
    )
    monkeypatch.setattr(exact_module.transaction, "atomic", nullcontext)
    monkeypatch.setattr(exact_module, "connection", SimpleNamespace(vendor="sqlite"))

    result = read_exact_annotation_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=_exact_structured_filters(start, end),
        interval="day",
        req_data_config={
            "id": "55555555-5555-4555-8555-555555555555",
            "output_type": "float",
        },
        observe_type=observe_type,
    )

    membership_queries = [
        query
        for query, _params, _settings in analytics.main_calls
        if "JSONExtractArrayRaw(attributes_extra" in query
    ]
    assert membership_queries
    assert "JSONExtractRaw(attributes_extra" in membership_queries[0]
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_type", "reader_name"),
    [
        ("eval", "read_exact_eval_graph"),
        ("annotation", "read_exact_annotation_graph"),
    ],
)
def test_session_eval_annotation_direct_reader_keeps_session_context(
    monkeypatch,
    metric_type,
    reader_name,
):
    from tracer.services.clickhouse import graph_dispatch

    captured = {}

    def direct_reader(**kwargs):
        captured.update(kwargs)
        return {
            "metric_name": "metric",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(
        graph_dispatch,
        reader_name,
        direct_reader,
    )
    common = {
        "analytics": object(),
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": _combined_session_filters(
            datetime(2026, 1, 1), datetime(2026, 3, 15)
        ),
        "interval": "day",
        "req_data_config": {"id": "55555555-5555-4555-8555-555555555555"},
        "observe_type": "trace",
        "aggregation_context": "session",
    }
    if metric_type == "eval":
        graph_dispatch.fetch_eval_graph_ch(**common)
    else:
        graph_dispatch.fetch_annotation_graph_ch(**common)

    assert captured["aggregation_context"] == "session"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_type", "reader_name"),
    [
        ("eval", "read_exact_eval_graph"),
        ("annotation", "read_exact_annotation_graph"),
    ],
)
def test_user_eval_annotation_direct_reader_keeps_user_context(
    monkeypatch,
    metric_type,
    reader_name,
):
    from tracer.services.clickhouse import graph_dispatch

    captured = {}

    def direct_reader(**kwargs):
        captured.update(kwargs)
        return {
            "metric_name": "metric",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(
        graph_dispatch,
        reader_name,
        direct_reader,
    )
    common = {
        "analytics": object(),
        "project_id": "22222222-2222-4222-8222-222222222222",
        "filters": [_time_filter(datetime(2026, 1, 1), datetime(2026, 3, 15))],
        "interval": "day",
        "req_data_config": {"id": "55555555-5555-4555-8555-555555555555"},
        "observe_type": "trace",
        "aggregation_context": "user",
    }
    if metric_type == "eval":
        graph_dispatch.fetch_eval_graph_ch(**common)
    else:
        graph_dispatch.fetch_annotation_graph_ch(**common)

    assert captured["aggregation_context"] == "user"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("namespace", "reader_name"),
    [
        ("observe-eval-graph", "read_exact_eval_graph"),
        ("observe-annotation-graph", "read_exact_annotation_graph"),
    ],
)
def test_exact_worker_forwards_session_context_to_eval_annotation_reader(
    monkeypatch,
    namespace,
    reader_name,
):
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.services.clickhouse.v2 import query_service
    from tracer.tasks import exact_aggregation

    captured = {}

    def reader(**kwargs):
        captured.update(kwargs)
        return {
            "metric_name": "metric",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(exact_graph_reads, reader_name, reader)
    monkeypatch.setattr(query_service, "V2AnalyticsQueryService", lambda: object())
    monkeypatch.setattr(
        exact_aggregation,
        "_reauthorize_exact_observe_project",
        lambda _identity: None,
    )
    exact_aggregation._observe_payload(
        namespace,
        {
            "project_id": "22222222-2222-4222-8222-222222222222",
            "filters": _combined_session_filters(
                datetime(2026, 1, 1), datetime(2026, 3, 15)
            ),
            "interval": "day",
            "req_data_config": {"id": "55555555-5555-4555-8555-555555555555"},
            "observe_type": "trace",
            "aggregation_context": "session",
        },
    )

    assert captured["aggregation_context"] == "session"


@pytest.mark.unit
def test_exact_user_graph_uses_one_full_window_current_state_statement():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)

    result = read_exact_user_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=[_time_filter(start, end)],
        interval="day",
        metric_id="active_users",
    )

    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, settings = analytics.main_calls[0]
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert "candidate_trace_ids AS" in query
    assert "HAVING min(start_time) >= %(start_date)s" in query
    assert "start_time >= %(snapshot_start_date)s" in query
    assert "GROUP BY end_user_id, trace_id" in query
    assert "FROM end_users AS dimension_user FINAL" in query
    assert "FROM user_rows" not in query
    assert "candidate_user_session_remap_target_new_ids AS" not in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_user_graph_applies_entity_filters_after_full_window_aggregation():
    analytics = _ExactEntityAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    filters = [
        _time_filter(start, end),
        {
            "column_id": "num_traces",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than_or_equal",
                "filter_value": 10,
            },
        },
        {
            "column_id": "num_sessions",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "between",
                "filter_value": [2, 20],
            },
        },
        {
            "column_id": "user_id",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "contains",
                "filter_value": "customer",
            },
        },
        {
            "column_id": "payload",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "map",
                "filter_op": "contains",
                "filter_value": {"kind": "vip"},
            },
        },
    ]

    result = read_exact_user_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        metric_id="active_users",
    )

    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, _settings = analytics.main_calls[0]
    assert "WHERE num_traces >= %(user_filter_1)s" in query
    assert "num_sessions BETWEEN %(user_filter_2_start)s" in query
    assert "positionCaseInsensitive(toString(user_id)" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert "span_attr_num['num_traces']" not in query
    assert "span_attr_num['num_sessions']" not in query
    assert "groupUniqArray(trace_id) AS user_trace_ids" not in query
    assert params["user_filter_1"] == 10
    assert params["user_filter_2_start"] == 2
    assert params["user_filter_2_end"] == 20
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_user_graph_does_not_apply_unsafe_relation_ceilings(
    monkeypatch,
):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger", "deleted = 0"),
    )

    result = read_exact_user_system_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=_combined_relation_filters(start, end),
        interval="day",
        metric_id="active_users",
    )

    assert analytics.capture_calls == []
    _assert_entity_output_partitions(analytics.main_calls, start, end)
    assert "additional_table_filters" not in analytics.main_calls[0][2]
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_user_eval_filter_is_full_window_membership_not_raw_span_attribute(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _SessionContextAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 3, 15)
    eval_config_id = "33333333-3333-4333-8333-333333333333"
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )
    config_qs = SimpleNamespace(get=lambda **_kwargs: config)
    monkeypatch.setattr(
        exact_module.CustomEvalConfig.objects,
        "select_related",
        lambda *_args: config_qs,
    )
    monkeypatch.setattr(
        exact_module,
        "eval_logger_source",
        lambda *_args, **_kwargs: ("tracer_eval_logger_v2", "eval_scan.is_deleted = 0"),
    )
    filters = [
        _time_filter(start, end),
        {
            "column_id": "eval_score",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "greater_than_or_equal",
                "filter_value": 80,
            },
        },
        {
            "column_id": "total_cost",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "number",
                "filter_op": "less_than",
                "filter_value": 100,
            },
        },
    ]

    result = read_exact_eval_graph(
        analytics=analytics,
        project_id="22222222-2222-4222-8222-222222222222",
        filters=filters,
        interval="day",
        req_data_config={"id": eval_config_id, "output_type": "SCORE"},
        observe_type="trace",
        aggregation_context="user",
    )

    _assert_entity_output_partitions(analytics.main_calls, start, end)
    query, params, settings = analytics.main_calls[0]
    assert "SELECT DISTINCT toString(candidate_member.trace_id) AS trace_id" in query
    assert "AS selected_users" in query
    assert "candidate_member_end_user_remap_target_new_ids AS" in query
    assert "candidate_end_user_remap_target_new_ids AS" in query
    assert "candidate_user_session_remap_target_new_ids AS" in query
    assert "OVER (PARTITION BY new_id)" not in query
    assert "user_eval_metrics AS" in query
    assert "WHERE bool_eval_pass_rate >= %(user_filter_1)s" in query
    assert "total_cost < %(user_filter_2)s" in query
    assert "span_attr_num['eval_score']" not in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
