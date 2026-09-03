from contextlib import contextmanager, nullcontext
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from django.conf import settings as django_settings
from django.db import DatabaseError

from tracer.services.clickhouse import graph_action_deadline as action_deadline
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


class _SequencedDeadline:
    def __init__(self, values):
        self._values = iter(values)

    def remaining_ms(self, cap_ms=None, *, floor_ms=25):
        value = next(self._values)
        if isinstance(value, BaseException):
            raise value
        if value < floor_ms:
            raise ReadDeadlineExceeded("test deadline exceeded")
        return min(value, cap_ms) if cap_ms is not None else value


@pytest.mark.unit
def test_graph_action_wall_uses_the_interactive_analytics_default():
    assert (
        action_deadline.GRAPH_ACTION_WALL_DEADLINE_MS
        == django_settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )


@pytest.mark.unit
def test_graph_action_postgres_statements_receive_a_shrinking_timeout(monkeypatch):
    installed = {}
    raw_cursor = MagicMock()
    fake_connection = SimpleNamespace(
        vendor="postgresql",
        execute_wrapper=lambda wrapper: (
            installed.setdefault("wrapper", wrapper) or nullcontext()
        ),
    )

    @contextmanager
    def install_wrapper(wrapper):
        installed["wrapper"] = wrapper
        yield

    fake_connection.execute_wrapper = install_wrapper
    monkeypatch.setattr(action_deadline, "connection", fake_connection)
    monkeypatch.setattr(
        action_deadline,
        "transaction",
        SimpleNamespace(atomic=lambda: nullcontext()),
    )
    deadline = _SequencedDeadline([9_000, 8_500, 7_000, 6_500, 6_000])
    executed = []

    with action_deadline.graph_action_postgres_budget(deadline):
        wrapper = installed["wrapper"]
        context = {"cursor": SimpleNamespace(cursor=raw_cursor)}

        def execute(sql, params, many, _context):
            executed.append((sql, params, many))
            return sql

        assert wrapper(execute, "SELECT first", (), False, context) == "SELECT first"
        assert wrapper(execute, "SELECT second", (), False, context) == "SELECT second"

    assert raw_cursor.execute.call_args_list == [
        call(
            "SELECT set_config('statement_timeout', %s, true)",
            ("9000",),
        ),
        call(
            "SELECT set_config('statement_timeout', %s, true)",
            ("7000",),
        ),
    ]
    assert [item[0] for item in executed] == ["SELECT first", "SELECT second"]


@pytest.mark.unit
def test_graph_action_postgres_timeout_fails_closed(monkeypatch):
    installed = {}
    fake_connection = SimpleNamespace(vendor="postgresql")

    @contextmanager
    def install_wrapper(wrapper):
        installed["wrapper"] = wrapper
        yield

    fake_connection.execute_wrapper = install_wrapper
    monkeypatch.setattr(action_deadline, "connection", fake_connection)
    monkeypatch.setattr(
        action_deadline,
        "transaction",
        SimpleNamespace(atomic=lambda: nullcontext()),
    )
    deadline = _SequencedDeadline([8_000])
    context = {"cursor": SimpleNamespace(cursor=MagicMock())}

    with pytest.raises(action_deadline.GraphActionUnavailable):
        with action_deadline.graph_action_postgres_budget(deadline):
            installed["wrapper"](
                lambda *_args: (_ for _ in ()).throw(DatabaseError("private")),
                "SELECT slow",
                (),
                False,
                context,
            )


