"""Dispatch-to-response deadline coverage for dashboard query actions."""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.db import DatabaseError

from tracer.services.clickhouse import dashboard_action_deadline as action_deadline
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


class _SequencedDeadline:
    def __init__(self, values, events=None):
        self._values = iter(values)
        self.events = events if events is not None else []

    def remaining_ms(self, cap_ms=None, *, floor_ms=25):
        value = next(self._values)
        self.events.append(("remaining", value))
        if isinstance(value, BaseException):
            raise value
        if value < floor_ms:
            raise ReadDeadlineExceeded("private dashboard deadline")
        return min(value, cap_ms) if cap_ms is not None else value


def _request(*, data=None):
    organization = SimpleNamespace(id="22222222-2222-4222-8222-222222222222")
    workspace = SimpleNamespace(
        id="33333333-3333-4333-8333-333333333333",
        organization_id=organization.id,
        organization=organization,
        is_default=False,
    )
    return SimpleNamespace(
        method="POST",
        query_params={},
        data=data or {},
        workspace=workspace,
        organization=organization,
        user=SimpleNamespace(organization=organization),
    )


def _response_gm():
    return SimpleNamespace(
        custom_error_response=lambda response_status, message, code: SimpleNamespace(
            status_code=response_status,
            data={"result": message, "code": code},
        ),
        success_response=lambda result: SimpleNamespace(
            status_code=200,
            data={"result": result},
        ),
        bad_request=lambda result: SimpleNamespace(
            status_code=400,
            data={"result": result},
        ),
    )


