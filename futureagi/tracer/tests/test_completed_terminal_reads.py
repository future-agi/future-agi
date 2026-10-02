"""Offline terminal-result/admission boundaries, not database qualification."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
from django.db import DatabaseError

from tracer.services import dashboard_metrics_catalog as catalog
from tracer.services.clickhouse import (
    dashboard_action_deadline,
    graph_action_deadline,
    list_request_deadline,
    read_budget,
)
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.tests.test_postgres_application_read_policy import FakePostgres
from tracer.views import eval_task, trace_session

pytestmark = pytest.mark.unit


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(read_budget, "time", SimpleNamespace(monotonic=lambda: now[0]))
    deadline = ReadDeadline.start(5_000)
    return deadline, lambda: now.__setitem__(0, 6.0)


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("rows", [[], [{"id": "owned"}]])
@pytest.mark.parametrize("next_read", [False, True])
def test_pg_completed_terminal_or_next_read(clock, outer, rows, next_read):
    deadline, expire = clock
    pg = FakePostgres(outer=outer)

    def finish(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql != "SELECT complete":
            return result
        expire()
        return rows

    with pytest.raises(ReadDeadlineExceeded) if next_read else nullcontext():
        with pg.scope(check_request=deadline.remaining_ms):
            with pg.execute_wrapper(finish):
                assert pg.execute("SELECT complete") is rows
                if next_read:
                    pg.execute("SELECT must_not_start")
    assert pg.query_timeouts == ["0"]
    assert not any(sql == "SELECT must_not_start" for sql, _ in pg.events)
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("failure", ["statement", "restore"])
def test_pg_late_failure_still_restores_and_raises(clock, outer, failure):
    deadline, expire = clock
    pg = FakePostgres(outer=outer, failure=failure)

    def finish(execute, sql, params, many, context):
        try:
            return execute(sql, params, many, context)
        finally:
            if sql == "SELECT complete":
                expire()

    with pytest.raises(DatabaseError):
        with pg.scope(check_request=deadline.remaining_ms):
            with pg.execute_wrapper(finish):
                pg.execute("SELECT complete")
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers


WRAPPERS = {
    "list": (list_request_deadline, "bounded_list_postgres_reads"),
    "graph": (graph_action_deadline, None),
    "dashboard": (dashboard_action_deadline, "bounded_dashboard_postgres_reads"),
    "tasks": (eval_task, "_bounded_eval_task_read_transaction"),
    "sessions": (trace_session, "_bounded_session_list_postgres_reads"),
}


@pytest.mark.parametrize("family", WRAPPERS)
@pytest.mark.parametrize("rows", [[], [{"id": "owned"}]])
@pytest.mark.parametrize("outcome", ["terminal", "next_read", "error"])
def test_public_wrapper_completed_terminal_or_next_read(
    monkeypatch, clock, family, rows, outcome
):
    deadline, expire = clock
    module, scope_name = WRAPPERS[family]
    monkeypatch.setattr(module.ReadDeadline, "start", lambda _wall: deadline)

    @contextmanager
    def scope(_deadline):
        deadline.remaining_ms()
        yield  # Isolate the outer finish check; real PG scope is tested above.

    if scope_name:
        monkeypatch.setattr(module, scope_name, scope)
    decorators = {
        "list": lambda fn: module.bounded_list_request(
            wall_ms=5_000, resource="test", unavailable_message="Unavailable"
        )(fn),
        "graph": lambda fn: module.bounded_graph_action_request(resource="test")(fn),
        "dashboard": lambda fn: module.bounded_dashboard_action_request(
            resource="test"
        )(fn),
        "tasks": lambda fn: module._bounded_eval_task_read(fn),
        "sessions": lambda fn: module._bounded_session_list_request(fn),
    }
    response = SimpleNamespace(status_code=200, data={"rows": rows, "complete": True})
    error = RuntimeError("read failed")
    completed = []

    def action(_view, _request, **_kwargs):
        deadline.remaining_ms()
        expire()
        if outcome == "error":
            raise error
        completed.append(rows)
        if outcome == "next_read":
            check = getattr(module, f"{family}_action_remaining_ms", None)
            if check:
                check(deadline)
            else:
                deadline.remaining_ms()
            pytest.fail("next required read must not start")
        return response

    gm = SimpleNamespace(
        custom_error_response=lambda status, *a, **kw: SimpleNamespace(
            status_code=status
        )
    )
    with pytest.raises(RuntimeError) if outcome == "error" else nullcontext() as caught:
        result = decorators[family](action)(SimpleNamespace(_gm=gm), SimpleNamespace())
        assert (
            result is response if outcome == "terminal" else result.status_code == 503
        )
    assert completed == ([] if outcome == "error" else [rows])
    if outcome == "error":
        assert caught.value is error


@pytest.mark.parametrize("rows", [[], [{"name": "latency"}]])
@pytest.mark.parametrize("outcome", ["terminal", "next_read", "error"])
def test_catalog_terminal_page_or_next_family(clock, rows, outcome):
    deadline, expire = clock
    completed = []
    error = RuntimeError("family read failed")

    def count():
        if rows:
            return len(rows)
        return finish([]) or 0

    def finish(result):
        expire()
        if outcome == "error":
            raise error
        completed.append(result)
        return result

    families = [
        catalog._CatalogPageFamily("owned", count, lambda offset, limit: finish(rows))
    ]
    if outcome == "next_read":
        if rows:
            families.append(
                catalog._CatalogPageFamily(
                    "next", lambda: 1, lambda *_: pytest.fail("next read")
                )
            )
        else:
            families.append(
                catalog._CatalogPageFamily(
                    "next", lambda: pytest.fail("next count"), lambda *_: []
                )
            )
    expected_error = {
        "next_read": ReadDeadlineExceeded,
        "error": catalog.MetricsCatalogUnavailable,
    }
    with (
        pytest.raises(expected_error[outcome])
        if outcome != "terminal"
        else nullcontext() as caught
    ):
        result = catalog._paginate_catalog_families(
            families, page=1, page_size=2, deadline=deadline
        )
        assert result == (rows, len(rows), False)
    assert completed == ([] if outcome == "error" else [rows])
    if outcome == "error":
        assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "rows", [[], [{"name": "latency", "category": "system_metric"}]]
)
def test_catalog_page_final_annotation_is_terminal(monkeypatch, clock, rows):
    deadline, expire = clock
    monkeypatch.setattr(
        catalog, "_resolve_metrics_catalog_project_scope", lambda *a, **kw: ([], True)
    )
    monkeypatch.setattr(catalog, "_catalog_family_requested", lambda **kw: False)
    monkeypatch.setattr(
        catalog,
        "_run_metrics_catalog_pg_snapshot",
        lambda _deadline, read: (rows, len(rows), False),
    )

    def finish(result):
        expire()
        return result

    monkeypatch.setattr(catalog, "_annotate_property_registry_identity", finish)
    assert catalog.build_metrics_catalog_page(
        SimpleNamespace(), page=1, page_size=2, deadline=deadline
    ) == (rows, len(rows), False)


@pytest.mark.parametrize("category", ["system_metric", "unknown"])
def test_catalog_definition_final_annotation_is_terminal(monkeypatch, clock, category):
    deadline, expire = clock
    annotate = catalog._annotate_property_registry_identity

    def finish(metrics):
        result = annotate(metrics)
        expire()
        return result

    monkeypatch.setattr(catalog, "_annotate_property_registry_identity", finish)
    result = catalog.build_metrics_catalog(
        SimpleNamespace(),
        category=category,
        include_custom_attributes=False,
        deadline=deadline,
        _resolved_project_scope=([], True),
    )
    assert bool(result) == (category == "system_metric")


@pytest.mark.parametrize("column", ["user_id", "session_id"])
@pytest.mark.parametrize(
    "rows", [[], [{"val": "00000000-0000-4000-8000-000000000002"}]]
)
@pytest.mark.parametrize("late_phase", ["query", "labels", "error"])
def test_session_picker_terminal_or_required_labels(
    monkeypatch, clock, column, rows, late_phase
):
    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    deadline, expire = clock
    completed = []

    def execute(*args, **kwargs):
        if late_phase == "error":
            expire()
            raise RuntimeError("real query failure")
        completed.append("query")
        if late_phase == "query":
            expire()
        return SimpleNamespace(data=rows)

    def labels(ids, **kwargs):
        if ids:
            completed.append("labels")
            if late_phase == "labels":
                expire()
        return {}

    monkeypatch.setattr(trace_session.ReadDeadline, "start", lambda _wall: deadline)
    monkeypatch.setattr(
        trace_session, "_read_session_filter_project_in_scope", lambda **kw: True
    )
    monkeypatch.setattr(
        trace_session,
        "V2AnalyticsQueryService",
        lambda: SimpleNamespace(execute_ch_query=execute),
    )
    monkeypatch.setattr(trace_session_dict_reader, "resolve_session_fields", labels)
    request = SimpleNamespace(
        method="GET",
        data={},
        query_params={
            "project_id": "00000000-0000-4000-8000-000000000001",
            "column": column,
            "page_size": 2,
        },
    )
    response = trace_session.TraceSessionView().get_session_filter_values(request)
    if late_phase == "error":
        assert response.status_code == 500 and completed == []
    elif rows and column == "session_id" and late_phase == "query":
        assert response.status_code == 503 and completed == ["query"]
    else:
        assert response.status_code == 200
        assert len(response.data["result"]["values"]) == len(rows)
        assert response.data["result"]["next"] is False
        assert completed == (
            ["query", "labels"] if rows and column == "session_id" else ["query"]
        )
