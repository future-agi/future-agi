"""Public Session graphs must not silently switch to non-exact time-only reads."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import session_graph


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("metric_id", sorted(session_graph.SESSION_SYSTEM_METRICS))
def test_time_only_session_metrics_schedule_exact_population(
    monkeypatch, days, metric_id
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
    expected = {"query_status": "pending", "query_complete": False, "data": []}
    schedule = Mock(return_value=expected)
    monkeypatch.setattr(session_graph, "read_or_schedule_exact_snapshot", schedule)
    analytics = Mock()
    result = session_graph.fetch_session_graph_ch(
        analytics=analytics,
        project_id="00000000-0000-4000-8000-000000000001",
        filters=filters,
        interval="day",
        req_data_config={"type": "SYSTEM_METRIC", "id": metric_id},
        organization_id="organization",
        workspace_id="workspace",
        refresh=True,
    )
    assert result is expected
    schedule.assert_called_once()
    namespace, identity = schedule.call_args.args
    assert namespace == "observe-session-system-graph"
    assert identity == {
        "project_id": "00000000-0000-4000-8000-000000000001",
        "filters": filters,
        "interval": "day",
        "metric_id": metric_id,
        "organization_id": "organization",
        "workspace_id": "workspace",
    }
    assert schedule.call_args.kwargs["refresh"] is True
    pending = schedule.call_args.kwargs["pending_payload"]
    assert pending["query_complete"] is False
    assert pending["query_sampled"] is False
    analytics.execute_ch_query.assert_not_called()
