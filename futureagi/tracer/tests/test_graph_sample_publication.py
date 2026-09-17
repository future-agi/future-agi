"""Public graphs publish only exact data or proven opt-in bounded samples."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracer.services.clickhouse.graph_dispatch import graph_payload_is_publishable


def _sampled_series(**overrides):
    return {
        "metric_name": "latency",
        "data": [{"timestamp": "2026-08-03T00:00:00Z", "value": 12}],
        "query_complete": False,
        "query_status": "sampled",
        "query_error_code": "sample_limit",
        "query_sampling_strategy": "time_stratified_latest_state",
        "query_sampling_strata": 8,
        "query_sampling_strata_completed": 8,
        **overrides,
    }


@pytest.mark.unit
def test_only_explicit_exact_or_empty_pending_graph_payloads_are_publishable():
    assert not graph_payload_is_publishable(
        {"metric_name": "latency", "data": []},
        allow_sampled=False,
    )
    assert graph_payload_is_publishable(
        {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
        allow_sampled=False,
    )
    assert graph_payload_is_publishable(
        {
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "pending",
            "query_sampled": False,
            "query_refreshing": True,
        },
        allow_sampled=False,
    )


@pytest.mark.unit
def test_sampled_graph_is_rejected_even_with_legacy_opt_in():
    sample = _sampled_series()

    assert not graph_payload_is_publishable(sample, allow_sampled=False)
    assert not graph_payload_is_publishable(sample, allow_sampled=True)


@pytest.mark.unit
def test_bounded_candidate_sample_is_rejected_even_with_legacy_opt_in():
    sample = _sampled_series(
        query_sampled=True,
        query_exact=False,
        query_provenance="bounded_candidates",
    )

    assert not graph_payload_is_publishable(sample, allow_sampled=False)
    assert not graph_payload_is_publishable(sample, allow_sampled=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"query_sampling_strategy": None},
        {"query_sampling_strata": 0},
        {"query_sampling_strata_completed": 1},
        {"query_complete": True},
    ],
)
def test_invalid_sample_is_never_publishable(overrides):
    assert not graph_payload_is_publishable(
        _sampled_series(**overrides),
        allow_sampled=True,
    )


@pytest.mark.unit
def test_every_series_in_a_public_chart_payload_must_be_publishable():
    assert not graph_payload_is_publishable(
        [
            _sampled_series(),
            {
                "name": "traffic",
                "data": [],
                "query_complete": False,
                "query_status": "degraded",
            },
        ],
        allow_sampled=True,
    )


@pytest.mark.unit
def test_project_system_graph_view_fails_closed_without_opt_in(monkeypatch):
    from tracer.views import project as project_view

    sample = _sampled_series(
        latency=[],
        tokens=[],
        cost=[],
        traffic=[],
    )
    monkeypatch.setattr(
        project_view, "get_all_system_metrics", lambda **_kwargs: sample
    )
    view = project_view.ProjectView()
    monkeypatch.setattr(
        view,
        "_get_project_in_scope",
        lambda _project_id: SimpleNamespace(
            organization_id="22222222-2222-4222-8222-222222222222"
        ),
    )

    def call(allow_sampled):
        request = SimpleNamespace(
            validated_query_data={
                "project_id": "11111111-1111-4111-8111-111111111111",
                "interval": "day",
                "filters": [],
                "allow_sampled": allow_sampled,
            }
        )
        view.request = request
        return unwrap(project_view.ProjectView.get_graph_data)(view, request)

    assert call(False).status_code == 503
    assert call(True).status_code == 503


@pytest.mark.unit
def test_users_aggregate_graph_view_fails_closed_without_opt_in(monkeypatch):
    from tracer.views import project as project_view

    monkeypatch.setattr(project_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        project_view,
        "fetch_annotation_graph_ch",
        lambda **_kwargs: _sampled_series(metric_name="annotation-id"),
    )
    view = project_view.ProjectView()
    monkeypatch.setattr(
        view,
        "_get_project_in_scope",
        lambda _project_id: SimpleNamespace(
            organization_id="22222222-2222-4222-8222-222222222222"
        ),
    )

    def call(allow_sampled):
        request = SimpleNamespace(
            validated_query_data={"allow_sampled": allow_sampled},
            validated_data={
                "project_id": "11111111-1111-4111-8111-111111111111",
                "filters": [],
                "interval": "day",
                "property": "average",
                "req_data_config": {
                    "id": "annotation-id",
                    "type": "ANNOTATION",
                    "output_type": "SCORE",
                },
            },
        )
        view.request = request
        return unwrap(project_view.ProjectView.get_users_aggregate_graph_data)(
            view,
            request,
        )

    assert call(False).status_code == 503
    assert call(True).status_code == 503


@pytest.mark.unit
def test_public_charts_view_fails_closed_without_opt_in(monkeypatch):
    from tracer.views import charts as charts_view

    monkeypatch.setattr(
        charts_view.Project.objects,
        "get",
        lambda **_kwargs: SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            organization_id="22222222-2222-4222-8222-222222222222",
        ),
    )
    monkeypatch.setattr(
        charts_view,
        "get_system_metric_data",
        lambda **_kwargs: _sampled_series(),
    )
    monkeypatch.setattr(
        charts_view,
        "get_request_organization",
        lambda _request: object(),
    )
    view = charts_view.ChartsView()

    def call(allow_sampled):
        request = SimpleNamespace(
            query_params={
                "project_id": "11111111-1111-4111-8111-111111111111",
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
                "allow_sampled": allow_sampled,
            },
            workspace=SimpleNamespace(id="33333333-3333-4333-8333-333333333333"),
        )
        view.request = request
        return unwrap(charts_view.ChartsView.fetch_graph)(view, request)

    assert call(False).status_code == 503
    assert call(True).status_code == 503


@pytest.mark.unit
def test_session_graph_view_fails_closed_without_opt_in_and_clears_points(
    monkeypatch,
):
    from tracer.views import trace_session as session_view

    project_scope = MagicMock()
    project_scope.get.return_value = SimpleNamespace(
        trace_type="observe",
        organization_id="22222222-2222-4222-8222-222222222222",
    )
    monkeypatch.setattr(
        session_view,
        "_project_queryset_for_request",
        lambda _request: project_scope,
    )
    monkeypatch.setattr(session_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        session_view,
        "fetch_session_graph_ch",
        lambda **_kwargs: _sampled_series(metric_name="session_count"),
    )
    view = session_view.TraceSessionView()

    def call(allow_sampled):
        request = SimpleNamespace(
            validated_query_data={"allow_sampled": allow_sampled},
            validated_data={
                "project_id": "11111111-1111-4111-8111-111111111111",
                "filters": [],
                "interval": "day",
                "property": "average",
                "req_data_config": {
                    "id": "session_count",
                    "type": "SYSTEM_METRIC",
                },
            },
        )
        view.request = request
        return unwrap(session_view.TraceSessionView.get_session_graph_data)(
            view,
            request,
        )

    rejected = call(False)
    assert rejected.status_code == 503
    assert rejected.data["result"]["data"] == []
    assert call(True).status_code == 503
