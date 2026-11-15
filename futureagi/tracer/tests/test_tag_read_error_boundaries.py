"""Tag query validation is actionable; transient source errors stay retryable."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests.test_effective_tag_filters import leaf
from tracer.tests.test_effective_tag_list_operators import post
from tracer.tests.test_entity_tag_target import PROJECT
from tracer.views import observation_span as span_views
from tracer.views import trace as trace_views


@pytest.mark.parametrize("kind", ["trace", "span", "voice"])
@pytest.mark.parametrize(
    "code,expected_status,expected_code",
    [
        ("tag_overlay_budget_exceeded", 400, "tag_filter_too_broad"),
        ("tag_candidate_budget_exceeded", 400, "tag_filter_too_broad"),
        ("tag_assignment_unavailable", 503, "service_unavailable"),
    ],
)
def test_public_lists_distinguish_invalid_tag_size_from_outage(
    monkeypatch, kind, code, expected_status, expected_code
):
    def fail(*args, **kwargs):
        raise TagReadUnavailable(code)

    if kind in {"trace", "span"}:
        view, method = (
            (trace_views.TraceView, "_list_traces_of_session_clickhouse")
            if kind == "trace"
            else (span_views.ObservationSpanView, "_list_spans_clickhouse")
        )
        monkeypatch.setattr(view, method, fail)
        monkeypatch.setattr(trace_views, "V2AnalyticsQueryService", MagicMock())
        monkeypatch.setattr(span_views, "V2AnalyticsQueryService", MagicMock())
        response = post(None, monkeypatch, kind, leaf("tag", ["wanted"], "in"))
    else:
        project = SimpleNamespace(id=PROJECT, trace_type="observe")
        monkeypatch.setattr(
            trace_views,
            "_project_queryset_for_request",
            lambda request: SimpleNamespace(
                filter=lambda **kwargs: SimpleNamespace(first=lambda: project)
            ),
        )
        monkeypatch.setattr(trace_views, "V2AnalyticsQueryService", MagicMock())
        monkeypatch.setattr(
            trace_views.Project.objects, "get", lambda *a, **kw: project
        )
        monkeypatch.setattr(trace_views.TraceView, "_list_voice_calls_clickhouse", fail)
        request = Request(
            APIRequestFactory().post(
                "/", {"project_id": PROJECT, "filters": []}, format="json"
            ),
            parsers=[JSONParser()],
        )
        request.organization = SimpleNamespace(id="org")
        request.workspace = SimpleNamespace(id="ws", organization_id="org")
        view = trace_views.TraceView()
        view.request = request
        response = view.list_voice_calls(request)
    assert response.status_code == expected_status, response.data
    assert response.data["code"] == expected_code
    if expected_status == 400:
        assert "retry" not in str(response.data).lower()
        assert "Tag filter" in str(response.data)


@pytest.mark.parametrize("kind", ["trace", "span"])
@pytest.mark.parametrize(
    "code,expected_status",
    [("tag_overlay_budget_exceeded", 400), ("tag_assignment_unavailable", 503)],
)
def test_navigation_tag_errors_keep_their_public_type(kind, code, expected_status):
    request = SimpleNamespace(
        validated_query_data={
            "trace_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "span_id": "current",
            "project_id": PROJECT,
            "user_id": None,
            "filters": [],
        }
    )
    error = TagReadUnavailable(code)
    if kind == "trace":
        scope = MagicMock()
        scope.filter.return_value.first.return_value = SimpleNamespace(
            trace_type="observe"
        )
        with (
            patch.object(
                trace_views, "_project_queryset_for_request", return_value=scope
            ),
            patch.object(trace_views, "V2AnalyticsQueryService"),
            patch.object(
                trace_views.TraceView,
                "_get_trace_id_by_index_observe_clickhouse",
                side_effect=error,
            ),
        ):
            response = trace_views.TraceView.get_trace_id_by_index_observe.__wrapped__(
                trace_views.TraceView(), request
            )
    else:
        with (
            patch.object(
                span_views, "_project_workspace_scope_q", return_value=MagicMock()
            ),
            patch.object(
                span_views, "_get_request_organization", return_value=MagicMock()
            ),
            patch.object(
                span_views.Project.objects,
                "get",
                return_value=SimpleNamespace(trace_type="observe"),
            ),
            patch.object(
                span_views.ObservationSpanView,
                "_bounded_span_navigation_response",
                side_effect=error,
            ),
        ):
            response = span_views.ObservationSpanView.get_trace_id_by_index_spans_as_observe.__wrapped__(
                span_views.ObservationSpanView(), request
            )
    assert response.status_code == expected_status, response.data
    assert response.data["code"] == (
        "tag_filter_too_broad" if expected_status == 400 else "service_unavailable"
    )
