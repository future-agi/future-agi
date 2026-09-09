"""Public chart bundles preserve exact snapshot state and trusted scope."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.v2 import query_service
from tracer.utils.graphs_optimized import get_all_system_metrics


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("refresh", [False, True])
def test_chart_bundle_returns_pending_without_inline_query(monkeypatch, days, refresh):
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
    analytics = Mock()
    monkeypatch.setattr(query_service, "V2AnalyticsQueryService", lambda: analytics)
    calls = []

    def schedule(namespace, identity, **options):
        calls.append((namespace, identity, options))
        return options["pending_payload"]

    monkeypatch.setattr(graph_dispatch, "read_or_schedule_exact_snapshot", schedule)
    result = get_all_system_metrics(
        interval="day",
        filters=filters,
        property="average",
        system_metric_filters={"project_id": "00000000-0000-4000-8000-000000000001"},
        refresh=refresh,
        organization_id="organization",
        workspace_id="workspace",
    )
    assert len(calls) == 1
    namespace, identity, options = calls[0]
    assert namespace == "observe-all-system-graphs"
    assert identity["filters"] == filters
    assert identity["organization_id"] == "organization"
    assert identity["workspace_id"] == "workspace"
    assert options["refresh"] is refresh
    assert result["query_status"] == "pending"
    assert result["query_complete"] is False
    assert result["query_sampled"] is False
    assert result["query_refreshing"] is True
    assert graph_dispatch.graph_payload_is_publishable(result, allow_sampled=False)
    assert all(result[key] == [] for key in ("latency", "tokens", "cost", "traffic"))
    analytics.execute_ch_query.assert_not_called()
