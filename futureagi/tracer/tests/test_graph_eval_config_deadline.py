"""Offline ownership/PG-control contracts; no database or wall-time benchmark."""

from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace

import pytest
from django.db import DatabaseError

from tracer.services.clickhouse import exact_graph_reads as graph

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
CONFIG = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


class FakePostgres:
    """Exercise Django wrapper/savepoint order and local-setting lifetime.

    The fake models PostgreSQL SET LOCAL rollback, not execution of the
    application SELECT. Actual config-query scope/parameters are asserted.
    """

    vendor = "postgresql"

    def __init__(self, *, outer=False, initial_timeout="0", outcome="ok"):
        self.in_atomic_block = outer
        self.timeout = initial_timeout
        self.outcome = outcome
        self.clock = 5.0
        self.wrappers = []
        self.events = []
        self.config_queries = 0
        self.config_query_timeout = None
        self.config_query_timeouts = []
        self.timeout_installs = 0

    @staticmethod
    def milliseconds(value):
        if value.endswith("ms"):
            return int(value[:-2])
        if value.endswith("s"):
            return int(value[:-1]) * 1000
        return int(value)

    @contextmanager
    def execute_wrapper(self, wrapper):
        self.wrappers.append(wrapper)
        try:
            yield
        finally:
            assert self.wrappers.pop() is wrapper

    @contextmanager
    def atomic(self):
        was_atomic, prior_timeout = self.in_atomic_block, self.timeout
        self.in_atomic_block = True
        self.events.append("savepoint" if was_atomic else "begin")
        if was_atomic:
            self.execute("SAVEPOINT ownership", ())
        try:
            yield
        except BaseException:
            self.timeout = prior_timeout
            self.events.append("rollback-savepoint" if was_atomic else "rollback")
            raise
        else:
            if was_atomic:
                # Wrapper must be removed before RELEASE, otherwise timeout
                # control would run again after its explicit restoration.
                self.execute("RELEASE SAVEPOINT ownership", ())
            else:
                self.timeout = prior_timeout
            self.events.append("release" if was_atomic else "commit")
        finally:
            self.in_atomic_block = was_atomic

    @contextmanager
    def cursor(self):
        yield SimpleNamespace(cursor=self, execute=self.execute)

    def fetchone(self):
        return self.timeout, self.milliseconds(self.timeout)

    def execute(self, sql, params=()):
        # Existing annotation-label discovery is mocked; model only its local
        # transaction controls so public annotation membership stays real.
        if sql.startswith("SET TRANSACTION "):
            assert self.in_atomic_block
            return None
        if sql.startswith("SET LOCAL statement_timeout = "):
            assert self.in_atomic_block
            self.timeout = sql.split("'")[1]
            return None
        if sql.startswith("SELECT current_setting"):
            self.events.append("read-prior-timeout")
            return None
        if sql.startswith("SELECT set_config"):
            assert "'statement_timeout', %s, true" in sql
            assert self.in_atomic_block
            self.timeout_installs += 1
            if (
                self.outcome == "raw-install-failure" and self.timeout_installs == 1
            ) or (self.outcome == "raw-restore-failure" and self.timeout_installs == 2):
                raise RuntimeError("private raw driver timeout-control failure")
            if (self.outcome == "install-failure" and self.timeout_installs == 1) or (
                self.outcome == "restore-failure" and self.timeout_installs == 2
            ):
                raise DatabaseError("private timeout-control failure")
            self.timeout = params[0]
            self.events.append(("local-timeout", self.timeout))
            if self.outcome == "slow-install" and self.timeout_installs == 1:
                self.clock = 0.501
            return None

        def underlying(query, bound, many, context):
            if query.startswith(("SAVEPOINT", "RELEASE")):
                self.events.append(query)
                return None
            assert (
                query
                == "SELECT id FROM tracer_custom_eval_config WHERE project_id = %s AND deleted = false"
            )
            assert bound == (PROJECT,)
            assert self.in_atomic_block
            self.config_queries += 1
            self.config_query_timeout = self.milliseconds(self.timeout)
            self.config_query_timeouts.append(self.config_query_timeout)
            assert self.config_query_timeout == 0
            if self.outcome == "query-failure":
                raise DatabaseError("private database timeout detail")
            if self.outcome == "slow-success":
                self.clock = 11.0
            return None

        wrapped = underlying
        for wrapper in reversed(self.wrappers):
            wrapped = partial(wrapper, wrapped)
        return wrapped(sql, params, False, {"cursor": SimpleNamespace(cursor=self)})


