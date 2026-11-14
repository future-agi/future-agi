"""Service-free outbox CDC contracts. Postgres behaviour is covered by
test_oss_outbox_cdc_pg.py; neither suite is proof of ClickHouse behaviour."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import uuid
from collections import Counter
from contextlib import nullcontext
from types import SimpleNamespace

import psycopg
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError, DataError
from clickhouse_connect.driver.exceptions import OperationalError as CHOperationalError
from django.core.exceptions import ImproperlyConfigured

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse import oss_outbox_cdc as cdc
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import source_client

DERIVED = upgrade.MIGRATIONS["cdc_002_usage_eval_fields.sql"]


@pytest.mark.parametrize(
    "value, mode",
    [(None, "peerdb"), ("outbox", "outbox"), (" Outbox ", "outbox"), ("OFF", "off")],
)
def test_cdc_mode_defaults_to_peerdb_and_normalizes(value, mode):
    env = {} if value is None else {"FI_CDC_MODE": value}
    assert cdc.cdc_mode(env) == mode


@pytest.mark.parametrize("value", ["", "outbax", "lite", "true"])
def test_unknown_cdc_mode_fails_closed(value):
    with pytest.raises(cdc.OutboxCDCError, match="FI_CDC_MODE"):
        cdc.cdc_mode({"FI_CDC_MODE": value})


def _fresh_module(monkeypatch, env):
    """Execute oss_outbox_cdc again under ``env``, leaving the loaded one alone."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("fresh_oss_outbox_cdc", cdc.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_blank_tuning_variables_mean_the_defaults(monkeypatch):
    # Compose and Helm pass unset variables through as empty strings.
    module = _fresh_module(
        monkeypatch, {"FI_CDC_DRAIN_BATCH": "", "FI_CDC_MAX_LAG_SECONDS": " "}
    )
    assert (module.DRAIN_BATCH, module.CHECK_MAX_LAG_S) == (5000, 900)


def test_a_malformed_tuning_variable_names_itself(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="FI_CDC_SNAPSHOT_PAGE"):
        _fresh_module(monkeypatch, {"FI_CDC_SNAPSHOT_PAGE": "10k"})


