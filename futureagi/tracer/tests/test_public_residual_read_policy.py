"""Reachable selector/view boundaries with real policy code, no live services."""

from concurrent.futures import Future
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from clickhouse_driver.errors import NetworkError, ServerException
from django.db import DatabaseError
from django.test import override_settings

from model_hub.selectors import eval_list_charts, eval_usage
from model_hub.tests.test_eval_usage_clickhouse_selector import _FakeClient
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
    is_application_read,
)
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests.test_application_read_transport import native_client
from tracer.tests.test_postgres_application_read_policy import FakePostgres
from tracer.views import dashboard, eval_task, span_attributes, trace_session

pytestmark = pytest.mark.unit
ID = "00000000-0000-4000-8000-000000000001"
PG_FAMILIES = ("tasks", "sessions", "session_picker", "dashboard_picker", "span_picker")


class StatementPostgres(FakePostgres):
    """Also model the old pickers' wrapped control cursor, for RED evidence."""

    @contextmanager
    def cursor(self):
        def execute(sql, params=None):
            return self.control(sql, params)

        yield SimpleNamespace(cursor=self.raw, execute=execute, fetchone=self.fetchone)


def run_pg_family(monkeypatch, family, pg, deadline=None, read=None):
    module = {
        "tasks": eval_task,
        "sessions": trace_session,
        "session_picker": trace_session,
        "dashboard_picker": dashboard,
        "span_picker": span_attributes,
    }[family]
    monkeypatch.setattr(module, "connection", pg)
    monkeypatch.setattr(module, "transaction", SimpleNamespace(atomic=pg.atomic))
    deadline = deadline or SimpleNamespace(remaining_ms=lambda *a, **kw: 5_000)
    read = read or (lambda: pg.execute("SELECT owned_metadata"))
    if family in ("tasks", "sessions"):
        scope = (
            eval_task._bounded_eval_task_read_transaction
            if family == "tasks"
            else trace_session._bounded_session_list_postgres_reads
        )
        with scope(deadline):
            return read()
    if family == "session_picker":
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.exists.side_effect = read
        request = object()
        authorize = Mock(return_value=queryset)
        monkeypatch.setattr(module, "_project_queryset_for_request", authorize)
        result = module._read_session_filter_project_in_scope(
            request=request, project_id=ID, deadline=deadline
        )
        authorize.assert_called_once_with(request)
        queryset.filter.assert_called_once_with(id=ID)
        return result
    runner = (
        dashboard._run_filter_value_pg_read
        if family == "dashboard_picker"
        else span_attributes._run_span_attribute_pg_read
    )
    return runner(deadline, read)


@pytest.mark.parametrize("family", PG_FAMILIES)
@pytest.mark.parametrize("outer", [False, True])
def test_public_pg_read_uses_uncapped_scope(monkeypatch, family, outer):
    pg = StatementPostgres(outer=outer)
    assert run_pg_family(monkeypatch, family, pg) == "SELECT owned_metadata"
    assert pg.query_timeouts == ["0"]
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers


@pytest.mark.parametrize("family", PG_FAMILIES)
@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("failure", ["install", "statement", "restore"])
def test_public_pg_failure_restores_and_propagates(monkeypatch, family, outer, failure):
    pg = StatementPostgres(outer=outer, failure=failure)
    with pytest.raises(DatabaseError):
        run_pg_family(monkeypatch, family, pg)
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers
    if outer:
        assert pg.prior_outer_work == ["unrelated write before read scope"]


@pytest.mark.parametrize("family", PG_FAMILIES)
@pytest.mark.parametrize("phase", ["before", "after", "between"])
def test_public_pg_request_checks_do_not_abort_statement(monkeypatch, family, phase):
    pg = StatementPostgres(outer=True)
    expired = [phase == "before"]

    def check(*args, **kwargs):
        if expired[0]:
            raise ReadDeadlineExceeded("cooperative expiry")
        return 5_000

    def read():
        result = pg.execute("SELECT completed")
        expired[0] = True
        if phase == "between":
            pg.execute("SELECT must_not_start")
        return result

    with pytest.raises(ReadDeadlineExceeded) if phase != "after" else nullcontext():
        run_pg_family(
            monkeypatch, family, pg, SimpleNamespace(remaining_ms=check), read
        )
    assert pg.query_timeouts == ([] if phase == "before" else ["0"])
    assert pg.timeout == "750ms" and not pg.wrappers


def run_eval(monkeypatch, family, owner):
    module = eval_list_charts if family == "charts" else eval_usage
    monkeypatch.setattr(module, "get_clickhouse_client", lambda: owner)
    if family == "charts":
        monkeypatch.setattr(module, "_cache_get", lambda *a: None)
        monkeypatch.setattr(module, "_cache_set", lambda *a, **kw: None)
        with override_settings(
            CLICKHOUSE={"CH_ENABLED": True, "CH_HOST": "offline.invalid"}
        ):
            return module.read_eval_list_charts(SimpleNamespace(id=ID), None, [ID])
    now = datetime(2026, 8, 2, tzinfo=UTC)
    return module.read_eval_usage(
        organization_id=ID,
        workspace_id=ID,
        project_ids=[ID],
        template_id=ID,
        start_date=now - timedelta(days=1),
        end_date=now,
        bucket_minutes=60,
        page=0,
        page_size=25,
    )