@pytest.fixture
def install(monkeypatch):
    def setup(*, outer=False, initial_timeout="0", outcome="ok", ids=(CONFIG,)):
        pg = FakePostgres(outer=outer, initial_timeout=initial_timeout, outcome=outcome)
        monkeypatch.setattr(graph, "connection", pg)
        monkeypatch.setattr(graph.transaction, "atomic", pg.atomic)
        monkeypatch.setattr(graph, "monotonic", lambda: pg.clock)
        monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 10_000)
        lookups = []

        def owned_configs(**lookup):
            assert lookup == {"project_id": PROJECT, "deleted": False}
            lookups.append(lookup)

            class Values:
                def __iter__(self):
                    pg.execute(
                        "SELECT id FROM tracer_custom_eval_config WHERE project_id = %s AND deleted = false",
                        (PROJECT,),
                    )
                    yield from ids

            def values_list(*fields, flat):
                assert fields == ("id",) and flat is True
                return Values()

            return SimpleNamespace(values_list=values_list)

        monkeypatch.setattr(
            graph.CustomEvalConfig.no_workspace_objects, "filter", owned_configs
        )
        return pg, lookups

    return setup


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("initial_timeout", ["0", "250ms", "8s"])
def test_owned_lookup_removes_statement_cap_and_restores_previous_setting(
    install, outer, initial_timeout
):
    pg, lookups = install(outer=outer, initial_timeout=initial_timeout)
    assert graph._owned_user_eval_config_ids(PROJECT, started=0.0) == (CONFIG,)
    assert lookups == [{"project_id": PROJECT, "deleted": False}]
    assert pg.config_queries == 1
    assert pg.config_query_timeout == 0
    assert pg.timeout == initial_timeout and pg.in_atomic_block is outer
    assert pg.timeout_installs == 2 and pg.wrappers == []


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize(
    "outcome",
    [
        "query-failure",
        "install-failure",
        "restore-failure",
        "raw-install-failure",
        "raw-restore-failure",
        "slow-success",
    ],
)
def test_failure_rolls_back_settings_and_never_returns_owned_ids(
    install, outer, outcome
):
    pg, _ = install(outer=outer, initial_timeout="8s", outcome=outcome)
    with pytest.raises(graph.ExactGraphReadError) as error:
        graph._owned_user_eval_config_ids(PROJECT, started=0.0)
    assert "private" not in str(error.value)
    assert pg.timeout == "8s" and pg.in_atomic_block is outer
    assert pg.wrappers == []
    # A completed read restores its connection settings normally. Expiry then
    # prevents the next graph read; it does not retroactively abort the SELECT.
    expected_exit = (
        ("release" if outer else "commit")
        if outcome == "slow-success"
        else ("rollback-savepoint" if outer else "rollback")
    )
    assert pg.events[-1] == expected_exit
    if outcome in {"install-failure", "raw-install-failure"}:
        assert pg.config_queries == 0


@pytest.mark.parametrize("started", [None, 0.0])
def test_missing_or_expired_deadline_skips_lookup_and_transaction(install, started):
    pg, lookups = install()
    pg.clock = 11.0
    with pytest.raises(graph.ExactGraphReadError, match="deadline"):
        graph._owned_user_eval_config_ids(PROJECT, started=started)
    assert lookups == [] and pg.events == [] and pg.config_queries == 0


@pytest.mark.parametrize("ids", [None, (None,), ("not-an-owned-id",)])
def test_unknown_or_invalid_ownership_cannot_become_empty_success(install, ids):
    pg, _ = install(outer=True, ids=ids)
    with pytest.raises(graph.ExactGraphReadError, match="Evaluation metadata"):
        graph._owned_user_eval_config_ids(PROJECT, started=0.0)
    assert pg.timeout == "0" and pg.events[-1] == "rollback-savepoint"


def test_proven_empty_config_set_is_distinct_from_failed_ownership(install):
    pg, lookups = install(ids=())
    assert graph._owned_user_eval_config_ids(PROJECT, started=0.0) == ()
    assert lookups and pg.config_queries == 1 and pg.events[-1] == "commit"


