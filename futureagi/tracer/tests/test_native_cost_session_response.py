"""Response mapping only: SQL truth is tested separately on the CH25 sidecar."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.views import trace_session

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.mark.parametrize("cost", [None, 0.0, 0.60])
def test_session_detail_metadata_preserves_sql_cost(monkeypatch, cost):
    session = "11111111-1111-4111-8111-111111111111"
    project = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(
        trace_session,
        "_resolve_session_ids_to_canonical",
        lambda *args, **kwargs: {session: session},
    )
    monkeypatch.setattr(
        trace_session, "_expand_session_group", lambda *args, **kwargs: [session]
    )
    monkeypatch.setattr(
        trace_session, "get_session_navigation", lambda *args, **kwargs: (None, None)
    )
    analytics = Mock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(
            data=[
                {
                    "session_start": None,
                    "session_end": None,
                    "total_cost": cost,
                    "total_tokens": 240,
                    "total_traces": 2,
                }
            ]
        ),
        SimpleNamespace(data=[]),
    ]
    view = trace_session.TraceSessionView()
    # Exercise the production response mapping without depending on envelope shape.
    view._gm = SimpleNamespace(success_response=lambda payload: payload)
    response = view._retrieve_clickhouse(
        Mock(), session, project, analytics, {"page_number": 0, "page_size": 10}
    )
    assert response["session_metadata"]["total_cost"] == cost
    assert response["session_metadata"]["total_tokens"] == 240
    assert response["session_metadata"]["total_traces"] == 2
    assert analytics.execute_ch_query.call_count == 2


@pytest.mark.parametrize("cost", [None, 0.0, 0.50])
def test_observe_response_keeps_rollup_coverage(cost):
    from tracer.tests.test_traces_of_session_pagination import (
        TestTracesOfSessionPagination as PaginationHarness,
    )

    harness = PaginationHarness()
    view = harness._make_view()
    request = harness._make_request(page_size=1)
    project = "22222222-2222-4222-8222-222222222222"
    roots = [
        {
            "trace_id": "11111111-1111-4111-8111-111111111111",
            "project_id": project,
            "cost": 0.5,
        }
    ]
    analytics = harness._routing_analytics(trace_rows=roots)
    original = analytics.execute_ch_query.side_effect

    def with_coverage(query, params=None, **kwargs):
        result = original(query, params, **kwargs)
        if params and params.get("content_trace_ids"):
            for row in result.data:
                row.update(
                    trace_rollup_present=1,
                    trace_rollup_cost=cost,
                    trace_rollup_cost_unknown=int(cost is None),
                    trace_rollup_total_tokens=120,
                )
        return result

    analytics.execute_ch_query.side_effect = with_coverage
    with (
        patch("tracer.views.trace.CustomEvalConfig") as configs,
        patch("tracer.views.trace.get_annotation_labels_for_project", return_value=[]),
        patch("tracer.views.trace._build_annotation_map_from_scores", return_value={}),
        patch(
            "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
            return_value=harness._bounded_page(roots, total=1),
        ),
    ):
        configs.objects.filter.return_value.select_related.return_value = []
        status, payload = view._list_traces_of_session_clickhouse(
            request,
            project_id=project,
            validated_data={
                "filters": [],
                "page_number": 0,
                "page_size": 1,
                "allow_sampled": True,
            },
            analytics=analytics,
            org_project_ids=None,
            org=request.organization,
        )
    assert status == "ok"
    assert len(payload["table"]) == 1
    assert payload["table"][0]["cost"] == cost
    assert payload["table"][0]["total_tokens"] == 120
