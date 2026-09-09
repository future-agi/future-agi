"""Trace/Span graph dispatch must use latest-state readers, not raw versions."""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
from tracer.services.clickhouse.application_read_policy import is_application_read
from tracer.services.exact_aggregation_cache import snapshot_cache_key
from tracer.tasks import exact_aggregation

PROJECT = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("with_property", [False, True])
def test_system_graph_schedules_latest_state(
    monkeypatch, observe_type, days, with_property
):
    end = datetime(2026, 9, 5)
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    (end - timedelta(days=days)).isoformat(),
                    end.isoformat(),
                ],
            },
        }
    ]
    if with_property:
        filters.append(
            {
                "column_id": "company_id",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["one", "two"],
                    "attribute_value_types": ["string", "string"],
                },
            }
        )
    expected = {"data": [], "query_status": "pending", "query_complete": False}
    schedule = Mock(return_value=expected)
    monkeypatch.setattr(graph_dispatch, "read_or_schedule_exact_snapshot", schedule)
    analytics = Mock()
    result = graph_dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
        organization_id="organization",
        workspace_id="workspace",
        refresh=True,
    )
    assert result is expected
    schedule.assert_called_once()
    namespace, identity = schedule.call_args.args
    assert namespace == "observe-system-graph"
    assert identity["filters"] == filters
    assert identity["observe_type"] == observe_type
    assert identity["organization_id"] == "organization"
    assert identity["workspace_id"] == "workspace"
    assert identity["payload_version"] == 2
    legacy = {k: v for k, v in identity.items() if k != "payload_version"}
    assert snapshot_cache_key(namespace, identity) != snapshot_cache_key(
        namespace, legacy
    )
    assert schedule.call_args.kwargs["refresh"] is True
    assert schedule.call_args.kwargs["pending_payload"]["query_complete"] is False
    analytics.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_system_worker_uses_exact_reader_after_reauthorization(
    monkeypatch, observe_type
):
    events = []
    analytics = object()

    @contextmanager
    def service():
        events.append("connect")
        yield analytics

    expected = {"query_complete": True, "query_exact": True}

    def reader(**kwargs):
        assert is_application_read()
        events.append("read")
        assert kwargs["analytics"] is analytics
        assert kwargs["observe_type"] == observe_type
        assert kwargs["project_id"] == PROJECT
        return expected

    monkeypatch.setattr(
        exact_aggregation,
        "_reauthorize_exact_observe_project",
        lambda _: events.append("authorize"),
    )
    monkeypatch.setattr(exact_aggregation, "_exact_observe_analytics", service)
    monkeypatch.setattr(exact_graph_reads, "read_exact_system_graph", reader)
    raw = Mock(side_effect=AssertionError("raw physical versions must not be used"))
    monkeypatch.setattr(graph_dispatch, "_fetch_direct_raw_system_metric_graph", raw)
    assert (
        exact_aggregation._observe_payload(
            "observe-system-graph",
            {
                "project_id": PROJECT,
                "filters": [],
                "interval": "day",
                "metric_id": "traffic",
                "observe_type": observe_type,
                "payload_version": 2,
            },
        )
        is expected
    )
    assert not is_application_read()
    assert events == ["authorize", "connect", "read"]
    raw.assert_not_called()