def test_missing_project_fails_before_any_database_work(install):
    pg, lookups = install()
    with pytest.raises(graph.ExactGraphReadError, match="valid project"):
        graph._owned_user_eval_config_ids(None, started=0.0)
    assert lookups == [] and pg.events == []


def test_public_user_eval_expired_wall_does_not_start_ownership_lookup(monkeypatch):
    from tracer.tests.test_graph_public_source_routing import (
        RecordingAnalytics,
        eval_filters,
    )

    times = iter((0.0, 11.0))
    monkeypatch.setattr(graph, "monotonic", lambda: next(times, 11.0))
    monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 10_000)

    def forbidden(**kwargs):
        pytest.fail("expired public graph attempted ownership lookup")

    monkeypatch.setattr(
        graph.CustomEvalConfig.no_workspace_objects, "filter", forbidden
    )
    analytics = RecordingAnalytics()
    with pytest.raises(graph.ExactGraphReadError, match="deadline"):
        graph.read_exact_user_system_graph(
            analytics=analytics,
            project_id=PROJECT,
            filters=eval_filters(),
            interval="day",
            metric_id="active_users",
        )
    assert analytics.calls == []


@pytest.mark.parametrize(
    "budget_ms,elapsed_ms",
    [(500, 250), (5_000, 4_750), (9_500, 9_250), (9_500, 0)],
)
def test_public_request_checks_do_not_become_owned_metadata_statement_caps(
    install, monkeypatch, budget_ms, elapsed_ms
):
    from tracer.services.clickhouse import graph_dispatch as dispatch
    from tracer.services.clickhouse import read_budget
    from tracer.tests.test_graph_public_source_routing import (
        RecordingAnalytics,
        eval_filters,
    )

    pg, lookups = install()
    pg.clock = 0.0
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: pg.clock)
    monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 30 * 60 * 1_000)
    original_clauses = graph._user_filter_clauses

    def expensive_clauses(*args, **kwargs):
        result = original_clauses(*args, **kwargs)
        pg.clock = elapsed_ms / 1_000
        return result

    monkeypatch.setattr(graph, "_user_filter_clauses", expensive_clauses)
    analytics = RecordingAnalytics()
    result = dispatch.fetch_user_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT,
        filters=eval_filters(),
        interval="day",
        metric_id="active_users",
        timeout_ms=budget_ms,
    )
    assert pg.config_query_timeout == 0
    assert result["query_complete"] is True
    assert lookups == [{"project_id": PROJECT, "deleted": False}]
    assert len(analytics.calls) == 1
    query, params, call = analytics.calls[0]
    assert "eval_scan.custom_eval_config_id IN %(user_eval_config_ids)s" in query
    assert params["user_eval_config_ids"] == (CONFIG,)
    assert call["timeout_ms"] <= budget_ms - elapsed_ms


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("phase", ["before-lookup", "after-install", "after-select"])
def test_public_expiry_skips_or_discards_ownership_without_background_grant(
    install, monkeypatch, outer, phase
):
    from tracer.services.clickhouse import graph_dispatch as dispatch
    from tracer.services.clickhouse import read_budget
    from tracer.tests.test_graph_public_source_routing import (
        RecordingAnalytics,
        eval_filters,
    )

    pg, lookups = install(
        outer=outer,
        initial_timeout="8s",
        outcome={"after-select": "slow-success", "after-install": "slow-install"}.get(
            phase, "ok"
        ),
    )
    pg.clock = 0.0
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: pg.clock)
    monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 30 * 60 * 1_000)
    original_clauses = graph._user_filter_clauses

    def expensive_clauses(*args, **kwargs):
        result = original_clauses(*args, **kwargs)
        pg.clock = 0.501 if phase == "before-lookup" else 0.250
        return result

    monkeypatch.setattr(graph, "_user_filter_clauses", expensive_clauses)
    analytics = RecordingAnalytics()
    result = dispatch.fetch_user_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT,
        filters=eval_filters(),
        interval="day",
        metric_id="active_users",
        timeout_ms=500,
    )
    assert result["query_complete"] is False
    assert result["query_error_code"] == "read_budget_exceeded"
    assert analytics.calls == []
    assert pg.timeout == "8s" and pg.in_atomic_block is outer
    assert pg.wrappers == []
    if phase == "before-lookup":
        assert lookups == [] and pg.events == [] and pg.config_queries == 0
    else:
        assert lookups == [{"project_id": PROJECT, "deleted": False}]
        if phase == "after-install":
            assert pg.config_queries == 0 and pg.config_query_timeout is None
        else:
            assert pg.config_queries == 1 and pg.config_query_timeout == 0
        expected_exit = (
            ("release" if outer else "commit")
            if phase == "after-select"
            else ("rollback-savepoint" if outer else "rollback")
        )
        assert pg.events[-1] == expected_exit


