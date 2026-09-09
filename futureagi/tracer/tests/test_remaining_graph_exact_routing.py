"""User, Eval and Annotation graphs retain scope across exact refresh routing."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import graph_dispatch as graph


@pytest.mark.parametrize("kind", ["user", "eval", "annotation"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("grain", ["trace", "session", "user"])
@pytest.mark.parametrize("refresh", [False, True])
def test_exact_graph_route_preserves_request(monkeypatch, kind, days, grain, refresh):
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
        },
        {
            "column_id": "company_id",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": ["12345678", "87654321"],
            },
        },
    ]
    calls = []

    def schedule(namespace, identity, **options):
        calls.append((namespace, identity, options))
        return options["pending_payload"]

    monkeypatch.setattr(graph, "read_or_schedule_exact_snapshot", schedule)
    analytics = Mock()
    common = {
        "analytics": analytics,
        "project_id": "00000000-0000-4000-8000-000000000001",
        "filters": filters,
        "interval": "day",
        "refresh": refresh,
        "organization_id": "org",
        "workspace_id": "workspace",
    }
    config = {"id": "score-id", "output_type": "SCORE", "aggregation": "average"}
    if kind == "user":
        result = graph.fetch_user_system_metric_graph_ch(
            **common, metric_id="active_users"
        )
        namespace = "observe-user-system-graph"
    else:
        result = getattr(graph, f"fetch_{kind}_graph_ch")(
            **common,
            req_data_config=config,
            observe_type="span",
            aggregation_context=grain,
        )
        namespace = f"observe-{kind}-graph"
    assert len(calls) == 1
    actual_namespace, identity, options = calls[0]
    assert actual_namespace == namespace
    assert identity["filters"] == filters
    assert identity["project_id"] == common["project_id"]
    assert identity["interval"] == "day"
    assert identity["organization_id"] == "org"
    assert identity["workspace_id"] == "workspace"
    assert options["refresh"] is refresh
    if kind != "user":
        assert identity["req_data_config"] == config
        assert identity["aggregation_context"] == grain
        assert identity["observe_type"] == "span"
    else:
        assert identity["metric_id"] == "active_users"
    assert result["query_complete"] is False
    assert result["query_status"] == "pending"
    assert result["query_sampled"] is False
    assert graph.graph_payload_is_publishable(result, allow_sampled=False)
    analytics.execute_ch_query.assert_not_called()
