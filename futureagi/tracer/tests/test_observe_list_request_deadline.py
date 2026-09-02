"""Request-owned wall coverage for trace/span list actions."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.db import OperationalError

from tracer.services.clickhouse import list_request_deadline as list_deadline
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


@pytest.mark.unit
def test_list_postgres_statements_receive_a_shrinking_request_timeout(monkeypatch):
    installed = {}
    raw_cursor = mock.MagicMock()

    @contextmanager
    def install_wrapper(wrapper):
        installed["wrapper"] = wrapper
        yield

    fake_connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=True,
        execute_wrapper=install_wrapper,
    )
    monkeypatch.setattr(list_deadline, "connection", fake_connection)
    deadline = mock.MagicMock()
    deadline.remaining_ms.side_effect = [9_000, 8_500, 7_000, 6_500, 6_000]
    executed = []

    with list_deadline.bounded_list_postgres_reads(deadline):
        wrapper = installed["wrapper"]
        context = {"cursor": SimpleNamespace(cursor=raw_cursor)}

        def execute(sql, params, many, _context):
            executed.append((sql, params, many))
            return sql

        assert wrapper(execute, "SELECT first", (), False, context) == "SELECT first"
        assert wrapper(execute, "SELECT second", (), False, context) == "SELECT second"

    assert raw_cursor.execute.call_args_list == [
        mock.call(
            "SELECT set_config('statement_timeout', %s, true)",
            ("9000",),
        ),
        mock.call(
            "SELECT set_config('statement_timeout', %s, true)",
            ("7000",),
        ),
    ]
    assert [item[0] for item in executed] == ["SELECT first", "SELECT second"]


@pytest.mark.unit
def test_list_postgres_driver_failure_becomes_typed_deadline_without_sql_text():
    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 8_000
    context = {"cursor": SimpleNamespace(cursor=mock.MagicMock())}

    with pytest.raises(ReadDeadlineExceeded) as caught:
        list_deadline._execute_list_postgres_query_with_deadline(
            deadline,
            mock.MagicMock(
                side_effect=OperationalError("private tenant SQL and credentials")
            ),
            "SELECT private",
            (),
            False,
            context,
        )

    assert "private" not in str(caught.value)


def _request():
    organization = SimpleNamespace(id="22222222-2222-4222-8222-222222222222")
    return SimpleNamespace(
        method="GET",
        data={},
        query_params={},
        validated_query_data={},
        organization=organization,
        workspace=SimpleNamespace(
            id="33333333-3333-4333-8333-333333333333", is_default=False
        ),
        user=SimpleNamespace(id="user-1", organization=organization),
    )


def _call_prototype_traces(monkeypatch, events, deadline):
    from tracer.views import trace as trace_view

    request = _request()
    request.validated_query_data = {
        "project_version_id": "project-version-1",
        "filters": [],
    }
    request.query_params = dict(request.validated_query_data)
    project_scope = mock.MagicMock()
    project_scope.filter.return_value.first.side_effect = lambda: (
        events.append("postgres_scope") or object()
    )
    monkeypatch.setattr(
        trace_view,
        "_project_version_queryset_for_request",
        lambda _request: project_scope,
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", mock.MagicMock)

    def dispatch(*_args, **kwargs):
        events.append("clickhouse_dispatch")
        assert kwargs["read_deadline"] is deadline
        return "prototype-traces"

    monkeypatch.setattr(trace_view.TraceView, "_list_traces_clickhouse", dispatch)
    view = trace_view.TraceView()
    view.request = request
    return trace_view.TraceView.list_traces(view, request)


def _call_observe_traces(monkeypatch, events, deadline):
    from tracer.views import trace as trace_view

    request = _request()
    request.validated_query_data = {
        "project_id": "project-1",
        "filters": [],
    }
    request.query_params = dict(request.validated_query_data)
    project_scope = mock.MagicMock()
    project_scope.filter.return_value.first.side_effect = lambda: (
        events.append("postgres_scope") or SimpleNamespace(trace_type="observe")
    )
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(
        trace_view, "_get_request_organization", lambda _request: request.organization
    )
    monkeypatch.setattr(
        trace_view, "bind_request_my_annotations_principal", lambda _r, filters: filters
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", mock.MagicMock)

    def dispatch(*_args, **kwargs):
        events.append("clickhouse_dispatch")
        assert kwargs["read_deadline"] is deadline
        return "observe-traces"

    monkeypatch.setattr(
        trace_view.TraceView, "_list_traces_of_session_clickhouse", dispatch
    )
    view = trace_view.TraceView()
    view.request = request
    return trace_view.TraceView.list_traces_of_session(view, request)


def _call_voice_calls(monkeypatch, events, deadline):
    from tracer.views import trace as trace_view

    request = _request()
    request.validated_query_data = {
        "project_id": "project-1",
        "filters": [],
        "page": 1,
        "page_size": 30,
    }
    request.query_params = dict(request.validated_query_data)
    monkeypatch.setattr(
        trace_view.Project.objects,
        "get",
        lambda *_args, **_kwargs: events.append("postgres_scope") or object(),
    )
    monkeypatch.setattr(
        trace_view, "bind_request_my_annotations_principal", lambda _r, filters: filters
    )
    monkeypatch.setattr(trace_view, "V2AnalyticsQueryService", mock.MagicMock)

    def dispatch(*_args, **kwargs):
        events.append("clickhouse_dispatch")
        assert kwargs["read_deadline"] is deadline
        return "voice-calls"

    monkeypatch.setattr(trace_view.TraceView, "_list_voice_calls_clickhouse", dispatch)
    view = trace_view.TraceView()
    view.request = request
    return trace_view.TraceView.list_voice_calls(view, request)


def _call_prototype_spans(monkeypatch, events, deadline):
    from tracer.views import observation_span as span_view

    request = _request()
    serializer = mock.MagicMock()
    serializer.is_valid.return_value = True
    serializer.validated_data = {
        "project_version_id": "project-version-1",
        "filters": [],
    }
    request.query_params = dict(serializer.validated_data)
    monkeypatch.setattr(
        span_view, "SpanListQuerySerializer", lambda **_kwargs: serializer
    )
    monkeypatch.setattr(
        span_view.ProjectVersion.objects,
        "get",
        lambda *_args, **_kwargs: (
            events.append("postgres_scope") or SimpleNamespace(project_id="project-1")
        ),
    )
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: request.organization
    )
    monkeypatch.setattr(
        span_view, "bind_request_my_annotations_principal", lambda _r, filters: filters
    )
    monkeypatch.setattr(span_view, "V2AnalyticsQueryService", mock.MagicMock)

    def dispatch(*_args, **kwargs):
        events.append("clickhouse_dispatch")
        assert kwargs["read_deadline"] is deadline
        return "prototype-spans"

    monkeypatch.setattr(
        span_view.ObservationSpanView,
        "_list_spans_non_observe_clickhouse",
        dispatch,
    )
    view = span_view.ObservationSpanView()
    view.request = request
    return span_view.ObservationSpanView.list_spans(view, request)


def _call_observe_spans(monkeypatch, events, deadline):
    from tracer.views import observation_span as span_view

    request = _request()
    request.validated_query_data = {
        "project_id": "project-1",
        "filters": [],
        "page_number": 0,
        "page_size": 30,
    }
    request.query_params = dict(request.validated_query_data)
    monkeypatch.setattr(
        span_view.Project.objects,
        "get",
        lambda *_args, **_kwargs: events.append("postgres_scope") or object(),
    )
    monkeypatch.setattr(
        span_view, "_project_workspace_scope_q", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        span_view, "_get_request_organization", lambda _request: request.organization
    )
    monkeypatch.setattr(
        span_view, "bind_request_my_annotations_principal", lambda _r, filters: filters
    )
    monkeypatch.setattr(span_view, "V2AnalyticsQueryService", mock.MagicMock)

    def dispatch(*_args, **kwargs):
        events.append("clickhouse_dispatch")
        assert kwargs["read_deadline"] is deadline
        return "observe-spans"

    monkeypatch.setattr(
        span_view.ObservationSpanView, "_list_spans_clickhouse", dispatch
    )
    view = span_view.ObservationSpanView()
    view.request = request
    return span_view.ObservationSpanView.list_spans_observe(view, request)


@pytest.mark.unit
@pytest.mark.parametrize(
    "call_boundary",
    [
        _call_prototype_traces,
        _call_observe_traces,
        _call_voice_calls,
        _call_prototype_spans,
        _call_observe_spans,
    ],
    ids=[
        "prototype-traces",
        "observe-traces",
        "voice-calls",
        "prototype-spans",
        "observe-spans",
    ],
)
def test_list_deadline_precedes_tenant_scope_and_reaches_clickhouse(
    monkeypatch, call_boundary
):
    from tfc.utils import api_contracts

    events = []
    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 8_000

    def start(wall_ms):
        assert wall_ms == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
        events.append("deadline_started")
        return deadline

    @contextmanager
    def postgres_scope(received_deadline):
        assert received_deadline is deadline
        events.append("postgres_budget_entered")
        try:
            yield
        finally:
            events.append("postgres_budget_exited")

    monkeypatch.setattr(list_deadline.ReadDeadline, "start", start)
    monkeypatch.setattr(list_deadline, "bounded_list_postgres_reads", postgres_scope)

    def validate(_serializer_class, data, **_kwargs):
        events.append("request_validation")
        return SimpleNamespace(validated_data=dict(data)), {}, True

    monkeypatch.setattr(api_contracts, "_validate_serializer", validate)

    response = call_boundary(monkeypatch, events, deadline)

    assert response in {
        "prototype-traces",
        "observe-traces",
        "voice-calls",
        "prototype-spans",
        "observe-spans",
    }
    assert events == [
        "deadline_started",
        "postgres_budget_entered",
        "request_validation",
        "postgres_scope",
        "clickhouse_dispatch",
        "postgres_budget_exited",
    ]
    deadline.remaining_ms.assert_called_once_with(floor_ms=1)


@pytest.mark.unit
def test_expired_scope_blocks_list_dispatch_and_returns_sanitized_503(monkeypatch):
    from tracer.views import trace as trace_view

    deadline = mock.MagicMock()
    monkeypatch.setattr(list_deadline.ReadDeadline, "start", lambda _wall: deadline)

    @contextmanager
    def expired_scope(_deadline):
        raise ReadDeadlineExceeded("private PostgreSQL scope details")
        yield

    monkeypatch.setattr(list_deadline, "bounded_list_postgres_reads", expired_scope)
    dispatch = mock.MagicMock()
    monkeypatch.setattr(trace_view.TraceView, "_list_traces_clickhouse", dispatch)
    request = _request()
    view = trace_view.TraceView()
    view.request = request
    view._gm = SimpleNamespace(
        custom_error_response=lambda response_status, message, code: SimpleNamespace(
            status_code=response_status,
            data={"message": message, "code": code},
        )
    )

    response = trace_view.TraceView.list_traces(view, request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert "temporarily unavailable" in response.data["message"]
    assert "private" not in str(response.data)
    dispatch.assert_not_called()


@pytest.mark.unit
def test_late_list_result_is_not_published_as_success(monkeypatch):
    deadline = mock.MagicMock()
    deadline.remaining_ms.side_effect = ReadDeadlineExceeded("private formatter timing")
    monkeypatch.setattr(list_deadline.ReadDeadline, "start", lambda _wall: deadline)

    @contextmanager
    def open_scope(_deadline):
        yield

    monkeypatch.setattr(
        list_deadline,
        "bounded_list_postgres_reads",
        open_scope,
    )

    class GeneralMethods:
        @staticmethod
        def custom_error_response(response_status, message, code):
            return SimpleNamespace(
                status_code=response_status,
                data={"message": message, "code": code},
            )

    @list_deadline.bounded_list_request(
        wall_ms=9_500,
        resource="test",
        unavailable_message="Rows are temporarily unavailable. Please retry.",
    )
    def late_success(_view, _request, **_kwargs):
        return SimpleNamespace(status_code=200, data={"private": "stale"})

    response = late_success(
        SimpleNamespace(_gm=GeneralMethods()),
        SimpleNamespace(),
    )

    assert response.status_code == 503
    assert response.data == {
        "message": "Rows are temporarily unavailable. Please retry.",
        "code": "service_unavailable",
    }
