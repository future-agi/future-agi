"""Direct-write routing contracts for customer-facing trace list/detail APIs."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import override_settings

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
PROJECT_VERSION_ID = "00000000-0000-4000-8000-000000000099"


def _legacy_factory():
    return MagicMock(
        side_effect=AssertionError("legacy ClickHouse service must not be used")
    )


@pytest.mark.unit
@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_DISABLED": "TRACE_LIST"})
def test_observe_trace_list_endpoint_binds_v2_service_when_routing_is_disabled(
    monkeypatch,
):
    from tracer.views import trace as trace_view

    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        validated_query_data={"project_id": PROJECT_ID},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    v2_service = object()
    v2_factory = MagicMock(return_value=v2_service)
    legacy_factory = _legacy_factory()
    list_impl = MagicMock(return_value="observe-result")

    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(
        trace_view, "_get_request_organization", lambda _request: organization
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)
    monkeypatch.setattr(
        trace_view.TraceView, "_list_traces_of_session_clickhouse", list_impl
    )
    view = trace_view.TraceView()
    view.request = request

    response = unwrap(trace_view.TraceView.list_traces_of_session)(view, request)

    assert response == "observe-result"
    assert list_impl.call_args.args[3] is v2_service
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()


@pytest.mark.unit
@override_settings(CLICKHOUSE_V2={})
def test_non_observe_trace_list_endpoint_binds_v2_service_without_routing_config(
    monkeypatch,
):
    from tracer.views import trace as trace_view

    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        validated_query_data={"project_version_id": PROJECT_VERSION_ID},
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    project_version_scope = MagicMock()
    project_version_scope.filter.return_value.first.return_value = object()
    v2_service = object()
    v2_factory = MagicMock(return_value=v2_service)
    legacy_factory = _legacy_factory()
    list_impl = MagicMock(return_value="non-observe-result")

    monkeypatch.setattr(
        trace_view,
        "_project_version_queryset_for_request",
        lambda _request: project_version_scope,
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)
    monkeypatch.setattr(trace_view.TraceView, "_list_traces_clickhouse", list_impl)
    view = trace_view.TraceView()
    view.request = request

    response = unwrap(trace_view.TraceView.list_traces)(view, request)

    assert response == "non-observe-result"
    assert list_impl.call_args.args[2] is v2_service
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()


@pytest.mark.unit
@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_DISABLED": "VOICE_CALL_LIST"})
def test_voice_list_endpoint_binds_v2_service_when_routing_is_disabled(monkeypatch):
    from tracer.views import trace as trace_view

    organization = SimpleNamespace(id="org-a")
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "page": 1,
            "page_size": 25,
        },
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    v2_service = object()
    v2_factory = MagicMock(return_value=v2_service)
    legacy_factory = _legacy_factory()
    list_impl = MagicMock(return_value="voice-result")

    monkeypatch.setattr(trace_view.Project.objects, "get", MagicMock())
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)
    monkeypatch.setattr(trace_view.TraceView, "_list_voice_calls_clickhouse", list_impl)
    view = trace_view.TraceView()
    view.request = request

    response = unwrap(trace_view.TraceView.list_voice_calls)(view, request)

    assert response == "voice-result"
    assert list_impl.call_args.args[4] is v2_service
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()


@pytest.mark.unit
@override_settings(CLICKHOUSE_V2={"QUERY_TYPES_DISABLED": "TRACE_DETAIL"})
def test_trace_detail_binds_v2_handler_and_service_when_routing_is_disabled(
    monkeypatch,
):
    from tracer.services.clickhouse.v2.query_builders.trace_detail import (
        TraceDetailHandlerV2,
    )
    from tracer.views import trace as trace_view

    v2_service = object()
    v2_factory = MagicMock(return_value=v2_service)
    legacy_factory = _legacy_factory()
    captured = {}

    def fetch(handler):
        captured["handler"] = handler
        return {"trace": {"id": "trace-1"}}

    monkeypatch.setattr(TraceDetailHandlerV2, "fetch", fetch)
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)
    view = trace_view.TraceView()
    view._gm = SimpleNamespace(success_response=lambda payload: ("ok", payload))
    request = SimpleNamespace()
    view.request = request

    response = view.retrieve(request, pk="trace-1")

    assert response == ("ok", {"trace": {"id": "trace-1"}})
    assert isinstance(captured["handler"], TraceDetailHandlerV2)
    assert captured["handler"].analytics is v2_service
    v2_factory.assert_called_once_with()
    legacy_factory.assert_not_called()


@pytest.mark.unit
def test_annotation_span_map_uses_supplied_v2_service_when_map_is_missing(
    monkeypatch,
):
    from tracer.views import trace as trace_view

    # The candidate trace may have arbitrary physical fanout; only these two
    # PG-proven Score identities are allowed into the ClickHouse membership
    # predicate.
    scored_span_ids = ("span-7", "span-4999")
    span_trace_map = dict.fromkeys(scored_span_ids, "trace-1")
    analytics = MagicMock()
    analytics.get_span_trace_map.return_value = span_trace_map
    pg_builder = MagicMock(return_value={"trace-1": {}})
    v2_factory = MagicMock(
        side_effect=AssertionError("supplied V2 service must be reused")
    )
    legacy_factory = _legacy_factory()
    score_span_reader = MagicMock(return_value=scored_span_ids)
    monkeypatch.setattr(trace_view, "_annotation_score_span_ids", score_span_reader)
    monkeypatch.setattr(trace_view, "_build_annotation_map_from_scores_pg", pg_builder)
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)

    result = trace_view._build_annotation_map_from_scores(
        ["trace-1"],
        ["label-1"],
        {"label-1": "numeric"},
        analytics=analytics,
        project_id=PROJECT_ID,
    )

    assert result == {"trace-1": {}}
    analytics.get_span_trace_map.assert_called_once_with(
        ["trace-1"],
        project_id=PROJECT_ID,
        start_date=None,
        end_date=None,
        scored_span_ids=scored_span_ids,
    )
    pg_builder.assert_called_once_with(
        ["trace-1"],
        ["label-1"],
        {"label-1": "numeric"},
        span_trace_map,
        project_id=PROJECT_ID,
    )
    score_span_reader.assert_called_once_with(["label-1"], PROJECT_ID)
    v2_factory.assert_not_called()
    legacy_factory.assert_not_called()


@pytest.mark.unit
def test_annotation_span_map_fallback_is_explicitly_v2(monkeypatch):
    from tracer.views import trace as trace_view

    analytics = MagicMock()
    analytics.get_span_trace_map.return_value = {}
    v2_factory = MagicMock(return_value=analytics)
    legacy_factory = _legacy_factory()
    monkeypatch.setattr(
        trace_view,
        "_annotation_score_span_ids",
        MagicMock(return_value=("span-1",)),
    )
    monkeypatch.setattr(
        trace_view, "_build_annotation_map_from_scores_pg", MagicMock(return_value={})
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", v2_factory)
    monkeypatch.setattr(trace_view, "AnalyticsQueryService", legacy_factory)

    assert (
        trace_view._build_annotation_map_from_scores(
            ["trace-1"],
            ["label-1"],
            {"label-1": "numeric"},
            project_id=PROJECT_ID,
        )
        == {}
    )

    v2_factory.assert_called_once_with()
    analytics.get_span_trace_map.assert_called_once()
    legacy_factory.assert_not_called()


@pytest.mark.unit
def test_annotation_span_map_skips_clickhouse_without_span_linked_scores(monkeypatch):
    from tracer.views import trace as trace_view

    analytics = MagicMock()
    pg_builder = MagicMock(return_value={"trace-1": {}})
    monkeypatch.setattr(
        trace_view,
        "_annotation_score_span_ids",
        MagicMock(return_value=()),
    )
    monkeypatch.setattr(trace_view, "_build_annotation_map_from_scores_pg", pg_builder)

    assert trace_view._build_annotation_map_from_scores(
        ["trace-1"],
        ["label-1"],
        {"label-1": "numeric"},
        analytics=analytics,
        project_id=PROJECT_ID,
    ) == {"trace-1": {}}

    analytics.get_span_trace_map.assert_not_called()
    pg_builder.assert_called_once_with(
        ["trace-1"],
        ["label-1"],
        {"label-1": "numeric"},
        {},
        project_id=PROJECT_ID,
    )
