from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from tracer.services import exact_aggregation_cache as cache_module
from tracer.services.clickhouse.query_builders import base as base_query_module
from tracer.services.exact_aggregation_cache import (
    finish_exact_refresh,
    normalize_exact_observe_identity,
    publish_exact_snapshot_for_refresh,
    read_or_schedule_exact_snapshot,
    snapshot_cache_key,
)

PROJECT_ID = "22222222-2222-4222-8222-222222222222"


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                "2026-07-01T00:00:00Z",
                "2026-08-01T00:00:00Z",
            ],
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


def _pending(metric_name: str) -> dict:
    return {
        "metric_name": metric_name,
        "data": [],
        "query_complete": False,
        "query_status": "pending",
        "query_sampled": False,
    }


@pytest.mark.unit
def test_exact_observe_identity_freezes_the_default_window_once(monkeypatch):
    class FrozenDateTime(datetime):
        current = datetime(2026, 8, 9, 12, 30, 45, 123456)

        @classmethod
        def utcnow(cls):
            return cls.current

    monkeypatch.setattr(base_query_module, "datetime", FrozenDateTime)
    raw_identity = {
        "project_id": PROJECT_ID,
        "filters": [],
        "metric_id": "latency",
    }

    frozen = normalize_exact_observe_identity(raw_identity)
    FrozenDateTime.current = datetime(2026, 8, 10, 12, 30, 45, 123456)

    # The worker receives the already-frozen identity. Re-normalizing it at a
    # later wall clock instant must address exactly the same cache and SQL range.
    assert normalize_exact_observe_identity(frozen) == frozen
    assert frozen["filters"] == [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-10T12:30:45.123456Z",
                    "2026-08-09T12:30:45.123456Z",
                ],
            },
        }
    ]


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_no_filter_poll_at_a_later_time_reuses_the_original_frozen_job(monkeypatch):
    class FrozenDateTime(datetime):
        current = datetime(2026, 8, 9, 12, 30)

        @classmethod
        def utcnow(cls):
            return cls.current

    monkeypatch.setattr(base_query_module, "datetime", FrozenDateTime)
    cache.clear()
    identity = {
        "project_id": PROJECT_ID,
        "filters": [],
        "metric_id": "latency",
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        first = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=_pending("latency"),
        )
        task = enqueue.call_args.kwargs["kwargs"]
        assert publish_exact_snapshot_for_refresh(
            "observe-system-graph",
            task["identity"],
            {
                "metric_name": "latency",
                "data": [{"timestamp": "2026-08-09T12:00:00Z", "value": 12}],
                "query_complete": True,
                "query_status": "complete",
                "query_sampled": False,
            },
            task["refresh_token"],
        )
        finish_exact_refresh(
            "observe-system-graph",
            task["identity"],
            task["refresh_token"],
            succeeded=True,
        )

        FrozenDateTime.current = datetime(2026, 8, 10, 12, 30)
        later_poll = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=_pending("latency"),
        )

        FrozenDateTime.current = datetime(2026, 8, 11, 12, 30)
        refresh_response = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=_pending("latency"),
        )
        FrozenDateTime.current = datetime(2026, 8, 12, 12, 30)
        refresh_poll = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=_pending("latency"),
        )

    assert enqueue.call_count == 2
    assert first["query_refreshing"] is True
    assert later_poll["query_status"] == "complete"
    assert later_poll["data"][0]["value"] == 12
    assert refresh_response["data"][0]["value"] == 12
    assert refresh_response["query_refreshing"] is True
    assert refresh_poll["data"][0]["value"] == 12
    assert refresh_poll["query_refreshing"] is True


