"""Service-free outbox CDC contracts. Postgres behaviour is covered by
test_oss_outbox_cdc_pg.py; neither suite is proof of ClickHouse behaviour."""

from __future__ import annotations

import asyncio
import importlib
import uuid
from collections import Counter
from contextlib import nullcontext
from types import SimpleNamespace

import psycopg
import pytest
from clickhouse_connect.driver.exceptions import DataError
from clickhouse_connect.driver.exceptions import OperationalError as CHOperationalError

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
    monkeypatch.setattr(
        cdc, "_ddl", lambda pg, statements, attempts: issued.append(statements)
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
        ]
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
    result = cdc._apply_isolated(None, None, SPEC, keys, None, Counter(), [budget])
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


def test_structural_errors_fail_the_table_without_bisecting(monkeypatch):
    calls = []

    def fail(values):
        calls.append(values)
        return psycopg.errors.UndefinedColumn("column does not exist")

    with pytest.raises(cdc._TableFailed):
        _isolate(monkeypatch, fail, [str(uuid.uuid4()) for _ in range(8)])
    assert len(calls) == 1


def test_park_budget_turns_a_bad_table_into_a_table_failure(monkeypatch):
    with pytest.raises(cdc._TableFailed):
        _isolate(
            monkeypatch,
            lambda values: DataError("all bad"),
            [str(uuid.uuid4()) for _ in range(5)],
            budget=2,
        )


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
            or {"lag_seconds": 3600.0, "outbox_depth": 9, "errors": {"t": "t: broken"}}
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["uninstall", "resync", "requeue"])
def test_state_changing_commands_require_apply(monkeypatch, capsys, command):
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
    assert cdc.main([command]) == 1
    assert "--apply" in capsys.readouterr().err and closed == ["pg", "ch"]


def test_cli_hides_driver_messages(monkeypatch, capsys):
    def boom(env=None):
        raise RuntimeError("password=hunter2 row=secret")

    monkeypatch.setattr(cdc, "load_config", boom)
    assert cdc.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "hunter2" not in err and "RuntimeError" in err
