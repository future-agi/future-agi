from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.db import DatabaseError
from rest_framework.response import Response

from simulate.models.agent_version import AgentVersion
from simulate.serializers.response.agent_definition import (
    AgentDefinitionResponseSerializer,
)
from simulate.serializers.response.scenarios import ScenarioResponseSerializer
from simulate.views import prompt_simulation, run_test
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded


class _RawCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))


class _Deadline:
    def __init__(self, remaining):
        self.remaining = iter(remaining)

    def remaining_ms(self, *, floor_ms):
        assert floor_ms == 1
        return next(self.remaining)


def test_run_test_query_timeout_shrinks_before_each_query():
    raw = _RawCursor()
    context = {"cursor": type("Cursor", (), {"cursor": raw})()}
    executed = []

    def execute(sql, params, many, passed_context):
        executed.append((sql, params, many, passed_context))
        return "result"

    deadline = _Deadline([8_901, 8_887])
    result = run_test._execute_run_test_list_query_with_deadline(
        deadline, execute, "SELECT page", (1,), False, context
    )

    assert result == "result"
    assert raw.calls == [
        (
            "SELECT set_config('statement_timeout', %s, true)",
            ("8901ms",),
        )
    ]
    assert executed == [("SELECT page", (1,), False, context)]


@pytest.mark.parametrize("failure", [ReadDeadlineExceeded("wall"), DatabaseError()])
def test_run_test_list_deadline_failure_is_sanitized_503(monkeypatch, failure):
    events = []
    deadline = object()

    class _DeadlineFactory:
        @staticmethod
        def start(total_ms):
            events.append(("start", total_ms))
            return deadline

    @contextmanager
    def bounded(received):
        assert received is deadline
        events.append(("transaction", None))
        yield

    monkeypatch.setattr(run_test, "ReadDeadline", _DeadlineFactory)
    monkeypatch.setattr(run_test, "_bounded_run_test_list_transaction", bounded)

    def view_method(_view, _request):
        events.append(("view", None))
        raise failure

    response = run_test._bounded_run_test_list_read(view_method)(object(), object())

    assert events == [
        ("start", run_test._RUN_TEST_LIST_WALL_MS),
        ("transaction", None),
        ("view", None),
    ]
    assert response.status_code == 503
    assert response.data["code"] == "simulation_list_unavailable"
    assert "wall" not in str(response.data)


def test_all_changed_run_test_list_and_detail_reads_use_the_action_deadline():
    wrapper_code = run_test._bounded_run_test_list_read(lambda *_args: None).__code__
    for view in (
        run_test.RunTestListView,
        run_test.RunTestAPIView,
        run_test.RunTestDetailView,
        prompt_simulation.PromptSimulationListCreateView,
        prompt_simulation.PromptSimulationDetailView,
    ):
        assert view.get.__code__ is wrapper_code


def test_run_test_response_limit_fails_closed_before_return(monkeypatch):
    deadline = object()

    class _DeadlineFactory:
        @staticmethod
        def start(_total_ms):
            return deadline

    @contextmanager
    def bounded(received):
        assert received is deadline
        yield

    monkeypatch.setattr(run_test, "ReadDeadline", _DeadlineFactory)
    monkeypatch.setattr(run_test, "_bounded_run_test_list_transaction", bounded)
    monkeypatch.setattr(
        run_test,
        "_ensure_run_test_response_bounded",
        lambda _value: (_ for _ in ()).throw(run_test.RunTestReadLimitExceeded()),
    )

    response = run_test._bounded_run_test_list_read(
        lambda _view, _request: Response({"result": "oversized"})
    )(object(), object())

    assert response.status_code == 503
    assert response.data["code"] == "simulation_list_unavailable"


def test_inner_swallowed_failure_is_reclassified_as_sanitized_503(monkeypatch):
    deadline = SimpleNamespace(remaining_ms=lambda *, floor_ms: 1_000)

    class _DeadlineFactory:
        @staticmethod
        def start(_total_ms):
            return deadline

    @contextmanager
    def bounded(_received):
        yield

    monkeypatch.setattr(run_test, "ReadDeadline", _DeadlineFactory)
    monkeypatch.setattr(run_test, "_bounded_run_test_list_transaction", bounded)

    response = run_test._bounded_run_test_list_read(
        lambda _view, _request: Response(
            {"detail": "private database failure"},
            status=500,
        )
    )(object(), object())

    assert response.status_code == 503
    assert response.data["code"] == "simulation_list_unavailable"
    assert "private" not in str(response.data)


def test_prefetched_empty_scenario_relations_never_issue_fallback_queries():
    serializer = ScenarioResponseSerializer()
    dataset = SimpleNamespace(column_order=[], _run_test_columns=[])
    scenario = SimpleNamespace(
        dataset=dataset,
        _dataset_row_count=None,
        _active_graphs=[],
    )

    assert serializer.get_dataset_rows(scenario) == 0
    assert serializer.get_dataset_column_config(scenario) == {}
    assert serializer.get_graph(scenario) == {}


def test_prefetched_agent_versions_avoid_active_and_latest_relation_queries():
    credentials = object()
    active = SimpleNamespace(
        status=AgentVersion.StatusChoices.ACTIVE,
        credentials=credentials,
    )
    agent = SimpleNamespace(_prefetched_versions=[active])

    assert AgentDefinitionResponseSerializer._get_latest_creds(agent) is credentials
