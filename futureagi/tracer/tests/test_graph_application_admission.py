"""Public graph admission survives elapsed time; diagnostic budgets still expire."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse import graph_action_deadline as graph
from tracer.services.clickhouse import read_budget


@pytest.mark.parametrize("elapsed_seconds", [30, 300, 3600])
def test_application_graph_admission_does_not_expire(monkeypatch, elapsed_seconds):
    clock = [0.0]
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: clock[0])
    deadline = graph.start_graph_action_deadline()
    clock[0] = elapsed_seconds
    assert graph.graph_action_remaining_ms(deadline) > 0
    assert graph.graph_action_remaining_ms(deadline, 1234) == 1234
    with pytest.raises(ValueError):
        graph.graph_action_remaining_ms(deadline, 0)
    diagnostic = read_budget.ReadDeadline(started=0, total_ms=500)
    with pytest.raises(graph.GraphActionUnavailable):
        graph.graph_action_remaining_ms(diagnostic)


def test_public_wrapper_keeps_slow_valid_request_publishable(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: clock[0])
    pending = {"query_status": "pending", "query_complete": False}
    view = SimpleNamespace(_gm=SimpleNamespace(custom_error_response=Mock()))

    @graph.bounded_graph_action_request(resource="test")
    def action(view, request, *, _graph_action_deadline):
        clock[0] = 3600
        assert graph.graph_action_remaining_ms(_graph_action_deadline) > 0
        return pending

    assert action(view, object()) is pending
    view._gm.custom_error_response.assert_not_called()


@pytest.mark.parametrize("outer", [False, True])
def test_slow_application_ownership_scope_restores_postgres_policy(monkeypatch, outer):
    from tracer.tests.test_postgres_application_read_policy import FakePostgres

    pg = FakePostgres(outer=outer)
    clock = [0.0]
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(graph, "connection", pg)
    monkeypatch.setattr(graph.transaction, "atomic", pg.atomic)
    deadline = graph.start_graph_action_deadline()
    with graph.graph_action_postgres_budget(deadline):
        pg.execute("SELECT first")
        clock[0] = 3600
        pg.execute("SELECT second")
    assert pg.query_timeouts == ["0", "0"]
    assert pg.timeout == "750ms"
    assert pg.in_atomic_block is outer
    assert not pg.wrappers