def test_background_metadata_is_uncapped_with_request_checks_separate(
    install, monkeypatch
):
    pg, _ = install()
    monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 30 * 60 * 1_000)
    assert graph._owned_user_eval_config_ids(PROJECT, started=0.0) == (CONFIG,)
    assert pg.config_query_timeout == 0
    pg.clock = 1_799.75
    assert graph._owned_user_eval_config_ids(PROJECT, started=0.0) == (CONFIG,)
    assert pg.config_query_timeout == 0


def test_adapter_remaining_read_ms_does_not_restart_request(monkeypatch):
    from tracer.services.clickhouse import graph_dispatch as dispatch
    from tracer.services.clickhouse import read_budget

    clock = [0.0]
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: clock[0])
    adapter = dispatch._DeadlineBoundGraphAnalytics(
        object(), read_budget.ReadDeadline.start(500)
    )
    assert adapter.remaining_read_ms(1_000) == 500
    clock[0] = 0.250
    assert adapter.remaining_read_ms(1_000) == 250
    assert adapter.remaining_read_ms(100) == 100
    clock[0] = 0.501
    with pytest.raises(read_budget.ReadDeadlineExceeded):
        adapter.remaining_read_ms(1_000)


@pytest.mark.parametrize("surface", ["eval", "annotation"])
def test_public_entity_graph_forwards_original_wall_to_each_owned_lookup(
    install, monkeypatch, surface
):
    from tracer.services.clickhouse import graph_dispatch as dispatch
    from tracer.services.clickhouse import read_budget
    from tracer.tests.test_graph_public_source_routing import (
        RecordingAnalytics,
        eval_filters,
    )

    pg, lookups = install()
    pg.clock = 0.0
    monkeypatch.setattr(read_budget.time, "monotonic", lambda: pg.clock)
    monkeypatch.setattr(graph, "EXACT_GRAPH_QUERY_TIMEOUT_MS", 30 * 60 * 1_000)
    original_clauses = graph._user_filter_clauses

    def expensive_clauses(*args, **kwargs):
        result = original_clauses(*args, **kwargs)
        pg.clock += 0.125
        return result

    monkeypatch.setattr(graph, "_user_filter_clauses", expensive_clauses)
    config = SimpleNamespace(
        name="quality",
        eval_template=SimpleNamespace(config={"output": "SCORE"}, choices=[]),
    )

    def get_config(**kwargs):
        assert kwargs == {"id": CONFIG, "project_id": PROJECT, "deleted": False}
        return config

    monkeypatch.setattr(
        graph.CustomEvalConfig.objects,
        "select_related",
        lambda *args: SimpleNamespace(get=get_config),
    )

    def labels(project_id):
        assert project_id == PROJECT
        return SimpleNamespace(
            get=lambda **kwargs: SimpleNamespace(name="quality", type="numeric")
        )

    monkeypatch.setattr(graph, "get_annotation_labels_for_project", labels)

    # Score transport is outside the ownership regression. Both real user
    # selectors above it must compile before this empty score partition.
    class EmptyScores:
        def order_by(self, *args):
            return self

        def values(self, *args):
            return self

        def iterator(self, **kwargs):
            return iter(())

    monkeypatch.setattr(
        graph.Score.no_workspace_objects, "filter", lambda *a, **k: EmptyScores()
    )
    analytics = RecordingAnalytics()
    fetch = (
        dispatch.fetch_eval_graph_ch
        if surface == "eval"
        else dispatch.fetch_annotation_graph_ch
    )
    result = fetch(
        analytics=analytics,
        project_id=PROJECT,
        filters=eval_filters(),
        interval="day",
        req_data_config={"id": CONFIG, "output_type": "float"},
        observe_type="trace",
        aggregation_context="user",
        timeout_ms=500,
    )
    assert result["query_complete"] is True
    expected = [0] if surface == "eval" else [0, 0]
    assert pg.config_query_timeouts == expected
    assert lookups == [{"project_id": PROJECT, "deleted": False}] * len(expected)
    assert pg.timeout == "0" and pg.wrappers == []