@pytest.mark.parametrize("name", ["FI_CDC_DRAIN_BATCH", "FI_CDC_SNAPSHOT_PAGE"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_page_size_below_one_names_itself(monkeypatch, name, value):
    # A 0-row page is never short, so the drain never finishes a table and a
    # snapshot never completes.
    with pytest.raises(ImproperlyConfigured, match=f"^{name} must be between 1 and"):
        _fresh_module(monkeypatch, {name: value})


def test_version_clock_is_strictly_increasing_across_backwards_steps_and_floor():
    ticks = iter([100, 50, 50, 200, 10])
    clock = cdc.VersionClock(floor=120, now=lambda: next(ticks))
    assert [clock.next() for _ in range(5)] == [121, 122, 123, 200, 201]
    assert clock.floor == 120


def test_landing_tables_are_the_peerdb_source_profile_without_derived_columns():
    source = source_client().inspect_source()
    profile = core._source_definitions(source)
    created = cdc.landing_definitions(source, include_usage_schema=True)
    assert set(created) == set(profile) == set(core.landing_tables())
    for name, ddl in created.items():
        if name != "usage_apicalllog":
            assert ddl == profile[name]
    columns = core._table(created["usage_apicalllog"])[0]
    assert not {column for _, column in DERIVED} & set(columns)
    assert set(core._table(profile["usage_apicalllog"])[0]) - set(columns) == {
        column for _, column in DERIVED
    }
    # PeerDB's physical contract, which every FINAL and _peerdb_* read relies on.
    for ddl in created.values():
        _, _, clauses = core._table(ddl)
        assert clauses["ENGINE"] == core._tokens(
            "ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted)"
        )
        assert core._key(clauses["ORDER"]) == ("id",)


def test_changed_source_profile_layout_fails_closed(monkeypatch):
    source = source_client().inspect_source()
    monkeypatch.setattr(
        core,
        "_source_definitions",
        lambda *a, **k: {
            "usage_apicalllog": "CREATE TABLE IF NOT EXISTS usage_apicalllog (id Int64)"
        },
    )
    with pytest.raises(cdc.OutboxCDCError, match="layout"):
        cdc.landing_definitions(source, include_usage_schema=True)


def test_select_expressions_match_the_qualified_transport():
    assert cdc.select_expr("payload", "jsonb") == '"payload"::text'
    assert cdc.select_expr("languages", "_varchar") == (
        "array_replace(coalesce(\"languages\", ARRAY[]::varchar[]), NULL, '')"
    )
    assert cdc.select_expr("name", "text") == '"name"'
    assert cdc._arrival_expr(
        {"updated_at": "timestamptz", "created_at": "timestamptz"}
    ) == ('coalesce("updated_at", "created_at")')
    assert cdc._arrival_expr({"updated_at": "text"}) == "NULL::timestamptz"
    with pytest.raises(cdc.OutboxCDCError):
        cdc.select_expr('x"; DROP TABLE t; --', "text")


PEERDB = {
    "_peerdb_synced_at": "DateTime64(9)",
    "_peerdb_is_deleted": "UInt8",
    "_peerdb_version": "UInt64",
}


def test_spec_copies_shared_columns_and_reports_drift_and_ch_only_columns():
    spec = cdc._spec(
        "usage_apicalllog",
        {
            "id": "int8",
            "config": "jsonb",
            "updated_at": "timestamptz",
            "new_col": "text",
        },
        ["id"],
        {"id": "Int64", "config": "String", "legacy": "String", **PEERDB},
        {"id", "config", "legacy", "eval_score", *PEERDB},
    )
    assert spec.columns == ("id", "config")
    assert spec.drift == ("updated_at", "new_col")
    assert not spec.pk_is_uuid and spec.pk_value("42") == 42
    assert spec.select_sql == (
        'SELECT "id", "config"::text, coalesce("updated_at") FROM public."usage_apicalllog"'
    )


@pytest.mark.parametrize(
    "udts, pk, ch_types, message",
    [
        ({}, ["id"], {"id": "UUID", **PEERDB}, "missing in PostgreSQL"),
        ({"id": "uuid"}, ["id", "x"], {"id": "UUID", **PEERDB}, "single-column id"),
        ({"id": "uuid"}, ["key"], {"id": "UUID", **PEERDB}, "single-column id"),
        ({"id": "uuid"}, ["id"], {}, "missing in ClickHouse"),
        ({"id": "uuid"}, ["id"], {"id": "UUID"}, "PeerDB columns"),
        ({"id": "uuid"}, ["id"], {"key": "UUID", **PEERDB}, "id column missing"),
    ],
)
def test_undrainable_table_shapes_fail_closed(udts, pk, ch_types, message):
    with pytest.raises(cdc.OutboxCDCError, match=message):
        cdc._spec("t", udts, pk, ch_types, set(ch_types))


def test_trigger_sql_is_statement_level_with_transition_tables():
    assert cdc.create_trigger_sql("tracer_trace", "fi_cdc_upd") == (
        'CREATE TRIGGER fi_cdc_upd AFTER UPDATE ON public."tracer_trace" '
        "REFERENCING NEW TABLE AS fi_cdc_new FOR EACH STATEMENT "
        "EXECUTE FUNCTION public.fi_cdc_capture_new()"
    )
    assert cdc.create_trigger_sql("t", "fi_cdc_trunc") == (
        'CREATE TRIGGER fi_cdc_trunc AFTER TRUNCATE ON public."t" '
        "FOR EACH STATEMENT EXECUTE FUNCTION public.fi_cdc_capture_truncate()"
    )


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def _trigger_row(table, name, **changes):
    _, tgtype, transition, function = cdc.TRIGGERS[name]
    row = {
        "table": table,
        "name": name,
        "enabled": "O",
        "tgtype": tgtype,
        "new": transition if transition == "fi_cdc_new" else None,
        "old": transition if transition == "fi_cdc_old" else None,
        "attributes": "",
        "unqualified": True,
        "function": function,
        **changes,
    }
    return tuple(row.values())


@pytest.mark.parametrize(
    "changes, state",
    [
        ({}, "ok"),
        ({"enabled": "A"}, "ok"),
        ({"enabled": "D"}, "disabled"),
        ({"enabled": "R"}, "disabled"),  # fires only for replicas
        ({"tgtype": 5}, "wrong"),  # FOR EACH ROW
        ({"function": "other"}, "wrong"),
        ({"attributes": "3"}, "wrong"),  # UPDATE OF <column>
        ({"unqualified": False}, "wrong"),  # WHEN (...)
        ({"new": None}, "wrong"),
    ],
)
def test_capture_state_compares_catalog_fields_not_formatted_text(changes, state):
    rows = [_trigger_row("t", "fi_cdc_upd", **changes)]
    rows += [_trigger_row("t", n) for n in ("fi_cdc_ins", "fi_cdc_del")]
    pg = SimpleNamespace(execute=lambda sql, params: Rows(rows))
    states = cdc.capture_state(pg, ("t",))
    assert states == {
        "t": {
            "fi_cdc_ins": "ok",
            "fi_cdc_upd": state,
            "fi_cdc_del": "ok",
            "fi_cdc_trunc": "missing",
        }
    }
    assert cdc.broken_capture(states) == {
        "t": sorted({"fi_cdc_trunc", *(["fi_cdc_upd"] if state != "ok" else [])})
    }


def test_arm_only_touches_broken_triggers_in_one_transaction(monkeypatch):
    issued = []

    def ddl(pg, statements, *, attempts, then):
        issued.append(statements)
        then()  # last, inside the same transaction

    monkeypatch.setattr(cdc, "_ddl", ddl)
    monkeypatch.setattr(
        cdc,
        "_reset_snapshots",
        lambda pg, tables: issued.append(("resnapshot", tables)),
    )
    cdc.arm_triggers(
        None,
        "t",
        {
            "fi_cdc_ins": "ok",
            "fi_cdc_upd": "disabled",
            "fi_cdc_del": "wrong",
            "fi_cdc_trunc": "missing",
        },
    )
    assert issued == [
        [
            'ALTER TABLE public."t" ENABLE TRIGGER fi_cdc_upd',
            'DROP TRIGGER fi_cdc_del ON public."t"',
            cdc.create_trigger_sql("t", "fi_cdc_del"),
            cdc.create_trigger_sql("t", "fi_cdc_trunc"),
        ],
        ("resnapshot", ("t",)),
    ]
    issued.clear()
    cdc.arm_triggers(None, "t", dict.fromkeys(cdc.TRIGGERS, "ok"))
    assert issued == []


class FlakyPg:
    """Fails lock acquisition ``failures`` times, then succeeds."""

    def __init__(self, failures):
        self.failures, self.statements = failures, []

    def transaction(self):
        return nullcontext()

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("CREATE") and self.failures:
            self.failures -= 1
            raise psycopg.errors.LockNotAvailable("lock timeout")


def test_ddl_sets_a_lock_timeout_and_retries_a_bounded_number_of_times(monkeypatch):
    monkeypatch.setattr(cdc.time, "sleep", lambda _: None)
    pg = FlakyPg(failures=2)
    cdc._ddl(pg, ["CREATE TRIGGER x"], attempts=3)
    assert (
        pg.statements
        == [
            f"SET LOCAL lock_timeout = '{cdc.DDL_LOCK_TIMEOUT_MS}ms'",
            "CREATE TRIGGER x",
        ]
        * 3
    )
    with pytest.raises(cdc.OutboxCDCError, match="long transaction"):
        cdc._ddl(FlakyPg(failures=5), ["CREATE TRIGGER x"], attempts=3)


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

SPEC = cdc.TableSpec(
    name="t",
    columns=("id",),
    types={"id": "UUID", **PEERDB},
    ch_columns=frozenset({"id", *PEERDB}),
    select_sql='SELECT "id", NULL::timestamptz FROM public."t"',
    pk_is_uuid=True,
    drift=(),
)


def _isolate(monkeypatch, fail, keys, budget=10):
    applied, parked, probes = [], [], []

    def apply_keys(pg, ch, spec, values, clock, stats, **_):
        error = fail(values)
        if error:
            raise error
        applied.extend(values)

    monkeypatch.setattr(cdc, "apply_keys", apply_keys)
    monkeypatch.setattr(cdc, "park", lambda pg, table, pk, error: parked.append(pk))
    monkeypatch.setattr(cdc, "_probe", lambda pg, ch, table: probes.append(table))
    result = cdc._apply_isolated(None, None, SPEC, keys, None, Counter(), budget)
    return result, applied, parked, probes


def test_poison_rows_are_bisected_out_and_parked(monkeypatch):
    keys = [str(uuid.uuid4()) for _ in range(9)]
    poison = {uuid.UUID(keys[2]), uuid.UUID(keys[7])}
    result, applied, parked, probes = _isolate(
        monkeypatch,
        lambda values: DataError("bad row") if poison & set(values) else None,
        keys,
    )
    assert result == {keys[2], keys[7]} and sorted(parked) == sorted(result)
    assert set(applied) == {uuid.UUID(k) for k in keys} - poison
    assert probes == ["t", "t"]  # an outage is ruled out before parking


@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("server closed the connection"),
        psycopg.errors.QueryCanceled("statement timeout"),
        CHOperationalError("connection refused"),
    ],
)
def test_transient_errors_are_never_parked(monkeypatch, error):
    with pytest.raises(type(error)):
        _isolate(monkeypatch, lambda values: error, [str(uuid.uuid4())] * 1)