@pytest.mark.parametrize("family", ["charts", "usage"])
@pytest.mark.parametrize("fails", [False, True])
def test_eval_actual_native_settings_and_transport(monkeypatch, family, fails):
    owner, native, connection, sock = native_client(monkeypatch)
    fixture = _FakeClient()
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params))
        assert is_application_read()
        assert connection.send_receive_timeout is None and sock.timeout is None
        assert all(kwargs["settings"][key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
        assert kwargs["settings"]["max_memory_usage"] > 0
        assert kwargs["settings"]["max_threads"] == 2
        assert "LIMIT 1 BY id" in sql and params["organization_id"] == ID
        if fails:
            raise RuntimeError("compiler failure")
        if family == "charts":
            return [], []
        rows, columns, _ = fixture.execute_read(
            sql, params, timeout_ms=None, settings={}
        )
        return rows, columns

    native.execute.side_effect = execute
    if fails:
        with pytest.raises(RuntimeError, match="compiler failure"):
            run_eval(monkeypatch, family, owner)
    else:
        result = run_eval(monkeypatch, family, owner)
        assert (
            result["query_complete"] if family == "charts" else result.total_runs == 9
        )
        assert len(calls) == (1 if family == "charts" else 3)
    assert calls and not is_application_read()
    assert connection.send_receive_timeout == sock.timeout == 10
    assert owner._read_admission.acquire(blocking=False)
    owner._read_admission.release()


@pytest.mark.parametrize(
    "case,late_phase,next_phase",
    [
        ("zero_total", 1, None),
        ("zero_period", 2, None),
        ("final_page", 3, None),
        ("positive_total", 1, "chart"),
        ("positive_chart", 2, "page"),
    ],
)
def test_eval_usage_late_terminal_or_next_phase(
    monkeypatch, case, late_phase, next_phase
):
    clock, completed = [0.0], []
    monkeypatch.setattr(eval_usage.time, "monotonic", lambda: clock[0])
    original_result = Future.result

    def untimed_result(self, timeout=None):
        assert timeout is None, "must not abandon an in-flight statement"
        return original_result(self)

    monkeypatch.setattr(Future, "result", untimed_result)
    owner, native, connection, sock = native_client(monkeypatch)
    fixture = _FakeClient(total_runs=0 if case == "zero_total" else 9)

    def read(sql, params, **kwargs):
        rows, columns, _ = fixture.execute_read(
            sql, params, timeout_ms=None, settings={}
        )
        completed.append(sql)
        if len(completed) == late_phase:
            clock[0] = eval_usage.READ_TIMEOUT_MS / 1000 + 1
            if case == "zero_period":
                rows = []
        return rows, columns

    native.execute.side_effect = read
    if next_phase:
        with pytest.raises(eval_usage.EvalUsageReadError) as exc:
            run_eval(monkeypatch, "usage", owner)
        assert exc.value.code == eval_usage.EvalUsageReadErrorCode.DEADLINE_EXCEEDED
        assert exc.value.operations == (next_phase,)
    else:
        result = run_eval(monkeypatch, "usage", owner)
        assert result.completeness == eval_usage.EvalUsageReadCompleteness.COMPLETE
        assert result.unavailable_fields == ()
        assert result.total_runs == (0 if case == "zero_total" else 9)
        assert result.runs_period == (3 if case == "final_page" else 0)
        assert len(result.logs) == (3 if case == "final_page" else 0)
    assert len(completed) == late_phase  # No further query after cooperative expiry.
    assert connection.send_receive_timeout == sock.timeout == 10
    assert not is_application_read()


@pytest.mark.parametrize(
    "phase,failure,expected_code",
    [
        (1, RuntimeError("compiler failure"), None),
        (
            2,
            NetworkError("transport failure"),
            eval_usage.EvalUsageReadErrorCode.QUERY_FAILED,
        ),
        (
            3,
            ServerException("memory failure", code=241),
            eval_usage.EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
        ),
    ],
)
def test_eval_usage_late_real_error_never_publishes(
    monkeypatch, phase, failure, expected_code
):
    clock, calls = [0.0], []
    monkeypatch.setattr(eval_usage.time, "monotonic", lambda: clock[0])
    owner, native, connection, sock = native_client(monkeypatch)
    fixture = _FakeClient()

    def read(sql, params, **kwargs):
        calls.append(sql)
        if len(calls) == phase:
            clock[0] = eval_usage.READ_TIMEOUT_MS / 1000 + 1
            raise failure
        rows, columns, _ = fixture.execute_read(
            sql, params, timeout_ms=None, settings={}
        )
        return rows, columns

    native.execute.side_effect = read
    with pytest.raises(
        eval_usage.EvalUsageReadError if expected_code else RuntimeError
    ) as exc:
        run_eval(monkeypatch, "usage", owner)
    if expected_code:
        assert exc.value.code == expected_code and exc.value.__cause__ is failure
    else:
        assert exc.value is failure
    assert len(calls) == phase
    assert connection.send_receive_timeout == sock.timeout == 10
    assert not is_application_read()
