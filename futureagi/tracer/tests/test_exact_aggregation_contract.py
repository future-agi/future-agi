import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from clickhouse_driver.errors import ServerException
from django.core.cache import cache
from django.db import DatabaseError

from tracer.services.clickhouse.exact_graph_reads import (
    ExactGraphReadError,
    _annotation_label_ids_for_filters,
    _filter_relation_requirements,
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
    _exact_refresh_workflow_task_id,
    begin_exact_refresh,
    exact_payload_is_complete,
    exact_refresh_state,
    finish_exact_refresh,
    normalized_snapshot_identity,
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
    assert enqueue.call_args.kwargs["queue"] == "tasks_xl"
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
    assert wire_identity == normalized_snapshot_identity(identity)
    assert snapshot_cache_key("observe-system-graph", identity) == snapshot_cache_key(
        "observe-system-graph", wire_identity
    )
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
def test_refresh_claim_uses_short_dispatch_lease_then_activity_promotes_it(
    monkeypatch,
):
    """Only an activity that actually starts may hold the one-hour lease."""

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
    monkeypatch.setattr(cache_module, "_refresh_dispatch_seconds", lambda: 600)
    monkeypatch.setattr(cache_module, "_refresh_lock_seconds", lambda: 3600)
    identity = {"project": "p", "metric": "latency"}

    token = cache_module.begin_exact_refresh("observe-lease-test", identity)

    assert token
    assert [timeout for _op, _key, timeout in recording_cache.timeouts] == [600, 600]
    assert cache_module.activate_exact_refresh("observe-lease-test", identity, token)
    assert [timeout for _op, _key, timeout in recording_cache.timeouts[-2:]] == [
        3600,
        3600,
    ]


@pytest.mark.unit
def test_dispatch_lease_survives_queue_delay_then_promotes_to_running(monkeypatch):
    """A healthy tasks_xl backlog must not expire the pre-start claim."""

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
    monkeypatch.setattr(cache_module, "_refresh_dispatch_seconds", lambda: 600)
    monkeypatch.setattr(cache_module, "_refresh_lock_seconds", lambda: 3600)
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

    expiring_cache.advance(9 * 60)
    assert cache_module.activate_exact_refresh(namespace, identity, token)

    # We are now past the original dispatch deadline, but activity promotion
    # owns an independent one-hour lease.
    expiring_cache.advance(2 * 60)
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
    """A Temporal pre-activity failure must not leave a one-hour pending UI."""

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

        # Model the short dispatch lease expiring because a mixed-version
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
def test_exact_refresh_is_registered_on_existing_temporal_xl_worker():
    from tfc.temporal.common.registry import (
        TEMPORAL_ACTIVITY_MODULES,
        get_workflows_for_queue,
    )
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow
    from tracer.tasks.exact_aggregation import refresh_exact_aggregation_snapshot

    metadata = _ACTIVITY_REGISTRY[refresh_exact_aggregation_snapshot.name]
    assert metadata["queue"] == "tasks_xl"
    assert metadata["time_limit"] == 60 * 60
    assert metadata["max_retries"] == 0
    assert "tracer.tasks" in TEMPORAL_ACTIVITY_MODULES
    assert TaskRunnerWorkflow in get_workflows_for_queue("tasks_xl")


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
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_system_graph_compiles_relations_in_one_project_scoped_statement(
    monkeypatch,
    observe_type,
):
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.services.clickhouse import exact_graph_reads as exact_module

    analytics = _RelationSnapshotAnalytics()
    _stub_annotation_label_ids(monkeypatch, exact_module)
    start = datetime(2026, 1, 1)
    end = datetime(2026, 4, 15)

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
    end = datetime(2026, 4, 15)
    user_filter = _combined_relation_filters(start, end)[-1]

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=[_time_filter(start, end), user_filter],
        interval="day",
        metric_id="traffic",
        observe_type="trace",
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
        filters=_combined_relation_filters(datetime(2026, 1, 1), datetime(2026, 4, 15)),
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )

    assert analytics.capture_calls == []
    assert len(analytics.main_calls) == 1


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_exact_system_graph_combines_scalar_array_map_and_legacy_json(observe_type):
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 5)

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_structured_filters(start, end),
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )

    assert analytics.partition_calls
    query, params, settings = analytics.partition_calls[0]
    assert "attrs_string" in query
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert "toString(JSONType(attributes_extra" in query
    assert params["start_date"] == start
    assert params["end_date"] == end
    assert params["graph_filter_2_latest_filter_key_2"] == "tags"
    assert params["graph_filter_3_latest_filter_key_3"] == "profile"
    assert params["graph_filter_4_latest_filter_key_4"] == "legacy_payload"
    assert "additional_table_filters" not in settings
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
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
    if observe_type == "trace":
        assert query.count("AS graph_bucket_match_") == 4
        assert query.count("max(graph_bucket_match_") == 4
        assert "groupArrayIf(" in query
        compact_suffix = query.split(") AS graph_physical_versions", 1)[1]
        assert "attrs_string" not in compact_suffix
        assert "attrs_number" not in compact_suffix
        assert "attrs_bool" not in compact_suffix
        assert "attributes_extra" not in compact_suffix
    else:
        assert "graph_bucket_match_" not in query
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_trace_graph_routes_every_span_read_through_one_statement_source():
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 8, 1)
    end = datetime(2026, 8, 5)

    result = read_exact_system_graph(
        analytics=analytics,
        project_id="11111111-1111-4111-8111-111111111111",
        filters=_exact_multi_filters(start, end),
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )

    query, params, settings = analytics.partition_calls[0]
    # Every outer contribution and scalar predicate is evaluated on one
    # physical latest-state row stream inside one ClickHouse statement.
    assert query.count("FROM spans") == 1
    assert "FROM spans FINAL" not in query
    assert "argMax(" in query
    assert "AS latest_spans" not in query
    assert "SELECT DISTINCT trace_id" not in query
    assert "JOIN spans" not in query
    assert "PREWHERE project_id = %(project_id)s" in query
    assert "snapshot_version_ceiling" not in params
    assert "additional_table_filters" not in settings
    assert settings["optimize_move_to_prewhere_if_final"] == 0
    assert settings["use_skip_indexes_if_final"] == 0
    # Separate bucket flags let two sibling spans independently satisfy
    # final_status and confidence; the compact trace aggregate retains every
    # exact additive output-bucket state without buffering raw Map columns.
    assert "OVER (PARTITION BY trace_id) AS graph_match_" not in query
    assert query.count("AS graph_bucket_match_") == 2
    assert query.count("max(graph_bucket_match_") == 2
    assert "graph_match_0 = 1" in query
    assert "graph_match_1 = 1" in query
    assert "groupArrayIf(" in query
    assert len(analytics.partition_calls) == 1
    assert result["query_count"] == 1
    assert "query_snapshot_version_ceiling" not in result
    assert result["query_complete"] is True
    assert result["query_sampled"] is False


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
def test_exact_system_graph_empty_datetime_domain_issues_no_clickhouse_query():
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
        observe_type="trace",
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
def test_exact_graph_budget_failure_does_not_stitch_cross_query_partitions():
    analytics = _BudgetSplittingAnalytics()
    start = datetime(2026, 8, 1, 0, 0)
    end = datetime(2026, 8, 1, 4, 0)

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
    assert "attrs_string" in query and "attrs_number" in query
    assert "snapshot_version_ceiling" not in params
    assert "additional_table_filters" not in settings
    assert timeout == 3_300_000