def _clickhouse_refusal(code: int, name: str) -> DatabaseError:
    # What clickhouse-connect raises for a server exception over HTTP.
    return DatabaseError(
        f"Received ClickHouse exception, code: {code}, server response: "
        f"Code: {code}. DB::Exception: limit reached. ({name}) "
        "(for url http://clickhouse:8123)"
    )


@pytest.mark.parametrize(
    "error",
    [
        _clickhouse_refusal(241, "MEMORY_LIMIT_EXCEEDED"),
        _clickhouse_refusal(252, "TOO_MANY_PARTS"),
        _clickhouse_refusal(202, "TOO_MANY_SIMULTANEOUS_QUERIES"),
        _clickhouse_refusal(159, "TIMEOUT_EXCEEDED"),
    ],
)
def test_clickhouse_capacity_errors_fail_the_tick_without_bisecting(monkeypatch, error):
    # A server short of memory or behind on merges refuses the whole batch;
    # bisecting it would park healthy keys (and leave their CH rows stale).
    calls = []

    def fail(values):
        calls.append(values)
        return error

    with pytest.raises(DatabaseError):
        _isolate(monkeypatch, fail, [str(uuid.uuid4()) for _ in range(8)])
    assert len(calls) == 1


def test_structural_errors_fail_the_table_without_bisecting(monkeypatch):
    calls = []

    def fail(values):
        calls.append(values)
        return psycopg.errors.UndefinedColumn("column does not exist")

    with pytest.raises(cdc._TableFailed):
        _isolate(monkeypatch, fail, [str(uuid.uuid4()) for _ in range(8)])
    assert len(calls) == 1


