import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.models import Q

from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.views import charts as chart_views
from tracer.views import dashboard as dashboard_views
from tracer.views import observation_span as span_views
from tracer.views import trace as trace_views
from tracer.views import trace_session as session_views

PRIVATE_ERROR = "Code: 159. DB::Exception: private internal stack"


def _assert_sanitized(response, *, message, code):
    assert response.status_code == 400
    assert response.data["code"] == code
    assert response.data["message"] == message
    assert response.data["result"] == message
    assert PRIVATE_ERROR not in json.dumps(response.data)
    contract = ApiErrorResponseSerializer(data=response.data)
    assert contract.is_valid(), contract.errors


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (RuntimeError(PRIVATE_ERROR), "query_failed"),
        (TimeoutError(PRIVATE_ERROR), "read_budget_exceeded"),
    ],
)
def test_trace_index_navigation_sanitizes_internal_errors(
    monkeypatch, exc, expected_code
):
    project_query = MagicMock()
    project_query.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    monkeypatch.setattr(
        trace_views, "_project_queryset_for_request", lambda request: project_query
    )
    log = MagicMock()
    monkeypatch.setattr(trace_views, "logger", log)

    view = trace_views.TraceView()
    view._get_trace_id_by_index_observe_clickhouse = MagicMock(side_effect=exc)
    request = SimpleNamespace(
        validated_query_data={
            "trace_id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "filters": [],
        }
    )

    response = trace_views.TraceView.get_trace_id_by_index_observe.__wrapped__(
        view, request
    )

    _assert_sanitized(
        response,
        message="Trace navigation could not be completed. Please try again.",
        code=expected_code,
    )
    assert log.exception.call_args.kwargs["error"] == PRIVATE_ERROR


@pytest.mark.parametrize(
    ("method_name", "validated_query_data", "manager"),
    [
        (
            "get_trace_id_by_index_spans_as_base",
            {
                "span_id": "private-span",
                "project_version_id": uuid.uuid4(),
                "filters": [],
            },
            span_views.ProjectVersion.objects,
        ),
        (
            "get_trace_id_by_index_spans_as_observe",
            {
                "span_id": "private-span",
                "project_id": uuid.uuid4(),
                "filters": [],
            },
            span_views.Project.objects,
        ),
    ],
)
def test_span_index_navigation_sanitizes_internal_errors(
    monkeypatch, method_name, validated_query_data, manager
):
    organization_id = uuid.uuid4()
    monkeypatch.setattr(
        span_views, "_project_workspace_scope_q", lambda *args, **kwargs: Q()
    )
    monkeypatch.setattr(
        span_views, "_get_request_organization", lambda request: organization_id
    )
    monkeypatch.setattr(
        type(manager),
        "get",
        MagicMock(side_effect=RuntimeError(PRIVATE_ERROR)),
    )
    log = MagicMock()
    monkeypatch.setattr(span_views, "logger", log)

    view = span_views.ObservationSpanView()
    request = SimpleNamespace(
        validated_query_data=validated_query_data,
        user=SimpleNamespace(organization=organization_id),
    )
    method = getattr(span_views.ObservationSpanView, method_name)

    response = method.__wrapped__(view, request)

    _assert_sanitized(
        response,
        message="Span navigation could not be completed. Please try again.",
        code="query_failed",
    )
    assert PRIVATE_ERROR in log.exception.call_args.args[0]