@pytest.mark.unit
def test_public_graph_poll_never_runs_long_exact_query_inline():
    from tracer.services.clickhouse import graph_dispatch

    cache.clear()

    class InlineQueryForbidden:
        @staticmethod
        def execute_ch_query(*_args, **_kwargs):
            pytest.fail("the HTTP graph poll must not execute ClickHouse inline")

    start = datetime(2026, 8, 1, 0, 0)
    end = datetime(2026, 8, 8, 0, 0)
    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        result = graph_dispatch.fetch_system_metric_graph_ch(
            analytics=InlineQueryForbidden(),
            project_id="11111111-1111-4111-8111-111111111111",
            filters=_exact_multi_filters(start, end),
            interval="day",
            metric_id="traffic",
            observe_type="trace",
            # The public timeout remains irrelevant: exact CH work belongs to
            # the background activity and is governed by its own ceiling.
            timeout_ms=1,
        )

    assert result["query_status"] == "pending"
    assert result["query_complete"] is False
    assert enqueue.call_count == 1


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
def test_long_exact_system_window_remains_one_statement():
    analytics = _ConcurrentArrivalAnalytics()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 4, 15)

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
        observe_type="trace",
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
    assert query.count("AS graph_bucket_match_") == 1
    assert query.count("max(graph_bucket_match_") == 1
    assert (params["start_date"], params["end_date"]) == (start, end)
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
@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_system_graph_does_not_stitch_budget_failed_statements(
    aggregation_context,
):
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
    assert timeout_ms == 3_300_000
    assert "additional_table_filters" not in settings