def test_park_budget_turns_a_bad_table_into_a_table_failure(monkeypatch):
    keys = [str(uuid.uuid4()) for _ in range(5)]
    with pytest.raises(cdc._TableFailed) as failure:
        _isolate(monkeypatch, lambda values: DataError("all bad"), keys, budget=2)
    # The keys parked before the budget ran out are reported, not lost.
    assert failure.value.parked == set(keys[:2])


def test_malformed_outbox_key_is_parked_not_fatal(monkeypatch):
    result, applied, parked, _ = _isolate(
        monkeypatch, lambda values: None, ["not-a-uuid"]
    )
    assert result == {"not-a-uuid"} and parked == ["not-a-uuid"] and applied == []


def test_row_size_estimate_counts_wide_text_and_arrays():
    assert cdc._row_bytes(("x" * 1000, None, 5)) == 16 * 3 + 1000
    assert cdc._row_bytes((["ab", "c"], b"xyz")) == 16 * 2 + 3 + 3


# ---------------------------------------------------------------------------
# Schedules and activities
# ---------------------------------------------------------------------------


def _schedules_module(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("FI_CDC_MODE", raising=False)
    else:
        monkeypatch.setenv("FI_CDC_MODE", mode)
    import tfc.temporal.schedules.outbox_cdc as module

    return importlib.reload(module)


@pytest.mark.parametrize("mode", [None, "peerdb", "off", "bogus"])
def test_schedules_are_registered_only_in_outbox_mode(monkeypatch, mode):
    assert _schedules_module(monkeypatch, mode).OUTBOX_CDC_SCHEDULES == []


def test_outbox_mode_registers_a_10s_drain_and_a_reconcile(monkeypatch):
    module = _schedules_module(monkeypatch, "outbox")
    try:
        drain, reconcile = module.OUTBOX_CDC_SCHEDULES
        assert (
            drain.schedule_id,
            drain.activity_name,
            drain.interval_seconds,
            drain.queue,
        ) == (
            "outbox-cdc-drain",
            "drain_outbox_cdc",
            10,
            "tasks_s",
        )
        assert (reconcile.schedule_id, reconcile.activity_name, reconcile.queue) == (
            "outbox-cdc-reconcile",
            "reconcile_outbox_cdc",
            "tasks_l",
        )
        assert reconcile.interval_seconds < cdc.RECONCILE_EVERY_S
    finally:
        _schedules_module(monkeypatch, None)


@pytest.mark.parametrize("mode, registered", [("outbox", True), ("peerdb", False)])
def test_register_temporal_schedules_includes_outbox_only_in_outbox_mode(
    monkeypatch, mode, registered
):
    import tfc.temporal.schedules as package
    from tfc.temporal.common.registry import TEMPORAL_ACTIVITY_MODULES

    assert "tracer.tasks.outbox_cdc" in TEMPORAL_ACTIVITY_MODULES
    _schedules_module(monkeypatch, mode)
    try:
        ids = {config.schedule_id for config in importlib.reload(package).ALL_SCHEDULES}
        outbox = {"outbox-cdc-drain", "outbox-cdc-reconcile"}
        assert (outbox <= ids) is registered and (
            outbox & ids == set()
        ) is not registered
        assert "sweep-stranded-eval-tasks" in ids
    finally:
        _schedules_module(monkeypatch, None)
        importlib.reload(package)


def test_sync_registers_or_deletes_both_schedules(monkeypatch):
    import tfc.temporal.schedules.manager as manager
    import tfc.temporal.schedules.outbox_cdc as module

    calls = []

    async def register(client, schedules, cleanup_orphans=False):
        calls.append(("register", [s.schedule_id for s in schedules], cleanup_orphans))

    async def exists(client, schedule_id):
        return schedule_id == "outbox-cdc-drain"

    async def delete(client, schedule_id):
        calls.append(("delete", schedule_id))

    monkeypatch.setattr(manager, "a_register_schedules", register)
    monkeypatch.setattr(manager, "a_schedule_exists", exists)
    monkeypatch.setattr(manager, "a_delete_schedule", delete)
    asyncio.run(module.a_sync_outbox_cdc_schedules(object(), enabled=True))
    asyncio.run(module.a_sync_outbox_cdc_schedules(object(), enabled=False))
    assert calls == [
        ("register", ["outbox-cdc-drain", "outbox-cdc-reconcile"], False),
        ("delete", "outbox-cdc-drain"),
    ]


def test_sync_wrapper_uses_the_shared_client_from_sync_code(monkeypatch):
    """ensure_installed() calls the sync wrapper from the Standalone bootstrap."""
    import tfc.temporal.common.client as client_module
    import tfc.temporal.schedules.outbox_cdc as module

    shared, calls = object(), []

    async def get_client():
        return shared

    async def sync(client, *, enabled):
        calls.append((client, enabled))

    monkeypatch.setattr(client_module, "get_client", get_client)
    monkeypatch.setattr(module, "a_sync_outbox_cdc_schedules", sync)
    assert module.sync_outbox_cdc_schedules(enabled=True) is None
    module.sync_outbox_cdc_schedules(enabled=False)
    assert calls == [(shared, True), (shared, False)]


def test_activities_are_no_ops_outside_outbox_mode(monkeypatch):
    from tracer.tasks import outbox_cdc as tasks

    monkeypatch.setenv("FI_CDC_MODE", "peerdb")
    monkeypatch.setattr(tasks, "_connections", pytest.fail)
    assert tasks.drain_outbox_cdc._original_func() == {"disabled": True}
    assert tasks.reconcile_outbox_cdc._original_func() == {"disabled": True}
    meta = tasks.drain_outbox_cdc._metadata
    assert (meta["queue"], meta["max_retries"], meta["time_limit"]) == (
        "tasks_s",
        0,
        60,
    )


def _drain_result(**changes) -> cdc.DrainResult:
    """A quiet tick, with ``changes``."""
    result: cdc.DrainResult = {
        "stats": {},
        "parked": {},
        "outbox_depth": 0,
        "lag_seconds": 0.0,
        "rearmed": [],
        "rearm_failed": {},
        "errors": {},
        "drift": {},
        "drift_errors": {},
        "added_columns": [],
        "pending_snapshots": [],
    }
    return {**result, **changes}


def test_drain_activity_passes_the_source_and_reports_lag(monkeypatch):
    from contextlib import contextmanager

    from tracer.tasks import outbox_cdc as tasks

    monkeypatch.setenv("FI_CDC_MODE", "outbox")
    source = object()

    @contextmanager
    def connections():
        yield SimpleNamespace(source=source), "pg", "ch", ("t",)

    seen, logged = {}, []
    monkeypatch.setattr(tasks, "_connections", connections)
    monkeypatch.setattr(
        cdc,
        "drain",
        lambda pg, ch, **kwargs: (
            seen.update(kwargs)
            or _drain_result(
                lag_seconds=3600.0, outbox_depth=9, errors={"t": "t: broken"}
            )
        ),
    )
    monkeypatch.setattr(
        tasks.logger, "error", lambda event, **kw: logged.append((event, sorted(kw)))
    )
    result = tasks.drain_outbox_cdc._original_func()
    assert seen == {
        "tables": ("t",),
        "source": source,
        "budget_s": tasks.DRAIN_BUDGET_S,
    }
    assert result["outbox_depth"] == 9
    assert logged == [
        ("outbox_cdc_attention", ["activity", "errors"]),
        ("outbox_cdc_lagging", ["lag_seconds", "outbox_depth"]),
    ]


@pytest.mark.parametrize(
    "result, warned, errored",
    [
        (_drain_result(lag_seconds=5.0, outbox_depth=50_001), [50_001], []),
        (_drain_result(lag_seconds=5.0, outbox_depth=50_000), [], []),
        # A lagging drain is already an error; no second, weaker message.
        (
            _drain_result(lag_seconds=601.0, outbox_depth=90_000),
            [],
            ["outbox_cdc_lagging"],
        ),
        (_drain_result(), [], []),
        ({"skipped": "another writer holds the CDC lock"}, [], []),
    ],
)
def test_a_deep_outbox_that_keeps_up_is_a_backlog_warning(
    monkeypatch, result, warned, errored
):
    from tracer.tasks import outbox_cdc as tasks

    warnings, errors = [], []
    monkeypatch.setattr(
        tasks.logger,
        "warning",
        lambda event, **kw: warnings.append((event, kw)),
    )
    monkeypatch.setattr(tasks.logger, "error", lambda event, **kw: errors.append(event))

    tasks._report_drain(result)

    assert warnings == [
        ("outbox_cdc_backlog", {"outbox_depth": depth}) for depth in warned
    ]
    assert errors == errored


@pytest.mark.parametrize(
    "changes, fields",
    [
        ({"parked": {"t": 2}}, {"parked": {"t": 2}}),
        ({"rearmed": ["t"], "added_columns": ["t.c"]}, None),
        ({"rearm_failed": {"t": "busy"}, "drift_errors": {"t": "x"}}, None),
    ],
)
def test_parked_keys_and_repairs_raise_the_attention_alert(
    monkeypatch, changes, fields
):
    from tracer.tasks import outbox_cdc as tasks

    logged = []
    monkeypatch.setattr(
        tasks.logger, "error", lambda event, **kw: logged.append((event, kw))
    )
    tasks._report_drain(_drain_result(**changes))
    assert logged == [
        (
            "outbox_cdc_attention",
            {"activity": "outbox_cdc_drain", **(fields or changes)},
        )
    ]


def test_reconcile_errors_raise_the_attention_alert(monkeypatch):
    from tracer.tasks import outbox_cdc as tasks

    logged = []
    monkeypatch.setattr(
        tasks.logger, "error", lambda event, **kw: logged.append((event, kw))
    )
    result: cdc.ReconcileResult = {
        "stats": {"t.reconciled": 3},
        "swept": ["t"],
        "errors": {"u": "u: landing table missing in ClickHouse"},
        "requeued": 0,
    }
    tasks._report_reconcile(result)
    assert logged == [
        (
            "outbox_cdc_attention",
            {"activity": "outbox_cdc_reconcile", "errors": result["errors"]},
        )
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch):
    """main() with its config and connections doubled; returns what closed."""
    closed = []
    monkeypatch.setattr(
        cdc, "load_config", lambda env=None: SimpleNamespace(include_usage_schema=True)
    )
    monkeypatch.setattr(
        cdc,
        "connect",
        lambda config: (
            SimpleNamespace(close=lambda: closed.append("pg")),
            SimpleNamespace(close=lambda: closed.append("ch")),
        ),
    )
    return closed


@pytest.mark.parametrize(
    "command, action",
    [
        ("uninstall", "uninstall_capture"),
        ("resync", "resync"),
        ("requeue", "requeue_deadletter"),
    ],
)
def test_state_changing_commands_require_apply(
    cli, monkeypatch, capsys, command, action
):
    monkeypatch.setattr(cdc, action, pytest.fail)
    assert cdc.main([command]) == 1
    assert "--apply" in capsys.readouterr().err and cli == ["pg", "ch"]


def test_uninstall_with_apply_prints_the_removed_triggers(cli, monkeypatch, capsys):
    monkeypatch.setattr(
        cdc, "uninstall_capture", lambda pg: ["tracer_trace.fi_cdc_ins"]
    )
    assert cdc.main(["uninstall", "--apply"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "removed_triggers": ["tracer_trace.fi_cdc_ins"]
    }


def test_status_prints_json(cli, monkeypatch, capsys):
    state = {
        "installed": True,
        "capture_broken": {},
        "outbox_depth": 3,
        "lag_seconds": 1.5,
        "pending_snapshots": {"tracer_trace": ""},
        "reconcile_requested": [],
        "parked_keys": 0,
    }
    monkeypatch.setattr(cdc, "status", lambda pg, *, tables: state)
    assert cdc.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == state
    assert cli == ["pg", "ch"]


@pytest.mark.parametrize("ready, code", [(True, 0), (False, 1)])
def test_install_without_apply_is_a_check_whose_exit_code_is_readiness(
    cli, monkeypatch, capsys, ready, code
):
    applied = []

    def install(pg, ch, *, config, apply, takeover_peerdb, snapshot_budget_s):
        applied.append(apply)
        return {
            "installed": True,
            "ready": ready,
            "applied": False,
            "problems": [] if ready else ["keys parked in fi_cdc_deadletter: 1"],
        }

    monkeypatch.setattr(cdc, "install", install)
    assert cdc.main(["install"]) == code
    assert applied == [False]
    assert json.loads(capsys.readouterr().out)["ready"] is ready


def test_resync_refuses_tables_that_are_not_landing_tables(cli, monkeypatch, capsys):
    monkeypatch.setattr(cdc, "resync", pytest.fail)
    assert cdc.main(["resync", "auth_user", "--apply"]) == 1
    assert "not landing tables: ['auth_user']" in capsys.readouterr().err


def test_resync_defaults_to_every_landing_table(cli, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        cdc, "resync", lambda pg, *, tables: seen.append(tables) or list(tables)
    )
    assert cdc.main(["resync", "--apply"]) == 0
    assert seen == [core.landing_tables(include_usage_schema=True)]
    assert json.loads(capsys.readouterr().out)["pending_snapshots"] == list(seen[0])


def test_cli_hides_driver_messages(monkeypatch, capsys):
    def boom(env=None):
        raise RuntimeError("password=hunter2 row=secret")

    monkeypatch.setattr(cdc, "load_config", boom)
    assert cdc.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "hunter2" not in err and "RuntimeError" in err
