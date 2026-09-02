"""Public-boundary ordering contracts for trace/span list and graph actions."""

from __future__ import annotations

from contextlib import contextmanager
from inspect import unwrap
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings as django_settings

from tfc.utils import api_contracts
from tracer.services.clickhouse import graph_action_deadline as graph_deadline
from tracer.services.clickhouse import list_request_deadline as list_deadline


def _request(*, method: str):
    organization = SimpleNamespace(id="22222222-2222-4222-8222-222222222222")
    return SimpleNamespace(
        method=method,
        data={"invalid": True},
        query_params={"invalid": True},
        organization=organization,
        workspace=SimpleNamespace(
            id="33333333-3333-4333-8333-333333333333", is_default=False
        ),
        user=SimpleNamespace(id="user-1", organization=organization),
    )


def _forbid_database_entry(monkeypatch):
    from tracer.views import observation_span as span_view
    from tracer.views import trace as trace_view

    forbidden = mock.MagicMock(
        side_effect=AssertionError("invalid validation must not reach database scope")
    )
    monkeypatch.setattr(trace_view, "_project_queryset_for_request", forbidden)
    monkeypatch.setattr(trace_view, "_project_version_queryset_for_request", forbidden)
    monkeypatch.setattr(trace_view.Project.objects, "get", forbidden)
    monkeypatch.setattr(span_view.Project.objects, "get", forbidden)
    monkeypatch.setattr(span_view.ProjectVersion.objects, "get", forbidden)
    return forbidden


def _list_actions():
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.trace import TraceView

    return [
        TraceView.list_traces,
        TraceView.list_traces_of_session,
        TraceView.list_voice_calls,
        ObservationSpanView.list_spans,
        ObservationSpanView.list_spans_observe,
    ]


def _graph_actions():
    from tracer.views.observation_span import ObservationSpanView
    from tracer.views.trace import TraceView

    return [
        TraceView.get_graph_methods,
        ObservationSpanView.get_graph_methods,
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("action_method", "http_method"),
    [pytest.param(method, "get", id=method.__qualname__) for method in _list_actions()]
    + [
        pytest.param(method, "post", id=method.__qualname__)
        for method in _graph_actions()
    ],
)
def test_outer_deadline_preserves_action_swagger_and_unwrapped_metadata(
    action_method, http_method
):
    original = action_method.__wrapped__

    assert unwrap(action_method) is original
    assert action_method.__name__ == original.__name__
    assert action_method.__doc__ == original.__doc__
    assert action_method.detail is False
    assert set(action_method.mapping) == {http_method}
    assert action_method.url_path == action_method.__name__
    assert action_method.url_name == action_method.__name__.replace("_", "-")
    assert action_method.kwargs["description"] == action_method.__doc__
    swagger_contract = action_method._swagger_auto_schema[http_method]
    assert swagger_contract["runtime_request_validation"] is True
    assert swagger_contract["runtime_response_validation"] is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "action_method",
    [pytest.param(method, id=method.__qualname__) for method in _list_actions()],
)
def test_list_wall_starts_before_invalid_validation_without_database_access(
    monkeypatch, action_method
):
    events = []
    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 8_000
    forbidden = _forbid_database_entry(monkeypatch)

    def start(wall_ms):
        assert wall_ms == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
        events.append("deadline_started")
        return deadline

    @contextmanager
    def install_execute_wrapper(_wrapper):
        events.append("postgres_wrapper_entered")
        try:
            yield
        finally:
            events.append("postgres_wrapper_exited")

    def invalid_validation(*_args, **_kwargs):
        events.append("request_validation")
        return SimpleNamespace(validated_data={}), {"invalid": ["bad"]}, False

    monkeypatch.setattr(list_deadline.ReadDeadline, "start", start)
    monkeypatch.setattr(
        list_deadline,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=False,
            execute_wrapper=install_execute_wrapper,
        ),
    )
    atomic = mock.MagicMock(
        side_effect=AssertionError("invalid validation must not open a transaction")
    )
    monkeypatch.setattr(list_deadline.transaction, "atomic", atomic)
    monkeypatch.setattr(api_contracts, "_validate_serializer", invalid_validation)
    request = _request(method="GET")
    view = action_method.__qualname__.startswith("TraceView.")
    if view:
        from tracer.views.trace import TraceView

        view = TraceView()
    else:
        from tracer.views.observation_span import ObservationSpanView

        view = ObservationSpanView()
    view.request = request

    response = action_method(view, request)

    assert response.status_code == 400
    assert events == [
        "deadline_started",
        "postgres_wrapper_entered",
        "request_validation",
        "postgres_wrapper_exited",
    ]
    assert deadline.remaining_ms.call_args_list == [
        mock.call(floor_ms=1),
        mock.call(floor_ms=1),
    ]
    atomic.assert_not_called()
    forbidden.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "action_method",
    [pytest.param(method, id=method.__qualname__) for method in _graph_actions()],
)
def test_graph_wall_starts_before_body_validation_without_database_access(
    monkeypatch, action_method
):
    from tracer.views import observation_span as span_view
    from tracer.views import trace as trace_view

    events = []
    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 8_000
    forbidden = _forbid_database_entry(monkeypatch)

    def start():
        events.append("deadline_started")
        return deadline

    validations = iter(
        [
            (SimpleNamespace(validated_data={"allow_sampled": False}), {}, True),
            (SimpleNamespace(validated_data={}), {"invalid": ["bad"]}, False),
        ]
    )

    def validate(*_args, **_kwargs):
        index = events.count("query_validation") + events.count("body_validation")
        events.append("query_validation" if index == 0 else "body_validation")
        return next(validations)

    monkeypatch.setattr(graph_deadline, "start_graph_action_deadline", start)
    monkeypatch.setattr(api_contracts, "_validate_serializer", validate)
    monkeypatch.setattr(trace_view, "start_graph_action_deadline", forbidden)
    monkeypatch.setattr(span_view, "start_graph_action_deadline", forbidden)
    request = _request(method="POST")
    if action_method.__qualname__.startswith("TraceView."):
        view = trace_view.TraceView()
    else:
        view = span_view.ObservationSpanView()
    view.request = request

    response = action_method(view, request)

    assert response.status_code == 400
    assert events == [
        "deadline_started",
        "query_validation",
        "body_validation",
    ]
    forbidden.assert_not_called()