@pytest.mark.unit
@pytest.mark.parametrize("aggregation_context", ["session", "user"])
def test_entity_system_graph_indivisible_budget_failure_is_fail_closed(
    aggregation_context,
):
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
    assert analytics.main_calls[0][2] == 3_300_000


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
    assert "WITH candidate_sessions AS" in query
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
    assert query.count("SELECT DISTINCT trace_id") >= 3
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
    assert "FROM spans AS candidate_member FINAL" in query
    assert "FROM (" in query and "AS selected_sessions" in query
    assert "argMin(rs.input, rs.start_time) AS first_message" in query
    assert "session_duration >= %(session_having_1)s" in query
    assert "first_message ILIKE %(session_having_2)s" in query
    assert "rs.trace_session_id, ts_remap.survivor_id) IN" in query
    assert "span_attr_str['first_message']" not in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
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
    assert timeout_ms == 3_300_000
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
    assert "JSONExtractArrayRaw(attributes_extra" in query
    assert "JSONExtractRaw(attributes_extra" in query
    assert params["candidate_trace_ids"] == (trace_id,)
    assert "additional_table_filters" not in settings
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
    ("metric_type", "namespace"),
    [
        ("eval", "observe-eval-graph"),
        ("annotation", "observe-annotation-graph"),
    ],
)
def test_session_eval_annotation_cache_identity_keeps_session_context(
    monkeypatch,
    metric_type,
    namespace,
):
    from tracer.services.clickhouse import graph_dispatch

    captured = {}

    def read_or_schedule(actual_namespace, identity, **_kwargs):
        captured.update(namespace=actual_namespace, identity=identity)
        return {"query_status": "pending"}

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        read_or_schedule,
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

    assert captured["namespace"] == namespace
    assert captured["identity"]["aggregation_context"] == "session"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("metric_type", "namespace"),
    [
        ("eval", "observe-eval-graph"),
        ("annotation", "observe-annotation-graph"),
    ],
)
def test_user_eval_annotation_cache_identity_keeps_user_context(
    monkeypatch,
    metric_type,
    namespace,
):
    from tracer.services.clickhouse import graph_dispatch

    captured = {}

    def read_or_schedule(actual_namespace, identity, **_kwargs):
        captured.update(namespace=actual_namespace, identity=identity)
        return {"query_status": "pending"}

    monkeypatch.setattr(
        graph_dispatch,
        "read_or_schedule_exact_snapshot",
        read_or_schedule,
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

    assert captured["namespace"] == namespace
    assert captured["identity"]["aggregation_context"] == "user"


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
    assert "SELECT toString(trace_id) FROM candidate_trace_ids" in query
    assert "GROUP BY end_user_id, trace_id" in query
    assert "FROM user_rows" in query
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
    assert "user_eval_metrics AS" in query
    assert "WHERE bool_eval_pass_rate >= %(user_filter_1)s" in query
    assert "total_cost < %(user_filter_2)s" in query
    assert "span_attr_num['eval_score']" not in query
    assert params["snapshot_start_date"] == start
    assert params["snapshot_end_date"] == end
    assert "additional_table_filters" not in settings
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