@pytest.mark.unit
def test_filtered_system_graph_uses_inline_exact_reader_without_snapshot(
    monkeypatch,
):
    from tracer.services.clickhouse import graph_dispatch

    exact_calls = []

    def exact_read(**kwargs):
        exact_calls.append(kwargs)
        return {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    monkeypatch.setattr(
        graph_dispatch,
        "read_exact_system_graph",
        exact_read,
    )
    result = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=[_attribute_filter()],
        interval="day",
        metric_id="latency",
        observe_type="span",
    )

    assert result["query_status"] == "complete"
    assert result["query_complete"] is True
    assert result["query_sampled"] is False
    assert result["query_exact"] is True
    assert result["query_provenance"] == "exact_snapshot"
    assert len(exact_calls) == 1
    assert exact_calls[0]["project_id"] == PROJECT_ID
    assert exact_calls[0]["filters"] == [_attribute_filter()]
    assert exact_calls[0]["observe_type"] == "span"


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_failed_default_window_refresh_keeps_exact_data_and_can_retry(monkeypatch):
    """A failed refresh must not strand the alias or erase the last result."""

    class FrozenDateTime(datetime):
        current = datetime(2026, 8, 9, 12, 30)

        @classmethod
        def utcnow(cls):
            return cls.current

    monkeypatch.setattr(base_query_module, "datetime", FrozenDateTime)
    cache.clear()
    identity = {
        "project_id": PROJECT_ID,
        "filters": [],
        "metric_id": "latency",
    }
    exact = {
        "metric_name": "latency",
        "data": [{"timestamp": "2026-08-09T12:00:00Z", "value": 578.0}],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=_pending("latency"),
        )
        initial = enqueue.call_args_list[-1].kwargs["kwargs"]
        assert publish_exact_snapshot_for_refresh(
            "observe-system-graph",
            initial["identity"],
            exact,
            initial["refresh_token"],
        )
        finish_exact_refresh(
            "observe-system-graph",
            initial["identity"],
            initial["refresh_token"],
            succeeded=True,
        )

        FrozenDateTime.current = datetime(2026, 8, 10, 12, 30)
        refreshing = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=_pending("latency"),
        )
        failed = enqueue.call_args_list[-1].kwargs["kwargs"]
        finish_exact_refresh(
            "observe-system-graph",
            failed["identity"],
            failed["refresh_token"],
            succeeded=False,
        )

        after_failure = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=False,
            pending_payload=_pending("latency"),
        )

        FrozenDateTime.current = datetime(2026, 8, 11, 12, 30)
        retrying = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            identity,
            refresh=True,
            pending_payload=_pending("latency"),
        )
        retry = enqueue.call_args_list[-1].kwargs["kwargs"]

    assert enqueue.call_count == 3
    assert failed["identity"] != initial["identity"]
    assert retry["identity"] != failed["identity"]
    assert refreshing["data"] == exact["data"]
    assert refreshing["query_refreshing"] is True
    assert after_failure["data"] == exact["data"]
    assert after_failure["query_refresh_failed"] is True
    assert retrying["data"] == exact["data"]
    assert retrying["query_refreshing"] is True
    finish_exact_refresh(
        "observe-system-graph",
        retry["identity"],
        retry["refresh_token"],
        succeeded=True,
    )


@pytest.mark.unit
def test_exact_observe_identity_coalesces_equivalent_filter_conjunctions():
    attribute = {
        "column_id": "final_status",
        "source": "traces",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["Rechazado", "Aprobado", "Rechazado"],
            "attribute_value_types": ["string", "string", "string"],
        },
    }
    left = {
        "project_id": PROJECT_ID,
        "filters": [
            {**attribute, "display_name": "Final status"},
            _time_filter(),
        ],
        "metric_id": "latency",
    }
    right_attribute = {
        **attribute,
        "filter_config": {
            **attribute["filter_config"],
            "filter_value": ["Aprobado", "Rechazado"],
            "attribute_value_types": ["string", "string"],
        },
    }
    right = {
        "metric_id": "latency",
        "filters": [_time_filter(), right_attribute, right_attribute],
        "project_id": PROJECT_ID,
    }

    normalized_left = normalize_exact_observe_identity(left)
    normalized_right = normalize_exact_observe_identity(right)

    assert normalized_left == normalized_right
    assert snapshot_cache_key("observe-system-graph", normalized_left) == (
        snapshot_cache_key("observe-system-graph", normalized_right)
    )