@pytest.mark.unit
def test_dashboard_action_wall_uses_configured_interactive_deadline():
    assert (
        action_deadline.DASHBOARD_ACTION_WALL_DEADLINE_MS
        == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


@pytest.mark.unit
def test_dashboard_postgres_budget_is_lazy_and_shrinks_every_statement(monkeypatch):
    installed = {}
    atomic_entries = []
    raw_cursor = mock.MagicMock()

    @contextmanager
    def install_wrapper(wrapper):
        installed["wrapper"] = wrapper
        yield

    @contextmanager
    def atomic():
        atomic_entries.append("entered")
        yield

    fake_connection = SimpleNamespace(
        vendor="postgresql",
        in_atomic_block=False,
        execute_wrapper=install_wrapper,
    )
    monkeypatch.setattr(action_deadline, "connection", fake_connection)
    monkeypatch.setattr(
        action_deadline,
        "transaction",
        SimpleNamespace(atomic=atomic, set_rollback=mock.MagicMock()),
    )
    deadline = _SequencedDeadline([9_000, 8_500, 7_000, 6_500, 6_000])
    executed = []

    with action_deadline.bounded_dashboard_postgres_reads(deadline):
        assert atomic_entries == []
        wrapper = installed["wrapper"]
        context = {"cursor": SimpleNamespace(cursor=raw_cursor)}

        def execute(sql, params, many, _context):
            executed.append((sql, params, many))
            return sql

        assert wrapper(execute, "SELECT first", (), False, context) == "SELECT first"
        assert wrapper(execute, "SELECT second", (), False, context) == "SELECT second"

    assert atomic_entries == ["entered"]
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
    assert [row[0] for row in executed] == ["SELECT first", "SELECT second"]


@pytest.mark.unit
def test_dashboard_postgres_failure_is_typed_and_sanitized(monkeypatch):
    fake_connection = SimpleNamespace(vendor="postgresql", in_atomic_block=True)
    monkeypatch.setattr(action_deadline, "connection", fake_connection)
    monkeypatch.setattr(
        action_deadline,
        "transaction",
        SimpleNamespace(set_rollback=mock.MagicMock()),
    )
    deadline = _SequencedDeadline([8_000])
    context = {"cursor": SimpleNamespace(cursor=mock.MagicMock())}

    with pytest.raises(action_deadline.DashboardActionUnavailable) as caught:
        action_deadline._execute_dashboard_postgres_query_with_deadline(
            deadline,
            mock.MagicMock(side_effect=DatabaseError("private SQL credentials")),
            "SELECT private",
            (),
            False,
            context,
        )

    assert "private" not in str(caught.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("view_name", "resource"),
    [
        ("query", "dashboard_query"),
        ("preview_query", "dashboard_widget_preview"),
    ],
)
def test_public_wall_starts_before_runtime_validation(monkeypatch, view_name, resource):
    from tfc.utils import api_contracts
    from tracer.views import dashboard as dashboard_view

    events = []
    deadline = _SequencedDeadline([9_000, 8_900], events)

    @contextmanager
    def install_wrapper(_wrapper):
        events.append("postgres_wrapper_installed")
        yield

    @contextmanager
    def atomic():
        events.append("atomic_entered")
        yield

    def reject_query(*_args, **_kwargs):
        events.append("runtime_validation")
        return SimpleNamespace(validated_data={}), {"invalid": ["value"]}, False

    monkeypatch.setattr(
        action_deadline,
        "start_dashboard_action_deadline",
        lambda: events.append("deadline_started") or deadline,
    )
    monkeypatch.setattr(
        action_deadline,
        "connection",
        SimpleNamespace(
            vendor="postgresql",
            in_atomic_block=False,
            execute_wrapper=install_wrapper,
        ),
    )
    monkeypatch.setattr(
        action_deadline,
        "transaction",
        SimpleNamespace(atomic=atomic, set_rollback=mock.MagicMock()),
    )
    monkeypatch.setattr(api_contracts, "_validate_serializer", reject_query)

    view_cls = (
        dashboard_view.DashboardViewSet
        if view_name == "query"
        else dashboard_view.DashboardWidgetViewSet
    )
    view = view_cls.__new__(view_cls)
    response = getattr(view_cls, view_name)(view, _request())

    assert response.status_code == 400
    assert events[:3] == [
        "deadline_started",
        "postgres_wrapper_installed",
        "runtime_validation",
    ]
    assert "atomic_entered" not in events
    assert getattr(view_cls, view_name).mapping["post"] == view_name
    assert resource in {"dashboard_query", "dashboard_widget_preview"}


@pytest.mark.unit
def test_expired_validation_returns_sanitized_503(monkeypatch):
    from tfc.utils import api_contracts
    from tracer.views import dashboard as dashboard_view

    deadline = _SequencedDeadline([ReadDeadlineExceeded("private validation timing")])

    @contextmanager
    def bounded_scope(_deadline):
        yield

    monkeypatch.setattr(
        action_deadline,
        "start_dashboard_action_deadline",
        lambda: deadline,
    )
    monkeypatch.setattr(
        action_deadline,
        "bounded_dashboard_postgres_reads",
        bounded_scope,
    )
    monkeypatch.setattr(
        api_contracts,
        "_validate_serializer",
        lambda *_args, **_kwargs: (
            SimpleNamespace(validated_data={}),
            {"invalid": ["value"]},
            False,
        ),
    )
    view = dashboard_view.DashboardViewSet.__new__(dashboard_view.DashboardViewSet)
    view._gm = _response_gm()

    response = dashboard_view.DashboardViewSet.query(view, _request())

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert "private" not in str(response.data)


def _accept_dashboard_contract(serializer_class, *_args, **_kwargs):
    if serializer_class.__name__ == "DashboardRefreshQuerySerializer":
        validated = {"refresh": False}
    elif serializer_class.__name__ == "DashboardPreviewQuerySerializer":
        validated = {"query_config": {"metrics": [], "filters": []}}
    else:
        validated = {"metrics": [], "filters": []}
    return SimpleNamespace(validated_data=validated), {}, True


@pytest.mark.unit
def test_dashboard_query_passes_outer_deadline_to_shared_executor(
    monkeypatch,
):
    from tfc.utils import api_contracts
    from tracer.views import dashboard as dashboard_view

    deadline = _SequencedDeadline([9_000, 8_900])

    @contextmanager
    def bounded_scope(received_deadline):
        assert received_deadline is deadline
        yield

    monkeypatch.setattr(
        action_deadline, "start_dashboard_action_deadline", lambda: deadline
    )
    monkeypatch.setattr(
        action_deadline, "bounded_dashboard_postgres_reads", bounded_scope
    )
    monkeypatch.setattr(
        api_contracts, "_validate_serializer", _accept_dashboard_contract
    )
    monkeypatch.setattr(api_contracts, "_validate_response", lambda *_args: None)
    execute = mock.MagicMock(
        return_value=SimpleNamespace(status_code=200, data={"result": {}})
    )
    monkeypatch.setattr(
        dashboard_view.DashboardWidgetViewSet,
        "_execute_ch_query_config",
        execute,
    )
    view = dashboard_view.DashboardViewSet.__new__(dashboard_view.DashboardViewSet)
    view._gm = _response_gm()

    response = dashboard_view.DashboardViewSet.query(view, _request())

    assert response.status_code == 200
    execute.assert_called_once_with(
        {"metrics": [], "filters": [], "allow_sampled": False},
        mock.ANY,
        refresh=False,
        _read_deadline=deadline,
    )


@pytest.mark.unit
def test_dashboard_preview_passes_outer_deadline_to_shared_executor(monkeypatch):
    from tfc.utils import api_contracts
    from tracer.views import dashboard as dashboard_view

    deadline = _SequencedDeadline([9_000, 8_900])

    @contextmanager
    def bounded_scope(received_deadline):
        assert received_deadline is deadline
        yield

    monkeypatch.setattr(
        action_deadline, "start_dashboard_action_deadline", lambda: deadline
    )
    monkeypatch.setattr(
        action_deadline, "bounded_dashboard_postgres_reads", bounded_scope
    )
    monkeypatch.setattr(
        api_contracts, "_validate_serializer", _accept_dashboard_contract
    )
    monkeypatch.setattr(api_contracts, "_validate_response", lambda *_args: None)
    monkeypatch.setattr(dashboard_view, "is_clickhouse_enabled", lambda: True)
    view = dashboard_view.DashboardWidgetViewSet.__new__(
        dashboard_view.DashboardWidgetViewSet
    )
    view._gm = _response_gm()
    execute = mock.MagicMock(
        return_value=view._gm.success_response({"query_complete": True, "metrics": []})
    )
    monkeypatch.setattr(view, "_execute_ch_query_config", execute)

    response = dashboard_view.DashboardWidgetViewSet.preview_query(
        view,
        _request(),
    )

    assert response.status_code == 200
    assert execute.call_args.kwargs["_read_deadline"] is deadline
    assert execute.call_args.kwargs["refresh"] is False


@pytest.mark.unit
def test_dashboard_action_metadata_survives_outer_deadline_wrapper():
    from tracer.views.dashboard import DashboardViewSet, DashboardWidgetViewSet

    assert DashboardViewSet.query.mapping["post"] == "query"
    assert DashboardWidgetViewSet.preview_query.mapping["post"] == "preview_query"
    assert DashboardViewSet.query.detail is False
    assert DashboardWidgetViewSet.preview_query.detail is False
    assert DashboardViewSet.query.url_path == "query"
    assert DashboardWidgetViewSet.preview_query.url_path == "preview"
    assert DashboardViewSet.query.__name__ == "query"
    assert DashboardWidgetViewSet.preview_query.__name__ == "preview_query"
    assert hasattr(DashboardViewSet.query, "_swagger_auto_schema")
    assert hasattr(DashboardWidgetViewSet.preview_query, "_swagger_auto_schema")
    assert DashboardViewSet.query.__wrapped__ is inspect.unwrap(DashboardViewSet.query)
    assert DashboardWidgetViewSet.preview_query.__wrapped__ is inspect.unwrap(
        DashboardWidgetViewSet.preview_query
    )