def _call_graph_action(
    monkeypatch,
    view_kind,
    *,
    deadline,
    metric_type="SYSTEM_METRIC",
    postgres_budget=None,
):
    events = []
    dispatched = []

    if view_kind == "trace":
        from tracer.views import trace as graph_view

        project_scope = MagicMock()

        def first():
            events.append("project_read")
            return SimpleNamespace(
                trace_type="observe",
                organization_id="22222222-2222-4222-8222-222222222222",
            )

        project_scope.filter.return_value.first.side_effect = first
        monkeypatch.setattr(
            graph_view,
            "_project_queryset_for_request",
            lambda _request: project_scope,
        )
        view_cls = graph_view.TraceView
    else:
        from tracer.views import observation_span as graph_view

        def get_project(*_args, **_kwargs):
            events.append("project_read")
            return SimpleNamespace(
                trace_type="observe",
                organization_id="22222222-2222-4222-8222-222222222222",
            )

        monkeypatch.setattr(graph_view.Project.objects, "get", get_project)
        monkeypatch.setattr(
            graph_view,
            "_project_workspace_scope_q",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            graph_view,
            "_get_request_organization",
            lambda _request: object(),
        )
        view_cls = graph_view.ObservationSpanView

    monkeypatch.setattr(
        graph_view,
        "start_graph_action_deadline",
        lambda: events.append("deadline_started") or deadline,
    )

    if postgres_budget is None:

        @contextmanager
        def postgres_budget(actual_deadline):
            assert actual_deadline is deadline
            events.append("pg_enter")
            yield
            events.append("pg_exit")

    else:
        postgres_budget = postgres_budget(events, deadline)
    monkeypatch.setattr(graph_view, "graph_action_postgres_budget", postgres_budget)
    monkeypatch.setattr(graph_view, "graph_execution_filters", lambda filters: filters)
    monkeypatch.setattr(graph_view, "graph_query_evidence", lambda **_kwargs: {})
    monkeypatch.setattr(
        graph_view,
        "validate_property_graph_namespace",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(graph_view, "V2AnalyticsQueryService", MagicMock)

    if metric_type == "EVAL":
        eval_scope = MagicMock()

        def exists():
            events.append("config_read")
            return True

        eval_scope.exists.side_effect = exists
        monkeypatch.setattr(
            graph_view.CustomEvalConfig.objects,
            "filter",
            lambda **_kwargs: eval_scope,
        )

    def fetch(**kwargs):
        events.append("graph_dispatch")
        dispatched.append(kwargs)
        return {
            "metric_name": "latency",
            "data": [],
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        }

    fetch_name = {
        "SYSTEM_METRIC": "fetch_system_metric_graph_ch",
        "EVAL": "fetch_eval_graph_ch",
        "ANNOTATION": "fetch_annotation_graph_ch",
    }[metric_type]
    monkeypatch.setattr(graph_view, fetch_name, fetch)
    request = SimpleNamespace(
        validated_query_data={"allow_sampled": False},
        validated_data={
            "project_id": PROJECT_ID,
            "filters": [],
            "property": "average",
            "interval": "day",
            "req_data_config": {"id": "latency", "type": metric_type},
        },
        workspace=None,
    )
    view = view_cls()
    view.request = request
    response = unwrap(view_cls.get_graph_methods)(view, request)
    return response, events, dispatched


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
@pytest.mark.parametrize("metric_type", ["SYSTEM_METRIC", "ANNOTATION"])
def test_graph_action_starts_before_pg_and_passes_only_remaining_wall(
    monkeypatch, view_kind, metric_type
):
    deadline = _SequencedDeadline([4_321, 4_000])

    response, events, dispatched = _call_graph_action(
        monkeypatch,
        view_kind,
        deadline=deadline,
        metric_type=metric_type,
    )

    assert response.status_code == 200
    assert events == [
        "deadline_started",
        "pg_enter",
        "project_read",
        "pg_exit",
        "graph_dispatch",
    ]
    assert dispatched[0]["timeout_ms"] == 4_321


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_eval_config_read_reuses_the_same_pg_deadline(monkeypatch, view_kind):
    deadline = _SequencedDeadline([3_500, 3_000])

    response, events, dispatched = _call_graph_action(
        monkeypatch,
        view_kind,
        deadline=deadline,
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
        "graph_dispatch",
    ]
    assert dispatched[0]["timeout_ms"] == 3_500


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_expired_pg_scope_prevents_graph_dispatch(monkeypatch, view_kind):
    @contextmanager
    def unavailable_budget(_deadline):
        raise action_deadline.GraphActionUnavailable("scope timeout")
        yield

    def budget_factory(_events, _deadline):
        return unavailable_budget

    response, events, dispatched = _call_graph_action(
        monkeypatch,
        view_kind,
        deadline=_SequencedDeadline([]),
        postgres_budget=budget_factory,
    )

    assert response.status_code == 503
    assert events == ["deadline_started"]
    assert dispatched == []


@pytest.mark.unit
@pytest.mark.parametrize("view_kind", ["trace", "span"])
def test_graph_result_is_not_published_after_action_wall_expires(
    monkeypatch, view_kind
):
    deadline = _SequencedDeadline(
        [4_000, ReadDeadlineExceeded("graph consumed the remaining wall")]
    )

    response, events, dispatched = _call_graph_action(
        monkeypatch,
        view_kind,
        deadline=deadline,
    )

    assert events[-1] == "graph_dispatch"
    assert dispatched[0]["timeout_ms"] == 4_000
    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
