"""Offline PG setting/scope contracts, not a real PostgreSQL qualification."""

from contextlib import contextmanager, nullcontext
from functools import partial
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, OperationalError

from tracer.services.postgres_read_policy import (
    ApplicationPostgresReadError,
    application_postgres_reads,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class FakePostgres:
    """Model transaction-local rollback and the real execute-wrapper order."""

    vendor = "postgresql"

    def __init__(self, *, outer=False, timeout="750ms", failure=None):
        self.in_atomic_block = outer
        self.timeout = timeout
        self.failure = failure
        self.wrappers = []
        self.events = []
        self.query_timeouts = []
        self.prior_outer_work = ["unrelated write before read scope"] if outer else []
        self.raw = SimpleNamespace(execute=self.control, fetchone=self.fetchone)

    def control(self, sql, params=None):
        assert self.in_atomic_block
        self.events.append((sql, params))
        if sql == "SELECT current_setting('statement_timeout')":
            return
        if sql.startswith("SET TRANSACTION "):
            return
        assert sql == "SELECT set_config('statement_timeout', %s, true)"
        setting = params[0]
        if self.failure == ("install" if setting == "0" else "restore"):
            raise RuntimeError("private raw driver error")
        self.timeout = setting

    def fetchone(self):
        if self.failure == "fetch":
            raise OperationalError("private setting read error")
        return (self.timeout,)

    @contextmanager
    def execute_wrapper(self, wrapper):
        self.wrappers.append(wrapper)
        try:
            yield
        finally:
            assert self.wrappers.pop() is wrapper

    @contextmanager
    def cursor(self):
        yield SimpleNamespace(cursor=self.raw, execute=self.execute)

    @contextmanager
    def atomic(self):
        if self.failure == "begin":
            raise DatabaseError("private begin error")
        was_atomic, prior = self.in_atomic_block, self.timeout
        prior_work = list(self.prior_outer_work)
        self.in_atomic_block = True
        self.events.append(("savepoint" if was_atomic else "begin", None))
        if was_atomic:
            self.execute("SAVEPOINT read_scope")
        try:
            yield
        except BaseException:
            self.timeout = prior
            self.prior_outer_work[:] = prior_work
            self.events.append(
                ("rollback-savepoint" if was_atomic else "rollback", None)
            )
            raise
        else:
            if was_atomic:
                self.execute("RELEASE SAVEPOINT read_scope")
            else:
                self.timeout = prior
            self.events.append(("release" if was_atomic else "commit", None))
        finally:
            self.in_atomic_block = was_atomic

    def execute(self, sql, params=()):
        def underlying(sql, params, many, context):
            if sql.startswith(("SAVEPOINT", "RELEASE")):
                self.events.append((sql, params))
                return
            self.events.append((sql, params))
            self.query_timeouts.append(self.timeout)
            if self.failure == "statement":
                raise OperationalError("private database failure")
            return sql

        execute = underlying
        for wrapper in reversed(self.wrappers):
            execute = partial(wrapper, execute)
        return execute(sql, params, False, {"cursor": SimpleNamespace(cursor=self.raw)})

    def scope(self, **kwargs):
        return application_postgres_reads(connection=self, atomic=self.atomic, **kwargs)


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("timeout", ["0", "17ms", "8s"])
def test_unlimited_read_restores_inherited_setting(outer, timeout):
    pg = FakePostgres(outer=outer, timeout=timeout)
    with pg.scope():
        assert pg.events == []
        assert pg.execute("SELECT first") == "SELECT first"
        assert pg.execute("SELECT second") == "SELECT second"
    assert pg.query_timeouts == ["0", "0"]
    settings = [
        params for sql, params in pg.events if sql.startswith("SELECT set_config")
    ]
    assert settings == [("0",), (timeout,)]
    assert pg.timeout == timeout and pg.in_atomic_block is outer and not pg.wrappers


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize(
    "failure", ["begin", "install", "fetch", "statement", "restore"]
)
def test_failure_restores_setting_and_remains_database_error(outer, failure):
    pg = FakePostgres(outer=outer, failure=failure)
    with pytest.raises(DatabaseError) as error:
        with pg.scope():
            pg.execute("SELECT failed")
    assert not isinstance(error.value, TimeoutError)
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers
    if failure in {"install", "restore"}:
        assert isinstance(error.value, ApplicationPostgresReadError)
        assert "private" not in str(error.value)


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("initialize_outer", [False, True])
@pytest.mark.parametrize("inner_fails", [False, True])
def test_nested_application_scopes_restore_outer_policy(
    outer, initialize_outer, inner_fails
):
    pg = FakePostgres(outer=outer)
    with pg.scope():
        if initialize_outer:
            pg.execute("SELECT outer")
        try:
            with pg.scope():
                pg.execute("SELECT inner")
                if inner_fails:
                    raise ValueError("caller failure")
        except ValueError:
            pass
        assert pg.timeout == "0"
        pg.execute("SELECT still_outer")
    assert pg.query_timeouts and set(pg.query_timeouts) == {"0"}
    assert pg.timeout == "750ms" and pg.in_atomic_block is outer and not pg.wrappers


@pytest.mark.parametrize("outer", [False, True])
def test_mock_only_scope_does_not_open_connection_or_change_settings(outer):
    pg = FakePostgres(outer=outer)
    with pg.scope(read_only=True, repeatable_read=True):
        pass
    assert pg.events == [] and pg.timeout == "750ms" and not pg.wrappers


@pytest.mark.parametrize("outer", [False, True])
def test_snapshot_characteristics_precede_first_read_and_never_override_outer(outer):
    pg = FakePostgres(outer=outer)
    with pg.scope(read_only=True, repeatable_read=True):
        pg.execute("SELECT snapshot")
    characteristics = [sql for sql, _ in pg.events if sql.startswith("SET TRANSACTION")]
    assert characteristics == (
        [] if outer else ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"]
    )
    assert pg.query_timeouts == ["0"]


@pytest.mark.parametrize("phase", ["before", "after", "between"])
def test_request_expiry_is_not_installed_as_statement_timeout(phase):
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    pg = FakePostgres()
    expired = [phase == "before"]

    def check():
        if expired[0]:
            raise ReadDeadlineExceeded("request deadline exceeded")

    with pytest.raises(ReadDeadlineExceeded) if phase != "after" else nullcontext():
        with pg.scope(check_request=check):
            pg.execute("SELECT complete")
            expired[0] = True
            if phase == "between":
                pg.execute("SELECT must_not_start")
    assert pg.query_timeouts == ([] if phase == "before" else ["0"])
    assert pg.timeout == "750ms" and not pg.wrappers
    if phase == "before":
        assert pg.events == []


def test_failed_read_savepoint_preserves_prior_outer_work_and_usable_transaction():
    pg = FakePostgres(outer=True, failure="statement")
    with pytest.raises(DatabaseError):
        with pg.scope(read_only=True):
            pg.execute("SELECT read_scope")
    assert pg.prior_outer_work == ["unrelated write before read scope"]
    assert pg.in_atomic_block and pg.events[-1][0] == "rollback-savepoint"
    pg.failure = None
    assert pg.execute("SELECT later_outer_work") == "SELECT later_outer_work"
    assert pg.timeout == "750ms"


def test_pg_scope_source_has_only_unlimited_install_and_previous_setting_restore():
    import ast
    import inspect
    from textwrap import dedent

    from tracer.services import dashboard_metrics_catalog
    from tracer.services.clickhouse import (
        dashboard_action_deadline,
        exact_graph_reads,
        graph_action_deadline,
        list_request_deadline,
    )
    from tracer.services.clickhouse.v2 import trace_session_dict_reader

    callers = [
        list_request_deadline.bounded_list_postgres_reads,
        graph_action_deadline.graph_action_postgres_budget,
        dashboard_action_deadline.bounded_dashboard_postgres_reads,
        dashboard_metrics_catalog._run_metrics_catalog_pg_read,
        dashboard_metrics_catalog._run_metrics_catalog_pg_snapshot,
        exact_graph_reads._owned_user_eval_config_ids,
        exact_graph_reads.read_exact_eval_graph,
        exact_graph_reads.read_exact_annotation_graph,
        trace_session_dict_reader.resolve_session_fields,
    ]
    for caller in callers:
        source = inspect.getsource(caller)
        assert "application_postgres_reads(" in source
        tree = ast.parse(dedent(source))
        strings = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not any(
            "set_config('statement_timeout'" in value
            or "SET LOCAL statement_timeout" in value
            for value in strings
        )
        assert "set_rollback(" not in source
    tree = ast.parse(dedent(inspect.getsource(application_postgres_reads)))
    setting_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "control"
        and len(node.args) == 3
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "SELECT set_config('statement_timeout', %s, true)"
    ]
    assert len(setting_calls) == 2
    assert {ast.unparse(node.args[2]) for node in setting_calls} == {
        "('0',)",
        "(previous_timeout,)",
    }
