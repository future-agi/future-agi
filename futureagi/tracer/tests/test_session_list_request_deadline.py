"""Request-owned deadline coverage for the public session list."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from inspect import unwrap
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.mark.unit
def test_session_list_public_wall_starts_before_runtime_validation(monkeypatch):
    from tfc.utils import api_contracts
    from tracer.views import trace_session as trace_session_view

    events = []
    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 9_000

    @contextmanager
    def bounded_scope(received_deadline):
        assert received_deadline is deadline
        events.append("postgres_scope_entered")
        yield

    def reject_query(*_args, **_kwargs):
        events.append("runtime_validation")
        serializer = SimpleNamespace(validated_data={})
        return serializer, {"page_size": ["invalid"]}, False

    request = SimpleNamespace(method="GET", data={}, query_params={"page_size": 0})
    view = trace_session_view.TraceSessionView.__new__(
        trace_session_view.TraceSessionView
    )

    with (
        mock.patch.object(
            trace_session_view.ReadDeadline,
            "start",
            side_effect=lambda _budget: events.append("deadline_started") or deadline,
        ),
        mock.patch.object(
            trace_session_view,
            "_bounded_session_list_postgres_reads",
            side_effect=bounded_scope,
        ),
        mock.patch.object(api_contracts, "_validate_serializer", reject_query),
    ):
        response = trace_session_view.TraceSessionView.list_sessions(view, request)

    assert response.status_code == 400
    assert events == [
        "deadline_started",
        "postgres_scope_entered",
        "runtime_validation",
    ]


@pytest.mark.unit
def test_unwrapped_session_list_original_acquires_fallback_deadline(monkeypatch):
    from tracer.views import trace_session as trace_session_view

    project_id = str(uuid.uuid4())
    organization = SimpleNamespace(id=uuid.uuid4())
    request = SimpleNamespace(
        validated_query_data={
            "project_id": project_id,
            "filters": [],
            "page_number": 0,
            "page_size": 25,
        },
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )
    view = trace_session_view.TraceSessionView.__new__(
        trace_session_view.TraceSessionView
    )
    view.request = request
    deadline = mock.MagicMock()
    project_queryset = mock.MagicMock()
    project_queryset.get.return_value = SimpleNamespace(
        id=project_id,
        source="observe",
    )
    expected = object()

    with (
        mock.patch.object(
            trace_session_view.ReadDeadline,
            "start",
            return_value=deadline,
        ) as start_deadline,
        mock.patch.object(
            trace_session_view,
            "_project_queryset_for_request",
            return_value=project_queryset,
        ),
        mock.patch.object(
            trace_session_view,
            "V2AnalyticsQueryService",
            return_value=mock.MagicMock(),
        ),
        mock.patch.object(view, "_build_bookmark_filter", return_value=None),
        mock.patch.object(
            view,
            "_list_sessions_clickhouse",
            return_value=expected,
        ) as list_clickhouse,
    ):
        response = unwrap(trace_session_view.TraceSessionView.list_sessions)(
            view,
            request,
        )

    assert response is expected
    start_deadline.assert_called_once_with(
        trace_session_view.SESSION_LIST_WALL_DEADLINE_MS
    )
    assert list_clickhouse.call_args.kwargs["read_deadline"] is deadline


@pytest.mark.unit
def test_session_list_postgres_statement_uses_shrinking_request_timeout():
    from tracer.views.trace_session import _execute_session_list_query_with_deadline

    deadline = mock.MagicMock()
    deadline.remaining_ms.side_effect = [7_321, 6_123]
    driver_cursor = mock.MagicMock()
    context = {"cursor": SimpleNamespace(cursor=driver_cursor)}
    execute = mock.MagicMock(return_value="result")

    result = _execute_session_list_query_with_deadline(
        deadline,
        execute,
        "SELECT 1",
        (),
        False,
        context,
    )

    assert result == "result"
    driver_cursor.execute.assert_called_once_with(
        "SELECT set_config('statement_timeout', %s, true)",
        ("7321",),
    )
    execute.assert_called_once_with("SELECT 1", (), False, context)
    assert deadline.remaining_ms.call_args_list == [
        mock.call(floor_ms=1),
        mock.call(floor_ms=1),
    ]


@pytest.mark.unit
def test_session_list_deadline_precedes_scope_and_reaches_all_read_phases():
    from tracer.views import trace_session as trace_session_view

    project_id = str(uuid.uuid4())
    organization = SimpleNamespace(id=uuid.uuid4())
    workspace = SimpleNamespace(id=uuid.uuid4(), is_default=False)
    request = SimpleNamespace(
        method="GET",
        data={},
        query_params={"project_id": project_id},
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
    )
    view = trace_session_view.TraceSessionView.__new__(
        trace_session_view.TraceSessionView
    )
    view.request = request
    view._gm = SimpleNamespace(
        bad_request=lambda message: ("bad_request", message),
        custom_error_response=lambda status, message, code: (
            "error",
            status,
            message,
            code,
        ),
    )

    deadline = mock.MagicMock()
    deadline.remaining_ms.return_value = 9_000
    scope_active = False

    @contextmanager
    def bounded_scope(received_deadline):
        nonlocal scope_active
        assert received_deadline is deadline
        scope_active = True
        try:
            yield
        finally:
            scope_active = False

    project = SimpleNamespace(id=project_id, source="observe")
    project_queryset = mock.MagicMock()

    def get_project(**_kwargs):
        assert scope_active is True
        return project

    project_queryset.get.side_effect = get_project
    expected = object()

    with (
        mock.patch.object(
            trace_session_view.ReadDeadline,
            "start",
            return_value=deadline,
        ) as start_deadline,
        mock.patch.object(
            trace_session_view,
            "_bounded_session_list_postgres_reads",
            side_effect=bounded_scope,
        ),
        mock.patch.object(
            trace_session_view,
            "_project_queryset_for_request",
            return_value=project_queryset,
        ),
        mock.patch.object(
            trace_session_view,
            "V2AnalyticsQueryService",
            return_value=mock.MagicMock(),
        ),
        mock.patch.object(
            view,
            "_build_bookmark_filter",
            return_value=None,
        ) as bookmark_filter,
        mock.patch.object(
            view,
            "_list_sessions_clickhouse",
            return_value=expected,
        ) as list_clickhouse,
    ):
        response = trace_session_view.TraceSessionView.list_sessions(view, request)

    assert response is expected
    start_deadline.assert_called_once_with(
        trace_session_view.SESSION_LIST_WALL_DEADLINE_MS
    )
    assert bookmark_filter.call_args.kwargs["deadline"] is deadline
    assert list_clickhouse.call_args.kwargs["read_deadline"] is deadline
    assert scope_active is False