@pytest.mark.unit
def test_exact_observe_identity_canonicalizes_equivalent_time_complements():
    excluded = "2026-07-15T12:00:00.000000Z"
    one_microsecond_later = "2026-07-15T12:00:00.000001Z"
    not_equals = {
        "project_id": PROJECT_ID,
        "filters": [
            _time_filter(),
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "not_equals",
                    "filter_value": excluded,
                },
            },
        ],
    }
    not_between = {
        "project_id": PROJECT_ID,
        "filters": [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "not_between",
                    "filter_value": [excluded, one_microsecond_later],
                },
            },
            _time_filter(),
        ],
    }

    assert normalize_exact_observe_identity(not_equals) == (
        normalize_exact_observe_identity(not_between)
    )


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_equivalent_exact_requests_enqueue_one_worker():
    cache.clear()
    left = {
        "project_id": PROJECT_ID,
        "filters": [
            _time_filter(),
            {
                "column_id": "final_status",
                "display_name": "Final status",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["Rechazado", "Aprobado"],
                },
            },
        ],
        "metric_id": "latency",
    }
    right = {
        **left,
        "filters": [
            {
                **left["filters"][1],
                "display_name": "Estado final",
                "filter_config": {
                    **left["filters"][1]["filter_config"],
                    "filter_value": ["Aprobado", "Rechazado", "Aprobado"],
                },
            },
            _time_filter(),
        ],
    }

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        first = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            left,
            refresh=False,
            pending_payload=_pending("latency"),
        )
        second = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            right,
            refresh=False,
            pending_payload=_pending("latency"),
        )

    assert enqueue.call_count == 1
    assert first["query_refreshing"] is True
    assert second["query_refreshing"] is True


@pytest.mark.unit
@override_settings(
    EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation",
    EXACT_AGGREGATION_MAX_INFLIGHT_PER_SCOPE=1,
)
def test_exact_refresh_admission_is_bounded_per_project_and_released_on_finish():
    cache.clear()
    latency = {
        "project_id": PROJECT_ID,
        "filters": [_time_filter()],
        "metric_id": "latency",
    }
    cost = {**latency, "metric_id": "cost"}
    traffic = {**latency, "metric_id": "traffic"}

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        read_or_schedule_exact_snapshot(
            "observe-system-graph",
            latency,
            refresh=False,
            pending_payload=_pending("latency"),
        )
        rejected = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            cost,
            refresh=False,
            pending_payload=_pending("cost"),
        )
        first_task = enqueue.call_args.kwargs["kwargs"]
        finish_exact_refresh(
            "observe-system-graph",
            first_task["identity"],
            first_task["refresh_token"],
            succeeded=True,
        )
        admitted_after_release = read_or_schedule_exact_snapshot(
            "observe-system-graph",
            traffic,
            refresh=False,
            pending_payload=_pending("traffic"),
        )

    assert enqueue.call_count == 2
    assert rejected["query_refreshing"] is True
    assert rejected["query_refresh_failed"] is False
    assert admitted_after_release["query_refreshing"] is True


@pytest.mark.unit
def test_low_level_observe_lifecycle_identity_without_filters_stays_compatible():
    identity = {"project": "p", "metric": "traffic"}

    assert normalize_exact_observe_identity(identity) == identity


@pytest.mark.unit
@override_settings(EXACT_AGGREGATION_MAX_INFLIGHT_PER_SCOPE=3)
def test_redis_scope_admission_is_atomic_and_uses_an_opaque_tenant_key(monkeypatch):
    captured: list[tuple] = []

    class RawClient:
        @staticmethod
        def eval(*args):
            captured.append(args)
            return 1

    class RedisAdapter:
        @staticmethod
        def get_client(*, write):
            assert write is True
            return RawClient()

        @staticmethod
        def make_key(key):
            return f"redis:{key}"

    monkeypatch.setattr(
        cache_module,
        "_redis_cache_client",
        lambda: RedisAdapter(),
    )

    assert cache_module._claim_exact_refresh_admission(
        {"project_id": PROJECT_ID},
        "opaque-token",
        lease_seconds=600,
    )

    (
        script,
        key_count,
        admission_key,
        now_ms,
        token,
        expiry_ms,
        ttl_margin_ms,
        max_inflight,
    ) = captured[0]
    assert script == cache_module._REDIS_CLAIM_SCOPE_ADMISSION_SCRIPT
    assert key_count == 1
    assert PROJECT_ID not in admission_key
    assert token == "opaque-token"
    assert expiry_ms - now_ms == 600_000
    assert ttl_margin_ms == 300_000
    assert max_inflight == 3
