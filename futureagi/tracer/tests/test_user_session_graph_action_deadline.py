"""Request-owned wall deadlines for session and project user graph actions."""

from contextlib import contextmanager
from datetime import UTC, datetime
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracer.services.clickhouse.graph_action_deadline import GraphActionUnavailable
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
ORG_ID = "22222222-2222-4222-8222-222222222222"
WORKSPACE_ID = "33333333-3333-4333-8333-333333333333"
END_USER_ID = "44444444-4444-4444-8444-444444444444"

GRAPH_ACTION_TARGETS = (
    ("session", "get_session_graph_data"),
    ("project", "get_user_metrics"),
    ("project", "get_users_aggregate_graph_data"),
    ("project", "get_user_graph_data"),
)


class _SequencedDeadline:
    def __init__(self, values, events):
        self._values = iter(values)
        self._events = events

    def remaining_ms(self, cap_ms=None, *, floor_ms=25):
        value = next(self._values)
        self._events.append(("remaining", value))
        if isinstance(value, BaseException):
            raise value
        if value < floor_ms:
            raise ReadDeadlineExceeded("test action deadline exceeded")
        return min(value, cap_ms) if cap_ms is not None else value


def _install_action_wall(
    monkeypatch, module, deadline, events, *, pg_unavailable=False
):
    monkeypatch.setattr(
        module,
        "start_graph_action_deadline",
        lambda: events.append("deadline_started") or deadline,
    )

    @contextmanager
    def postgres_budget(actual_deadline):
        assert actual_deadline is deadline
        events.append("pg_enter")
        if pg_unavailable:
            raise GraphActionUnavailable("private PostgreSQL timeout")
        yield
        events.append("pg_exit")

    monkeypatch.setattr(module, "graph_action_postgres_budget", postgres_budget)


