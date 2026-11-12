"""Session trace cards must retain the project resolved by session retrieval."""

from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import UUID

import pytest

from tracer.views import trace_session

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.mark.parametrize(
    "project_id",
    [
        pytest.param(UUID("11111111-1111-4111-8111-111111111111"), id="postgres-uuid"),
        pytest.param("22222222-2222-4222-8222-222222222222", id="clickhouse-string"),
    ],
)
def test_each_session_trace_carries_resolved_project_id(monkeypatch, project_id):
    session_id = "33333333-3333-4333-8333-333333333333"
    # Both cases deliberately use identical trace IDs in different projects.
    trace_ids = ["shared-trace", "another-shared-trace"]
    trace_rows = [
        {
            "trace_id": trace_id,
            "input": f"{project_id} input {index}",
            "output": f"{project_id} output {index}",
            "root_latency_ms": 12,
            "total_cost": None,
            "total_tokens": 9,
            "input_tokens": 4,
            "output_tokens": 5,
        }
        for index, trace_id in enumerate(trace_ids)
    ]
    monkeypatch.setattr(
        trace_session,
        "_resolve_session_ids_to_canonical",
        Mock(return_value={session_id: session_id}),
    )
    monkeypatch.setattr(
        trace_session, "_expand_session_group", Mock(return_value=[session_id])
    )
    monkeypatch.setattr(
        trace_session, "get_session_navigation", Mock(return_value=(None, None))
    )
    configs = Mock()
    configs.objects.filter.return_value.values_list.return_value = []
    monkeypatch.setattr(trace_session, "CustomEvalConfig", configs)
    analytics = Mock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[{"total_traces": len(trace_rows)}]),
        SimpleNamespace(data=trace_rows),
    ]
    analytics.get_eval_config_ids_for_traces_ch.return_value = []
    view = trace_session.TraceSessionView()
    view._gm = SimpleNamespace(success_response=lambda payload: payload)

    payload = view._retrieve_clickhouse(
        request=SimpleNamespace(query_params={}),
        trace_session_id=session_id,
        project_id=project_id,
        analytics=analytics,
        query_data={"page_number": 0, "page_size": 10},
    )

    rows = payload["response"]
    assert [row["trace_id"] for row in rows] == trace_ids
    assert [row["system_metrics"]["project_id"] for row in rows] == [
        str(project_id),
        str(project_id),
    ]
    for actual, source in zip(rows, trace_rows, strict=True):
        assert actual["input"] == source["input"]
        assert actual["output"] == source["output"]
        assert actual["system_metrics"]["total_cost"] is None
        assert actual["system_metrics"]["total_tokens"] == 9
        assert actual["evals_metrics"] == {}
    assert payload["next"] is False
    assert analytics.execute_ch_query.call_count == 2
    for call in analytics.execute_ch_query.call_args_list:
        assert call.args[1]["project_id"] == str(project_id)
    configs.objects.filter.assert_called_once_with(project_id=project_id, deleted=False)
    analytics.get_eval_config_ids_for_traces_ch.assert_called_once_with(
        trace_ids, [], timeout_ms=ANY
    )