def test_session_export_sanitizes_internal_errors(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(session_views, "logger", log)

    view = session_views.TraceSessionView()
    view.list_sessions = MagicMock(side_effect=RuntimeError(PRIVATE_ERROR))
    request = SimpleNamespace(query_params={"project_id": str(uuid.uuid4())})

    response = view.get_trace_session_export_data(request)

    _assert_sanitized(
        response,
        message="Session export could not be completed. Please try again.",
        code="query_failed",
    )
    assert log.exception.call_args.kwargs["error"] == PRIVATE_ERROR


def test_session_eval_logs_sanitizes_internal_errors(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(session_views, "logger", log)

    view = session_views.TraceSessionView()
    view.kwargs = {"pk": str(uuid.uuid4())}
    view.get_object = MagicMock(side_effect=RuntimeError(PRIVATE_ERROR))
    request = SimpleNamespace(query_params={})

    response = view.eval_logs(request)

    _assert_sanitized(
        response,
        message="Session evaluation logs could not be loaded. Please try again.",
        code="query_failed",
    )
    assert PRIVATE_ERROR in log.exception.call_args.args[0]


@pytest.mark.parametrize("method_name", ["execute_query", "preview_query"])
def test_dashboard_query_endpoints_sanitize_internal_errors(monkeypatch, method_name):
    monkeypatch.setattr(dashboard_views, "is_clickhouse_enabled", lambda: True)
    log = MagicMock()
    monkeypatch.setattr(dashboard_views, "logger", log)

    view = dashboard_views.DashboardWidgetViewSet()
    query_config = {"metrics": [{"id": "latency"}]}
    view.get_object = MagicMock(return_value=SimpleNamespace(query_config=query_config))
    view._execute_ch_query_config = MagicMock(side_effect=RuntimeError(PRIVATE_ERROR))
    request = SimpleNamespace(
        workspace=object(),
        validated_data={"query_config": query_config},
    )
    method = getattr(dashboard_views.DashboardWidgetViewSet, method_name)

    response = method.__wrapped__(view, request)

    _assert_sanitized(
        response,
        message="Dashboard query could not be completed. Please try again.",
        code="query_failed",
    )
    assert log.error.call_args.kwargs["error"] == PRIVATE_ERROR
    assert log.error.call_args.kwargs["exc_info"] is True


def test_trace_eval_names_sanitizes_internal_errors(monkeypatch):
    project_query = MagicMock()
    project_query.filter.return_value.first.return_value = SimpleNamespace(
        trace_type="observe"
    )
    monkeypatch.setattr(
        trace_views, "_project_queryset_for_request", lambda request: project_query
    )
    monkeypatch.setattr(
        trace_views,
        "AnalyticsQueryService",
        MagicMock(side_effect=RuntimeError(PRIVATE_ERROR)),
    )
    log = MagicMock()
    monkeypatch.setattr(trace_views, "logger", log)

    request = SimpleNamespace(query_params={"project_id": str(uuid.uuid4())})
    view = trace_views.TraceView()
    view.request = request

    response = view.get_eval_names(request)

    _assert_sanitized(
        response,
        message="Evaluation names could not be loaded. Please try again.",
        code="query_failed",
    )
    assert log.exception.call_args.kwargs["error_type"] == "RuntimeError"


def test_span_evaluation_details_sanitizes_internal_errors(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(span_views, "logger", log)
    monkeypatch.setattr(
        span_views.ObservationSpanView,
        "_get_evaluation_details_clickhouse",
        MagicMock(side_effect=RuntimeError(PRIVATE_ERROR)),
    )

    request = SimpleNamespace(
        query_params={
            "observation_span_id": str(uuid.uuid4()),
            "custom_eval_config_id": str(uuid.uuid4()),
        }
    )
    view = span_views.ObservationSpanView()
    view.request = request

    response = view.get_evaluation_details(request)

    _assert_sanitized(
        response,
        message="Evaluation details could not be loaded. Please try again.",
        code="query_failed",
    )
    assert log.exception.call_args.kwargs["error_type"] == "RuntimeError"


def test_chart_graph_sanitizes_internal_errors(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(chart_views, "logger", log)

    view = chart_views.ChartsView()
    view.serializer_class = MagicMock(side_effect=RuntimeError(PRIVATE_ERROR))
    request = SimpleNamespace(query_params={})

    response = view.fetch_graph(request)

    _assert_sanitized(
        response,
        message="Graph data could not be loaded. Please try again.",
        code="query_failed",
    )
    assert log.exception.call_args.kwargs["error_type"] == "RuntimeError"