def _complete_graph(metric_name):
    return {
        "metric_name": metric_name,
        "data": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _public_graph_action(module_kind, action_name):
    if module_kind == "session":
        from tracer.views import trace_session as graph_view

        return (
            graph_view,
            graph_view.TraceSessionView,
            getattr(
                graph_view.TraceSessionView,
                action_name,
            ),
        )

    from tracer.views import project as graph_view

    return (
        graph_view,
        graph_view.ProjectView,
        getattr(
            graph_view.ProjectView,
            action_name,
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(("module_kind", "action_name"), GRAPH_ACTION_TARGETS)
def test_graph_action_public_wall_starts_before_invalid_runtime_validation_without_db(
    monkeypatch,
    module_kind,
    action_name,
):
    from tfc.utils import api_contracts

    graph_view, view_cls, public_action = _public_graph_action(
        module_kind,
        action_name,
    )
    events = []
    deadline = MagicMock()

    def remaining_ms(*_args, **_kwargs):
        events.append("deadline_checked")
        return 9_000

    deadline.remaining_ms.side_effect = remaining_ms
    monkeypatch.setattr(
        graph_view,
        "start_graph_action_deadline",
        lambda: events.append("deadline_started") or deadline,
    )

    def reject_request(*_args, **_kwargs):
        events.append("runtime_validation")
        return SimpleNamespace(validated_data={}), {"request": ["invalid"]}, False

    monkeypatch.setattr(api_contracts, "_validate_serializer", reject_request)
    view = view_cls.__new__(view_cls)
    if module_kind == "session":
        monkeypatch.setattr(
            graph_view,
            "_project_queryset_for_request",
            lambda _request: (_ for _ in ()).throw(
                AssertionError("invalid validation reached PostgreSQL scope")
            ),
        )
    else:
        monkeypatch.setattr(
            view,
            "_get_project_in_scope",
            lambda _project_id: (_ for _ in ()).throw(
                AssertionError("invalid validation reached PostgreSQL scope")
            ),
        )
    request = SimpleNamespace(method="POST", data={}, query_params={})

    response = public_action(view, request)

    assert response.status_code == 400
    assert events == [
        "deadline_started",
        "runtime_validation",
        "deadline_checked",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(("module_kind", "action_name"), GRAPH_ACTION_TARGETS)
def test_graph_action_invalid_validation_cannot_publish_after_wall_expires(
    monkeypatch,
    module_kind,
    action_name,
):
    from tfc.utils import api_contracts

    graph_view, view_cls, public_action = _public_graph_action(
        module_kind,
        action_name,
    )
    events = []
    deadline = MagicMock()

    def expired(*_args, **_kwargs):
        events.append("deadline_checked")
        raise ReadDeadlineExceeded("private validation timeout")

    deadline.remaining_ms.side_effect = expired
    monkeypatch.setattr(
        graph_view,
        "start_graph_action_deadline",
        lambda: events.append("deadline_started") or deadline,
    )

    def reject_request(*_args, **_kwargs):
        events.append("runtime_validation")
        return SimpleNamespace(validated_data={}), {"request": ["invalid"]}, False

    monkeypatch.setattr(api_contracts, "_validate_serializer", reject_request)
    view = view_cls.__new__(view_cls)
    request = SimpleNamespace(method="POST", data={}, query_params={})

    response = public_action(view, request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert events == [
        "deadline_started",
        "runtime_validation",
        "deadline_checked",
    ]
    assert "private" not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(("module_kind", "action_name"), GRAPH_ACTION_TARGETS)
def test_graph_action_public_wrapper_injects_one_deadline_into_postgres_scope(
    monkeypatch,
    module_kind,
    action_name,
):
    from tfc.utils import api_contracts

    graph_view, view_cls, public_action = _public_graph_action(
        module_kind,
        action_name,
    )
    events = []
    deadline = MagicMock()
    start_deadline = MagicMock(
        side_effect=lambda: events.append("deadline_started") or deadline
    )
    monkeypatch.setattr(graph_view, "start_graph_action_deadline", start_deadline)
    validated_by_serializer = {
        "ObserveGraphDataQuerySerializer": {
            "allow_sampled": False,
            "refresh": False,
        },
        "TraceSessionGraphDataRequestSerializer": {
            "project_id": PROJECT_ID,
            "filters": [],
            "interval": "day",
            "property": "average",
            "req_data_config": {
                "id": "session_count",
                "type": "SYSTEM_METRIC",
            },
        },
        "ProjectUserMetricsRequestSerializer": {
            "project_id": PROJECT_ID,
            "end_user_id": END_USER_ID,
            "filters": [],
            "interval": "day",
        },
        "ProjectUsersAggregateGraphDataRequestSerializer": {
            "project_id": PROJECT_ID,
            "filters": [],
            "interval": "day",
            "property": "average",
            "req_data_config": {
                "id": "active_users",
                "type": "SYSTEM_METRIC",
            },
        },
        "ProjectUserGraphDataQuerySerializer": {
            "project_id": PROJECT_ID,
            "end_user_id": END_USER_ID,
        },
        "ProjectUserGraphDataRequestSerializer": {
            "filters": [],
            "interval": "day",
        },
    }

    def accept_request(serializer_class, *_args, **_kwargs):
        events.append(f"validated:{serializer_class.__name__}")
        return (
            SimpleNamespace(
                validated_data=validated_by_serializer[serializer_class.__name__]
            ),
            {},
            True,
        )

    monkeypatch.setattr(api_contracts, "_validate_serializer", accept_request)

    @contextmanager
    def unavailable_postgres_scope(received_deadline):
        assert received_deadline is deadline
        events.append("postgres_scope")
        raise GraphActionUnavailable("private PostgreSQL timeout")
        yield

    monkeypatch.setattr(
        graph_view,
        "graph_action_postgres_budget",
        unavailable_postgres_scope,
    )
    request = SimpleNamespace(
        method="POST",
        data={},
        query_params={},
        user=SimpleNamespace(organization=SimpleNamespace(id=ORG_ID)),
        workspace=SimpleNamespace(id=WORKSPACE_ID, is_default=False),
    )
    view = view_cls.__new__(view_cls)
    view.request = request

    response = public_action(view, request)

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    start_deadline.assert_called_once_with()
    assert events[0] == "deadline_started"
    assert events[-1] == "postgres_scope"
    assert all(event.startswith("validated:") for event in events[1:-1])
    assert "private" not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(("module_kind", "action_name"), GRAPH_ACTION_TARGETS)
def test_graph_action_outer_wrapper_preserves_drf_and_swagger_metadata(
    module_kind,
    action_name,
):
    _graph_view, _view_cls, public_action = _public_graph_action(
        module_kind,
        action_name,
    )

    assert dict(public_action.mapping) == {"post": action_name}
    assert public_action.detail is False
    assert public_action.url_path == action_name
    assert "post" in public_action._swagger_auto_schema
    assert public_action.__code__.co_name == "wrapped"
    assert public_action.__wrapped__.__name__ == action_name
    assert unwrap(public_action) is public_action.__wrapped__


def _call_session_graph(
    monkeypatch,
    *,
    deadline_values,
    pg_unavailable=False,
    metric_type="SYSTEM_METRIC",
):
    from tracer.views import trace_session as session_view

    events = []
    dispatched = []
    deadline = _SequencedDeadline(deadline_values, events)
    _install_action_wall(
        monkeypatch,
        session_view,
        deadline,
        events,
        pg_unavailable=pg_unavailable,
    )

    project_scope = MagicMock()

    def get_project(**_kwargs):
        events.append("project_read")
        return SimpleNamespace(trace_type="observe", organization_id=ORG_ID)

    project_scope.get.side_effect = get_project
    monkeypatch.setattr(
        session_view,
        "_project_queryset_for_request",
        lambda _request: project_scope,
    )
    monkeypatch.setattr(session_view, "V2AnalyticsQueryService", MagicMock)
    monkeypatch.setattr(
        session_view,
        "validate_property_graph_namespace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(session_view, "graph_execution_filters", lambda value: value)
    monkeypatch.setattr(session_view, "graph_query_evidence", lambda **_kwargs: {})
    if metric_type == "EVAL":
        eval_scope = MagicMock()
        eval_scope.exists.side_effect = lambda: events.append("config_read") or True
        monkeypatch.setattr(
            session_view.CustomEvalConfig.objects,
            "filter",
            lambda **_kwargs: eval_scope,
        )

    def fetch(**kwargs):
        events.append("graph_dispatch")
        dispatched.append(kwargs)
        return _complete_graph("session_count")

    monkeypatch.setattr(session_view, "fetch_session_graph_ch", fetch)
    request = SimpleNamespace(
        validated_query_data={"allow_sampled": False},
        validated_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "interval": "day",
            "req_data_config": {
                "id": "session_count" if metric_type == "SYSTEM_METRIC" else "eval-id",
                "type": metric_type,
            },
        },
        workspace=SimpleNamespace(id=WORKSPACE_ID),
    )
    view = session_view.TraceSessionView()
    view.request = request
    response = unwrap(session_view.TraceSessionView.get_session_graph_data)(
        view,
        request,
    )
    return response, events, dispatched


@pytest.mark.unit
def test_session_graph_starts_wall_before_scope_and_passes_only_remaining_time(
    monkeypatch,
):
    response, events, dispatched = _call_session_graph(
        monkeypatch,
        deadline_values=[4_321, 4_000],
    )

    assert response.status_code == 200
    assert events == [
        "deadline_started",
        "pg_enter",
        "project_read",
        "pg_exit",
        ("remaining", 4_321),
        "graph_dispatch",
        ("remaining", 4_000),
    ]
    assert dispatched[0]["wall_deadline_ms"] == 4_321


@pytest.mark.unit
def test_session_eval_config_read_reuses_the_action_postgres_wall(monkeypatch):
    response, events, dispatched = _call_session_graph(
        monkeypatch,
        deadline_values=[4_321, 4_000],
        metric_type="EVAL",
    )

    assert response.status_code == 200
    assert events == [
        "deadline_started",
        "pg_enter",
        "project_read",
        "pg_exit",
        "pg_enter",
        "config_read",
        "pg_exit",
        ("remaining", 4_321),
        "graph_dispatch",
        ("remaining", 4_000),
    ]
    assert dispatched[0]["wall_deadline_ms"] == 4_321


@pytest.mark.unit
def test_session_graph_pg_timeout_is_sanitized_and_prevents_dispatch(monkeypatch):
    response, events, dispatched = _call_session_graph(
        monkeypatch,
        deadline_values=[],
        pg_unavailable=True,
    )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert events == ["deadline_started", "pg_enter"]
    assert dispatched == []
    assert "private" not in str(response.data)


@pytest.mark.unit
def test_session_graph_does_not_publish_after_shared_wall_expires(monkeypatch):
    response, events, dispatched = _call_session_graph(
        monkeypatch,
        deadline_values=[
            4_321,
            ReadDeadlineExceeded("private post-dispatch timeout"),
        ],
    )

    assert events[-2] == "graph_dispatch"
    assert dispatched[0]["wall_deadline_ms"] == 4_321
    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert "private" not in str(response.data)


class _UserMetricsBuilder:
    def __init__(self, **_kwargs):
        pass

    def build(self):
        return "SELECT bounded user metrics", {}

    def format_rows(self, _rows):
        return {"table": []}


class _UserDetailBuilder:
    start_date = datetime(2026, 8, 1, tzinfo=UTC)
    end_date = datetime(2026, 8, 2, tzinfo=UTC)

    def __init__(self, **_kwargs):
        pass

    def build(self):
        return "SELECT bounded user graph", {}

    def format_time_series(self, **_kwargs):
        return []


def _call_project_user_action(
    monkeypatch,
    action_name,
    *,
    deadline_values,
    pg_unavailable=False,
    metric_type="SYSTEM_METRIC",
):
    from tracer.views import project as project_view

    events = []
    dispatched = []
    deadline = _SequencedDeadline(deadline_values, events)
    _install_action_wall(
        monkeypatch,
        project_view,
        deadline,
        events,
        pg_unavailable=pg_unavailable,
    )
    monkeypatch.setattr(
        project_view,
        "get_request_organization",
        lambda _request: SimpleNamespace(id=ORG_ID),
    )
    monkeypatch.setattr(
        project_view,
        "validate_property_graph_namespace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(project_view, "UserListQueryBuilderV2", _UserMetricsBuilder)
    monkeypatch.setattr(
        project_view,
        "UserDetailTimeSeriesQueryBuilderV2",
        _UserDetailBuilder,
    )
    if metric_type == "EVAL":
        eval_scope = MagicMock()
        eval_scope.exists.side_effect = lambda: events.append("config_read") or True
        monkeypatch.setattr(
            project_view.CustomEvalConfig.objects,
            "filter",
            lambda **_kwargs: eval_scope,
        )

    def execute_ch_query(_query, _params, **kwargs):
        events.append("graph_dispatch")
        dispatched.append(kwargs)
        return SimpleNamespace(data=[])

    analytics = SimpleNamespace(
        supports_per_query_read_settings=True,
        execute_ch_query=execute_ch_query,
    )
    monkeypatch.setattr(project_view, "V2AnalyticsQueryService", lambda: analytics)

    def fetch_user_graph(**kwargs):
        events.append("graph_dispatch")
        dispatched.append(kwargs)
        return _complete_graph("active_users")

    monkeypatch.setattr(
        project_view,
        "fetch_user_system_metric_graph_ch",
        fetch_user_graph,
    )
    monkeypatch.setattr(project_view, "fetch_eval_graph_ch", fetch_user_graph)

    view = project_view.ProjectView()

    def get_project(_project_id):
        events.append("project_read")
        return SimpleNamespace(organization_id=ORG_ID)

    monkeypatch.setattr(view, "_get_project_in_scope", get_project)
    request = SimpleNamespace(
        validated_query_data={
            "allow_sampled": False,
            "project_id": PROJECT_ID,
            "end_user_id": "customer@example.com",
        },
        validated_data={
            "project_id": PROJECT_ID,
            "end_user_id": "customer@example.com",
            "filters": [],
            "interval": "day",
            "req_data_config": {
                "id": "active_users" if metric_type == "SYSTEM_METRIC" else "eval-id",
                "type": metric_type,
            },
        },
        workspace=SimpleNamespace(id=WORKSPACE_ID, is_default=False),
        user=SimpleNamespace(organization=SimpleNamespace(id=ORG_ID)),
    )
    view.request = request
    response = unwrap(getattr(project_view.ProjectView, action_name))(view, request)
    return response, events, dispatched


@pytest.mark.unit
@pytest.mark.parametrize(
    "action_name",
    [
        "get_user_metrics",
        "get_users_aggregate_graph_data",
        "get_user_graph_data",
    ],
)
def test_project_user_actions_start_before_scope_and_pass_only_remaining_time(
    monkeypatch,
    action_name,
):
    response, events, dispatched = _call_project_user_action(
        monkeypatch,
        action_name,
        deadline_values=[4_321, 4_000],
    )

    assert response.status_code == 200
    assert events == [
        "deadline_started",
        "pg_enter",
        "project_read",
        "pg_exit",
        ("remaining", 4_321),
        "graph_dispatch",
        ("remaining", 4_000),
    ]
    assert dispatched[0]["timeout_ms"] == 4_321


@pytest.mark.unit
def test_project_user_eval_config_read_reuses_the_action_postgres_wall(monkeypatch):
    response, events, dispatched = _call_project_user_action(
        monkeypatch,
        "get_users_aggregate_graph_data",
        deadline_values=[4_321, 4_000],
        metric_type="EVAL",
    )

    assert response.status_code == 200
    assert events == [
        "deadline_started",
        "pg_enter",
        "project_read",
        "pg_exit",
        "pg_enter",
        "config_read",
        "pg_exit",
        ("remaining", 4_321),
        "graph_dispatch",
        ("remaining", 4_000),
    ]
    assert dispatched[0]["timeout_ms"] == 4_321


@pytest.mark.unit
@pytest.mark.parametrize(
    "action_name",
    [
        "get_user_metrics",
        "get_users_aggregate_graph_data",
        "get_user_graph_data",
    ],
)
def test_project_user_pg_timeout_is_sanitized_and_prevents_dispatch(
    monkeypatch,
    action_name,
):
    response, events, dispatched = _call_project_user_action(
        monkeypatch,
        action_name,
        deadline_values=[],
        pg_unavailable=True,
    )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert events == ["deadline_started", "pg_enter"]
    assert dispatched == []
    assert "private" not in str(response.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "action_name",
    [
        "get_user_metrics",
        "get_users_aggregate_graph_data",
        "get_user_graph_data",
    ],
)
def test_project_user_actions_do_not_publish_after_shared_wall_expires(
    monkeypatch,
    action_name,
):
    response, events, dispatched = _call_project_user_action(
        monkeypatch,
        action_name,
        deadline_values=[
            4_321,
            ReadDeadlineExceeded("private post-dispatch timeout"),
        ],
    )

    assert events[-2] == "graph_dispatch"
    assert dispatched[0]["timeout_ms"] == 4_321
    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert "private" not in str(response.data)
