"""Outbox CDC against a real PostgreSQL and a ClickHouse double.

Postgres is real: capture triggers, transition tables, advisory locks,
server-side cursors, lock_timeout and catalog reads all run on a server. The
suite creates a scratch database on the server named by
``FI_OUTBOX_CDC_TEST_PG_DSN`` or, failing that, the Django test settings'
server, and skips when neither can create one.

ClickHouse is a double, NOT proof of server behaviour: dev's ``MemoryClient``
supplies the metadata contract ``bootstrap_cdc`` qualifies against, plus
ReplacingMergeTree(version, is_deleted) row semantics and clickhouse-connect's
own offline Native serialization of every insert block (so insert typing is
checked with the real driver code, not the server). Its UUID key order is
deliberately different from Postgres' to prove reconcile never relies on the
two orders agreeing.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from clickhouse_connect.datatypes.registry import get_from_name
from clickhouse_connect.driver.exceptions import DatabaseError, DataError
from clickhouse_connect.driver.exceptions import OperationalError as CHOperationalError
from clickhouse_connect.driver.insert import InsertContext
from clickhouse_connect.driver.transform import NativeTransform

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse import oss_outbox_cdc as cdc
from tracer.services.clickhouse.oss_cdc_install import Config
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import (
    DATABASE,
    DEPENDENT_NAMES,
    LANDING_NAMES,
    MemoryClient,
)

pytestmark = pytest.mark.integration

DERIVED = upgrade.MIGRATIONS["cdc_002_usage_eval_fields.sql"]
TABLES = core.landing_tables(include_usage_schema=True)
_PG_TYPES = {
    "tracer_project_id": "uuid NULL",
    "target_speaks_first": "boolean NULL",
    "prompt_tokens": "integer NULL",
    "pass_rate": "numeric(5,2) NULL",
    "value_history": "jsonb NOT NULL DEFAULT '[]'",
    "config": "jsonb NOT NULL DEFAULT '{}'",
    "created_at": "timestamptz NOT NULL DEFAULT now()",
    "updated_at": "timestamptz NOT NULL DEFAULT now()",
    "deleted": "boolean NOT NULL DEFAULT false",
}


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def _admin_conninfo() -> str:
    dsn = os.environ.get("FI_OUTBOX_CDC_TEST_PG_DSN")
    if dsn:
        return dsn
    from django.conf import settings

    db = settings.DATABASES["default"]
    return psycopg.conninfo.make_conninfo(
        host=db["HOST"],
        port=db["PORT"],
        user=db["USER"],
        password=db["PASSWORD"],
        dbname=db["NAME"],
    )


@pytest.fixture(scope="module")
def scratch_db():
    try:
        admin = psycopg.connect(_admin_conninfo(), autocommit=True, connect_timeout=3)
    except Exception as error:  # no server in this lane
        pytest.skip(f"no PostgreSQL for outbox CDC tests ({type(error).__name__})")
    name = f"fi_outbox_cdc_{uuid.uuid4().hex[:10]}"
    try:
        admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as error:
        admin.close()
        pytest.skip(f"cannot create a scratch database ({type(error).__name__})")
    info = psycopg.conninfo.conninfo_to_dict(_admin_conninfo())
    info["dbname"] = name
    try:
        yield info
    finally:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _pg_ddl(name: str) -> str:
    required = [
        column
        for column in core._table(core.LANDING[name])[0]
        if not column.startswith("_peerdb_") and (name, column) not in DERIVED
    ]
    fields = []
    for column in dict.fromkeys(
        ["id", *required, "created_at", "updated_at", "deleted"]
    ):
        if column == "id":
            fields.append(
                "id bigint PRIMARY KEY"
                if name == "usage_apicalllog"
                else "id uuid PRIMARY KEY"
            )
        elif name == "simulate_agent_definition" and column == "languages":
            fields.append("languages varchar[] NULL")
        else:
            fields.append(
                f'"{column}" {_PG_TYPES.get(column, "text NOT NULL DEFAULT %s")}'
            )
    if name == "tracer_trace":
        fields.append("payload jsonb NULL")
    return f"CREATE TABLE {name} ({', '.join(fields)})".replace("%s", "''")


@pytest.fixture
def pg(scratch_db):
    conn = psycopg.connect(**scratch_db, autocommit=True)
    # Publications and slots live outside the schema; start clean.
    for (name,) in conn.execute("SELECT pubname FROM pg_publication").fetchall():
        conn.execute(f'DROP PUBLICATION "{name}"')
    conn.execute(
        "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
        "WHERE database = current_database()"
    )
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    for name in TABLES:
        conn.execute(_pg_ddl(name))
    yield conn
    conn.close()


@pytest.fixture
def other(scratch_db):
    """A second session, as an app writer."""
    conn = psycopg.connect(**scratch_db, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture
def config(scratch_db):
    return Config(
        source=core.PeerTarget(
            "pg_source",
            scratch_db.get("host") or "localhost",
            int(scratch_db.get("port") or 5432),
            scratch_db["dbname"],
        ),
        destination=core.PeerTarget("ch_dest", "clickhouse", 9000, DATABASE),
        http_port=8123,
        pg_user=scratch_db.get("user") or "postgres",
        ch_user="default",
        pg_password=scratch_db.get("password") or "",
        ch_password="",
        peerdb_url="http://peerdb-flow-api:8113",
        hosted=False,
        include_usage_schema=True,
    )


# ---------------------------------------------------------------------------
# ClickHouse double
# ---------------------------------------------------------------------------


def _ch_order(value):
    # Deliberately NOT Postgres' uuid order.
    return value.bytes[::-1] if isinstance(value, uuid.UUID) else value


class FakeClickHouse(MemoryClient):
    """MemoryClient metadata + landing rows + offline driver serialization."""

    def __init__(self):
        super().__init__()
        self.rows: dict[str, list[dict]] = {}
        self.insert_log: list[tuple[str, tuple[str, ...], int]] = []
        self.fail_insert = None  # callable(table, rows, column_names) -> None/raise
        self.commands: list[str] = []

    # -- metadata writes ---------------------------------------------------

    def _install_source_table(self, name: str, ddl: str) -> None:
        columns, indexes, clauses = core._table(ddl)
        self.columns[name] = {
            key: ("".join(t), k, " ".join(e)) for key, (t, k, e) in columns.items()
        }
        self.indexes[name] = indexes
        self.tables[name] = (
            "ReplacingMergeTree",
            "ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted) "
            "ORDER BY id SETTINGS index_granularity = 8192",
            "",
            "id",
            "id",
            ddl,
        )
        self.rows.setdefault(name, [])

    def command(self, sql):
        self.commands.append(sql)
        tokens = core._tokens(sql)
        if (
            tokens[:2] == ("CREATE", "TABLE")
            and tokens[5] in core.LANDING
            and "_peerdb_synced_at" in tokens
        ):
            if tokens[5] not in self.tables:
                self._install_source_table(tokens[5], sql)
            self.events.append(("CREATE", tokens[5]))
            return None
        added = re.fullmatch(
            r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS `(\w+)` (.+)", sql
        )
        if added:
            table, column, data_type = added.groups()
            self.columns[table].setdefault(column, (data_type, "", ""))
            self.events.append(("ADD", table, column))
            return None
        dropped = re.fullmatch(r"DROP TABLE IF EXISTS `(\w+)`", sql)
        if dropped:
            if dropped[1] in self.tables:
                self.remove(dropped[1])
            return None
        return super().command(sql)

    # -- reads ---------------------------------------------------------------

    def live(self, table) -> dict:
        latest = {}
        for row in self.rows.get(table, []):
            key = row["id"]
            if (
                key not in latest
                or row["_peerdb_version"] >= latest[key]["_peerdb_version"]
            ):
                latest[key] = row
        return {k: r for k, r in latest.items() if r["_peerdb_is_deleted"] == 0}

    def query(self, sql, parameters=None, settings=None):
        parameters = parameters or {}
        if sql == cdc._CH_COLUMNS_SQL:
            return SimpleNamespace(
                result_rows=[
                    (table, column, shape[0], shape[1])
                    for table in parameters["tables"]
                    for column, shape in self.columns.get(table, {}).items()
                ]
            )
        if sql == cdc._CH_EXISTING_SQL:
            return SimpleNamespace(
                result_rows=[(n,) for n in parameters["names"] if n in self.tables]
            )
        if sql == cdc._CH_TABLE_COLUMNS_SQL:
            return SimpleNamespace(
                result_rows=[(c,) for c in self.columns[parameters["table"]]]
            )
        if "startsWith(name, '_peerdb_raw_')" in sql:
            return SimpleNamespace(
                result_rows=[
                    (n,) for n in sorted(self.tables) if n.startswith("_peerdb_raw_")
                ]
            )
        match = re.fullmatch(r"SELECT max\(_peerdb_version\) FROM (\w+)", sql)
        if match:
            versions = [r["_peerdb_version"] for r in self.rows.get(match[1], [])]
            return SimpleNamespace(result_rows=[(max(versions) if versions else 0,)])
        match = re.fullmatch(
            r"SELECT 1 FROM (\w+) WHERE _peerdb_is_deleted = 0 LIMIT 1", sql
        )
        if match:
            return SimpleNamespace(
                result_rows=[(1,)]
                if any(
                    r["_peerdb_is_deleted"] == 0 for r in self.rows.get(match[1], [])
                )
                else []
            )
        match = re.fullmatch(r"SELECT 1 FROM (\w+) LIMIT 0", sql)
        if match:
            if match[1] not in self.tables:
                raise DatabaseError("UNKNOWN_TABLE")
            return SimpleNamespace(result_rows=[])
        match = re.fullmatch(
            r"SELECT id FROM (\w+) FINAL WHERE _peerdb_is_deleted = 0"
            r"( AND id > (?:toUUID\(%\(after\)s\)|%\(after\)s))? ORDER BY id LIMIT %\(limit\)s",
            sql,
        )
        if match:
            ids = sorted(self.live(match[1]), key=_ch_order)
            if match[2]:
                after = parameters["after"]
                after = uuid.UUID(after) if isinstance(after, str) else after
                ids = [i for i in ids if _ch_order(i) > _ch_order(after)]
            return SimpleNamespace(
                result_rows=[(i,) for i in ids[: parameters["limit"]]]
            )
        match = re.fullmatch(
            r"SELECT id FROM (\w+) FINAL WHERE id IN %\(ids\)s AND _peerdb_is_deleted = 0",
            sql,
        )
        if match:
            live = self.live(match[1])
            assert len(parameters["ids"]) <= cdc.RECONCILE_CHUNK
            return SimpleNamespace(
                result_rows=[(i,) for i in parameters["ids"] if i in live]
            )
        return super().query(sql, parameters=parameters or None, settings=settings)

    # -- inserts -------------------------------------------------------------

    def insert(self, table, rows, column_names, column_type_names=None):
        if table == "schema_versions":
            [(filename, sha, _who, _notes)] = rows
            self.versions[filename] = sha.encode("ascii")
            return None
        if table not in self.tables:
            raise DatabaseError("UNKNOWN_TABLE")
        names = list(column_names)
        declared = {c: shape[0] for c, shape in self.columns[table].items()}
        assert column_type_names == [declared[c] for c in names], "stale column types"
        assert all(self.columns[table][c][1] != "MATERIALIZED" for c in names)
        if self.fail_insert:
            self.fail_insert(table, rows, names)
        context = InsertContext(
            table, names, [get_from_name(t) for t in column_type_names], rows
        )
        b"".join(NativeTransform.build_insert(context))
        if context.insert_exception:
            raise context.insert_exception
        for row in rows:
            record = dict(zip(names, row, strict=True))
            record.setdefault("_peerdb_synced_at", "now64()")
            self.rows[table].append(record)
        self.insert_log.append((table, tuple(names), len(rows)))
        return None


@pytest.fixture
def ch():
    return FakeClickHouse()


def _install(pg, ch, config, **kwargs):
    kwargs.setdefault("snapshot_budget_s", 30)
    return cdc.install(pg, ch, config=config, apply=True, **kwargs)


def _drain(pg, ch, config, **kwargs):
    kwargs.setdefault("budget_s", 30)
    return cdc.drain(pg, ch, tables=TABLES, source=config.source, **kwargs)


def _pg_ids(pg, table):
    return {row[0] for row in pg.execute(f"SELECT id FROM {table}").fetchall()}


def _assert_parity(pg, ch, tables=TABLES):
    for table in tables:
        assert set(ch.live(table)) == _pg_ids(pg, table), table


def _trace(pg, **values):
    key = values.pop("id", uuid.uuid4())
    columns = ["id", *values]
    pg.execute(
        f"INSERT INTO tracer_trace ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        [key, *values.values()],
    )
    return key


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def test_fresh_install_creates_peerdb_shaped_tables_and_all_dependents(pg, ch, config):
    result = _install(pg, ch, config)

    assert result["ready"] and sorted(result["created_landing"]) == sorted(TABLES)
    assert sorted(result["created_dependents"]) == sorted(DEPENDENT_NAMES)
    assert set(LANDING_NAMES) <= set(ch.tables) and set(DEPENDENT_NAMES) <= set(
        ch.tables
    )
    # Derived usage columns came through the packaged cdc_002 migration and
    # its receipt, exactly as on the PeerDB path; cdc_001 is never recorded.
    assert set(ch.versions) == {"cdc_002_usage_eval_fields.sql"}
    assert [e for e in ch.events if e[0] == "ADD"] == [
        ("ADD", "usage_apicalllog", column) for _, column in DERIVED
    ]
    usage = ch.columns["usage_apicalllog"]
    assert usage["eval_score"][1] == "MATERIALIZED"
    assert usage["_peerdb_synced_at"] == ("DateTime64(9)", "DEFAULT", "now64 ( )")
    assert ch.tables["tracer_trace"][1].startswith(
        "ReplacingMergeTree(_peerdb_version, _peerdb_is_deleted)"
    )
    # Every trigger is armed; the independent read-only check agrees.
    assert cdc.broken_capture(cdc.capture_state(pg, TABLES)) == {}
    check = cdc.install(pg, ch, config=config, apply=False)
    assert check["ready"], check["problems"]


def test_repeat_install_issues_no_trigger_ddl_and_no_clickhouse_writes(pg, ch, config):
    _install(pg, ch, config)
    before = pg.execute(
        "SELECT t.oid, t.xmin::text FROM pg_trigger t WHERE tgname LIKE 'fi_cdc_%' ORDER BY 1"
    ).fetchall()
    functions = pg.execute(
        "SELECT oid, xmin::text FROM pg_proc WHERE proname LIKE 'fi_cdc_capture_%' ORDER BY 1"
    ).fetchall()
    writes = list(ch.writes)

    result = _install(pg, ch, config)

    assert result["armed_triggers"] == {} and result["created_landing"] == []
    assert result["created_dependents"] == [] and result["resynced"] == []
    assert ch.writes == writes
    assert (
        pg.execute(
            "SELECT t.oid, t.xmin::text FROM pg_trigger t WHERE tgname LIKE 'fi_cdc_%' ORDER BY 1"
        ).fetchall()
        == before
    )
    assert (
        pg.execute(
            "SELECT oid, xmin::text FROM pg_proc WHERE proname LIKE 'fi_cdc_capture_%' ORDER BY 1"
        ).fetchall()
        == functions
    )


def test_install_gives_up_on_a_busy_table_instead_of_queueing_writers(
    pg, ch, config, other, monkeypatch
):
    monkeypatch.setattr(cdc, "DDL_LOCK_TIMEOUT_MS", 200)
    monkeypatch.setattr(cdc, "DDL_ATTEMPTS", 2)
    monkeypatch.setattr(cdc.time, "sleep", lambda _: None)
    with other.transaction():
        # An app transaction that has written to the table and is still open.
        other.execute("LOCK TABLE tracer_trace IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        with pytest.raises(cdc.OutboxCDCError, match="long transaction"):
            cdc.install_capture(pg, TABLES, attempts=2)
        assert time.monotonic() - started < 5
        # Nothing is left waiting in the lock queue in front of app writers.
        assert not pg.execute(
            "SELECT 1 FROM pg_locks WHERE NOT granted AND relation = 'tracer_trace'::regclass"
        ).fetchall()
    # Tables that were free got their triggers; the busy one is armed next time.
    broken = cdc.broken_capture(cdc.capture_state(pg, TABLES))
    assert "tracer_trace" in broken and len(broken) < len(TABLES)
    cdc.install_capture(pg, TABLES)
    assert cdc.broken_capture(cdc.capture_state(pg, TABLES)) == {}


def test_hosted_or_running_peerdb_refuses_before_any_write(pg, ch, config):
    hosted = Config(**{**config.__dict__, "hosted": True})
    with pytest.raises(cdc.OutboxCDCError, match="single-node"):
        cdc.install(pg, ch, config=hosted, apply=True)
    ch.install("_peerdb_raw_fi_cdc", core._LEDGER_DDL)
    pg.execute("CREATE PUBLICATION peerflow_pub_fi_cdc FOR TABLE tracer_trace")
    with pytest.raises(cdc.OutboxCDCError, match="--takeover-peerdb"):
        _install(pg, ch, config)
    assert not cdc._outbox_exists(pg) and "tracer_trace" not in ch.tables
    check = cdc.install(pg, ch, config=config, apply=False)
    assert (
        not check["ready"] and "PeerDB replication objects exist" in check["problems"]
    )


def test_takeover_drops_inactive_peerdb_objects_and_resyncs_with_higher_versions(
    pg, ch, config
):
    # A PeerDB-era install: landing tables already hold rows with PeerDB versions.
    _install(pg, ch, config)
    kept, stale = _trace(pg, name="kept"), uuid.uuid4()
    _drain(pg, ch, config)
    cdc.uninstall_capture(pg)
    peerdb_version = 9 * 10**18
    ch.rows["tracer_trace"].append(
        {"id": stale, "_peerdb_is_deleted": 0, "_peerdb_version": peerdb_version}
    )
    pg.execute("UPDATE tracer_trace SET name = 'changed after PeerDB stopped'")
    ch.install("_peerdb_raw_fi_cdc", core._LEDGER_DDL)
    pg.execute("CREATE PUBLICATION peerflow_pub_fi_cdc FOR TABLE tracer_trace")
    slot = pg.execute("SHOW wal_level").fetchone()[0] == "logical"
    if slot:
        pg.execute(
            "SELECT pg_create_logical_replication_slot('peerflow_slot_fi_cdc', 'pgoutput')"
        )

    result = _install(pg, ch, config, takeover_peerdb=True)

    assert result["peerdb_takeover"]["publications"] == ["peerflow_pub_fi_cdc"]
    assert "_peerdb_raw_fi_cdc" not in ch.tables
    assert not pg.execute("SELECT 1 FROM pg_publication").fetchall()
    if slot:
        assert not pg.execute("SELECT 1 FROM pg_replication_slots").fetchall()
    _drain(pg, ch, config)
    cdc.reconcile(pg, ch, tables=TABLES)
    live = ch.live("tracer_trace")
    assert set(live) == {kept}  # the id PeerDB never deleted is tombstoned
    assert live[kept]["name"] == "changed after PeerDB stopped"
    # Every post-takeover write outranks the PeerDB-era versions.
    [tombstone] = [r for r in ch.rows["tracer_trace"] if r["_peerdb_is_deleted"]]
    assert tombstone["id"] == stale and tombstone["_peerdb_synced_at"] == cdc.EPOCH
    assert (
        min(live[kept]["_peerdb_version"], tombstone["_peerdb_version"])
        > peerdb_version
    )


def test_takeover_refuses_while_peerdb_slot_is_active(pg, ch, config, monkeypatch):
    monkeypatch.setattr(
        cdc,
        "peerdb_objects",
        lambda pg, ch: {
            "slots": ["peerflow_slot_fi_cdc"],
            "active_slots": ["peerflow_slot_fi_cdc"],
            "publications": [],
            "raw_tables": [],
        },
    )
    with pytest.raises(cdc.OutboxCDCError, match="running PeerDB"):
        _install(pg, ch, config, takeover_peerdb=True)
    assert cdc.broken_capture(cdc.capture_state(pg, ("tracer_trace",)))


# ---------------------------------------------------------------------------
# Capture and drain
# ---------------------------------------------------------------------------


def test_every_write_path_is_captured_and_mirrored(pg, ch, config, other):
    _install(pg, ch, config)
    a, b = _trace(pg, name="a"), _trace(pg, name="b", payload='{"b": 1, "a": [1, 2]}')
    pg.execute("UPDATE tracer_trace SET name = name || '!'")  # bulk, no updated_at bump
    pg.execute(
        "INSERT INTO tracer_trace (id, name) SELECT gen_random_uuid(), 'raw' "
        "FROM generate_series(1, 3)"
    )
    pg.execute("DELETE FROM tracer_trace WHERE id = %s", (b,))
    with pytest.raises(RuntimeError):
        with other.transaction():
            other.execute("INSERT INTO tracer_trace (id) VALUES (%s)", (uuid.uuid4(),))
            raise RuntimeError("rolled back")
    usage = [
        [1, '{"output": {"output": {"score": 0.5}}}'],
        [2, "{}"],
    ]
    pg.cursor().executemany(
        "INSERT INTO usage_apicalllog (id, config) VALUES (%s, %s)", usage
    )
    pg.execute(
        "INSERT INTO simulate_agent_definition (id, languages, target_speaks_first) "
        "VALUES (%s, NULL, NULL), (%s, ARRAY['en', NULL]::varchar[], true)",
        (uuid.uuid4(), uuid.uuid4()),
    )
    pg.execute(
        "INSERT INTO model_hub_score (id, tracer_project_id, value_history) "
        "VALUES (%s, %s, '[1]')",
        (uuid.uuid4(), uuid.uuid4()),
    )

    result = _drain(pg, ch, config)

    assert result["outbox_depth"] == 0 and result["errors"] == {}
    _assert_parity(pg, ch)
    live = ch.live("tracer_trace")
    assert live[a]["name"] == "a!"
    # jsonb travels as PG's own text form, as PeerDB's text transport carries it.
    assert all(r["payload"] in (None, '{"a": [1, 2], "b": 1}') for r in live.values())
    assert ch.live("usage_apicalllog")[1]["config"].startswith('{"output"')
    languages = sorted(
        r["languages"] for r in ch.live("simulate_agent_definition").values()
    )
    assert languages == [[], ["en", ""]]
    # Drain deltas land "now", like PeerDB's; the tombstone carries no row data.
    assert all(r["_peerdb_synced_at"] == "now64()" for r in live.values())
    tombstone = [r for r in ch.rows["tracer_trace"] if r["id"] == b][-1]
    assert tombstone["_peerdb_is_deleted"] == 1 and set(tombstone) == {
        "id",
        "_peerdb_is_deleted",
        "_peerdb_version",
        "_peerdb_synced_at",
    }


def test_idle_tick_writes_nothing(pg, ch, config):
    _install(pg, ch, config)
    _drain(pg, ch, config)
    # Any UPDATE, even to the same value, writes a new tuple version.
    versions = "SELECT table_name, xmin::text, ctid::text FROM fi_cdc_state ORDER BY 1"
    before, log = pg.execute(versions).fetchall(), list(ch.insert_log)
    result = _drain(pg, ch, config)
    assert result["outbox_depth"] == 0 and ch.insert_log == log
    assert pg.execute(versions).fetchall() == before


def test_versions_strictly_increase_across_writers_and_a_backwards_clock(
    pg, ch, config, monkeypatch
):
    _install(pg, ch, config)
    key = _trace(pg, name="v1")
    _drain(pg, ch, config)
    first = ch.live("tracer_trace")[key]["_peerdb_version"]
    monkeypatch.setattr(cdc.time, "time_ns", lambda: 1)  # clock jumps back
    pg.execute("UPDATE tracer_trace SET name = 'v2'")
    _drain(pg, ch, config)
    pg.execute("UPDATE tracer_trace SET name = 'v3'")
    cdc.reconcile(pg, ch, tables=("tracer_trace",), full=True)
    _drain(pg, ch, config)
    versions = [r["_peerdb_version"] for r in ch.rows["tracer_trace"]]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert ch.live("tracer_trace")[key]["name"] == "v3"
    assert ch.live("tracer_trace")[key]["_peerdb_version"] > first


def test_drain_flushes_wide_rows_in_bounded_blocks(pg, ch, config, monkeypatch):
    _install(pg, ch, config)
    monkeypatch.setattr(cdc, "FLUSH_BYTES", 64 * 1024)
    monkeypatch.setattr(cdc, "FETCH_ROWS", 10)
    pg.execute(
        "INSERT INTO tracer_trace (id, name) SELECT gen_random_uuid(), repeat('x', 10000) "
        "FROM generate_series(1, 100)"
    )
    log = len(ch.insert_log)
    _drain(pg, ch, config)
    blocks = [n for t, _, n in ch.insert_log[log:] if t == "tracer_trace"]
    assert sum(blocks) == 100 and len(blocks) >= 10 and max(blocks) <= 10
    _assert_parity(pg, ch, ("tracer_trace",))


def test_truncate_is_tombstoned_by_the_requested_reconcile(pg, ch, config):
    _install(pg, ch, config)
    for _ in range(3):
        _trace(pg)
    _drain(pg, ch, config)
    cdc.reconcile(pg, ch, tables=TABLES)  # settle the post-snapshot sweep
    pg.execute("TRUNCATE tracer_trace")
    kept = _trace(pg, name="after truncate")
    _drain(pg, ch, config)
    assert cdc.status(pg, tables=TABLES)["reconcile_requested"] == ["tracer_trace"]
    result = cdc.reconcile(pg, ch, tables=TABLES)
    assert result["swept"] == ["tracer_trace"]
    assert set(ch.live("tracer_trace")) == {kept}
    assert cdc.status(pg, tables=TABLES)["reconcile_requested"] == []


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_existing_rows_snapshot_resumably_without_looking_like_new_arrivals(
    pg, ch, config, monkeypatch
):
    old = dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    pg.execute(
        "INSERT INTO tracer_trace (id, name, updated_at) "
        "SELECT gen_random_uuid(), 'old', %s FROM generate_series(1, 25)",
        (old,),
    )
    monkeypatch.setattr(cdc, "SNAPSHOT_PAGE", 10)
    result = _install(pg, ch, config, snapshot_budget_s=0)
    assert "tracer_trace" in result["pending_snapshots"] and not ch.live("tracer_trace")
    racing = _trace(pg, name="written during the snapshot")

    tick = _drain(pg, ch, config)

    assert tick["tracer_trace.snapshot"] == 25 + 1
    _assert_parity(pg, ch, ("tracer_trace",))
    live = ch.live("tracer_trace")
    olds = [r for k, r in live.items() if k != racing]
    # Snapshot rows carry their PG change time, never "now".
    assert {r["_peerdb_synced_at"] for r in olds} == {old}
    assert cdc.status(pg, tables=TABLES)["pending_snapshots"] == {}
    assert "tracer_trace" in cdc.status(pg, tables=TABLES)["reconcile_requested"]


def test_emptied_or_recreated_clickhouse_table_is_resnapshotted_on_next_install(
    pg, ch, config
):
    _install(pg, ch, config)
    keys = [_trace(pg) for _ in range(3)]
    _drain(pg, ch, config)
    ch.rows["tracer_trace"].clear()  # volume reset / truncated in CH only
    ch.remove("model_hub_score")  # dropped (ch25_remove_pg style)
    pg.execute("INSERT INTO model_hub_score (id) VALUES (%s)", (uuid.uuid4(),))
    ch.rows.pop("model_hub_score")

    # The PG-side capture state survived, so this is not a first install.
    result = _install(pg, ch, config)

    assert result["created_landing"] == ["model_hub_score"]
    assert result["resynced"] == ["model_hub_score", "tracer_trace"]
    _drain(pg, ch, config)
    assert set(ch.live("tracer_trace")) == set(keys)
    _assert_parity(pg, ch, ("tracer_trace", "model_hub_score"))


# ---------------------------------------------------------------------------
# Self-healing, isolation and parking
# ---------------------------------------------------------------------------


def test_disabled_trigger_is_rearmed_resnapshotted_and_reconciled(pg, ch, config):
    _install(pg, ch, config)
    gone, kept = _trace(pg), _trace(pg)
    _drain(pg, ch, config)
    cdc.reconcile(pg, ch, tables=TABLES)
    pg.execute("ALTER TABLE tracer_trace DISABLE TRIGGER fi_cdc_upd")
    pg.execute("ALTER TABLE tracer_trace DISABLE TRIGGER fi_cdc_del")
    pg.execute("UPDATE tracer_trace SET name = 'unseen'")
    pg.execute("DELETE FROM tracer_trace WHERE id = %s", (gone,))

    result = _drain(pg, ch, config)

    assert result["rearmed"] == ["tracer_trace"]
    assert cdc.broken_capture(cdc.capture_state(pg, TABLES)) == {}
    assert ch.live("tracer_trace")[kept]["name"] == "unseen"
    cdc.reconcile(pg, ch, tables=TABLES)
    assert set(ch.live("tracer_trace")) == {kept}


def test_resync_recopies_content_that_capture_never_saw(pg, ch, config, other):
    _install(pg, ch, config)
    key = _trace(pg, name="before")
    _drain(pg, ch, config)
    with other.transaction():
        other.execute("SET LOCAL session_replication_role = replica")
        other.execute("UPDATE tracer_trace SET name = 'after'")
    _drain(pg, ch, config)
    cdc.reconcile(pg, ch, tables=TABLES, full=True)
    assert ch.live("tracer_trace")[key]["name"] == "before"  # id parity only

    assert cdc.resync(pg, tables=["tracer_trace"]) == ["tracer_trace"]
    _drain(pg, ch, config)

    assert ch.live("tracer_trace")[key]["name"] == "after"


def test_replica_role_writes_are_repaired_by_the_daily_sweep(pg, ch, config, other):
    _install(pg, ch, config)
    keys = [_trace(pg) for _ in range(7)]
    _drain(pg, ch, config)
    cdc.reconcile(pg, ch, tables=TABLES)
    with other.transaction():
        other.execute("SET LOCAL session_replication_role = replica")
        other.execute("DELETE FROM tracer_trace WHERE id = ANY(%s)", (keys[:3],))
        added = uuid.uuid4()
        other.execute("INSERT INTO tracer_trace (id) VALUES (%s)", (added,))
    assert _drain(pg, ch, config)["outbox_depth"] == 0  # nothing was captured
    assert cdc.reconcile(pg, ch, tables=TABLES)["swept"] == []  # not due yet

    result = cdc.reconcile(pg, ch, tables=TABLES, max_age_s=0)

    assert result["tracer_trace.reconciled"] == 4
    assert set(ch.live("tracer_trace")) == set(keys[3:]) | {added}


def test_reconcile_pages_and_locks_per_chunk_while_the_drain_keeps_running(
    pg, ch, config, monkeypatch
):
    _install(pg, ch, config)
    keys = [_trace(pg) for _ in range(23)]
    _drain(pg, ch, config)
    ch.rows["tracer_trace"] = [
        r for r in ch.rows["tracer_trace"] if r["id"] not in keys[:9]
    ]
    ch.rows["tracer_trace"] += [
        {"id": uuid.uuid4(), "_peerdb_is_deleted": 0, "_peerdb_version": 1}
        for _ in range(6)
    ]
    monkeypatch.setattr(cdc, "RECONCILE_CHUNK", 4)
    locks = []
    original = cdc.advisory_lock

    def counting(pg, *, wait):
        locks.append(wait)
        return original(pg, wait=wait)

    monkeypatch.setattr(cdc, "advisory_lock", counting)
    specs, _ = cdc.load_specs(pg, ch, ("tracer_trace",))
    stats = cdc.Counter()
    cdc.reconcile_table(pg, ch, specs["tracer_trace"], stats, chunk=4)
    assert stats["tracer_trace.reconciled"] == 15
    assert len(locks) > 2  # one short hold per repaired page, not one for the sweep
    _assert_parity(pg, ch, ("tracer_trace",))


def test_one_broken_landing_table_does_not_stop_the_others(pg, ch, config):
    _install(pg, ch, config)
    ch.remove("model_hub_score")
    score = uuid.uuid4()
    pg.execute("INSERT INTO model_hub_score (id) VALUES (%s)", (score,))
    trace = _trace(pg)

    result = _drain(pg, ch, config)

    assert "model_hub_score" in result["errors"]
    assert set(ch.live("tracer_trace")) == {trace}
    assert result["outbox_depth"] == 1  # the score change waits in the outbox
    assert pg.execute("SELECT table_name FROM fi_cdc_outbox").fetchall() == [
        ("model_hub_score",)
    ]


def test_poison_row_is_bisected_parked_and_later_retried(pg, ch, config, monkeypatch):
    _install(pg, ch, config)

    def reject(table, rows, names):
        if table == "tracer_trace" and "name" in names:
            if any(row[names.index("name")] == "poison" for row in rows):
                raise DataError("Cannot parse input")

    ch.fail_insert = reject
    good = [_trace(pg, name=f"ok{i}") for i in range(9)]
    bad = _trace(pg, name="poison")

    result = _drain(pg, ch, config)

    assert result["tracer_trace.parked"] == 1 and result["errors"] == {}
    assert result["outbox_depth"] == 0
    assert set(ch.live("tracer_trace")) == set(good)
    assert pg.execute(
        "SELECT table_name, pk, error FROM fi_cdc_deadletter"
    ).fetchall() == [("tracer_trace", str(bad), "DataError")]
    ch.fail_insert = None
    assert cdc.requeue_deadletter(pg) == 1
    assert cdc.requeue_deadletter(pg) == 0  # at most hourly
    _drain(pg, ch, config)
    assert bad in ch.live("tracer_trace")
    assert not pg.execute("SELECT 1 FROM fi_cdc_deadletter").fetchall()


def test_transient_clickhouse_failure_keeps_every_outbox_row(pg, ch, config):
    _install(pg, ch, config)
    _trace(pg)

    def down(table, rows, names):
        raise CHOperationalError("connection refused")

    ch.fail_insert = down
    with pytest.raises(CHOperationalError):
        _drain(pg, ch, config)
    assert cdc.status(pg, tables=TABLES)["outbox_depth"] == 1
    assert not pg.execute("SELECT 1 FROM fi_cdc_deadletter").fetchall()
    ch.fail_insert = None
    assert _drain(pg, ch, config)["outbox_depth"] == 0


def test_park_budget_fails_the_table_instead_of_parking_everything(
    pg, ch, config, monkeypatch
):
    _install(pg, ch, config)
    monkeypatch.setattr(cdc, "MAX_PARKED_PER_TICK", 2)

    def reject(table, rows, names):
        if table == "tracer_trace" and "name" in names:
            raise DataError("every row is bad")

    ch.fail_insert = reject
    for _ in range(5):
        _trace(pg, name="x")
    trace_free = pg.execute(
        "INSERT INTO model_hub_score (id) VALUES (%s) RETURNING id", (uuid.uuid4(),)
    ).fetchone()[0]

    result = _drain(pg, ch, config)

    assert result["tracer_trace.parked"] == 2
    assert "tracer_trace" in result["errors"]
    assert trace_free in ch.live("model_hub_score")
    assert result["outbox_depth"] == 5  # kept, not dropped


def test_new_pg_column_is_added_in_clickhouse_at_runtime(pg, ch, config):
    _install(pg, ch, config)
    pg.execute("ALTER TABLE tracer_trace ADD COLUMN sample_rate double precision NULL")
    key = _trace(pg, sample_rate=0.25)

    result = _drain(pg, ch, config)

    assert result["added_columns"] == ["tracer_trace.sample_rate"]
    assert ch.columns["tracer_trace"]["sample_rate"][0] == "Nullable(Float64)"
    assert ch.live("tracer_trace")[key]["sample_rate"] == 0.25
    assert _drain(pg, ch, config)["drift"] == {}
    assert cdc.install(pg, ch, config=config, apply=False)["ready"]


def test_unmappable_new_column_is_reported_and_the_rest_keeps_flowing(pg, ch, config):
    _install(pg, ch, config)
    pg.execute("ALTER TABLE tracer_trace ADD COLUMN geo point NULL")
    key = _trace(pg, name="still mirrored")
    result = _drain(pg, ch, config)
    assert result["drift"] == {"tracer_trace": ["geo"]} and result["drift_errors"]
    assert ch.live("tracer_trace")[key]["name"] == "still mirrored"


# ---------------------------------------------------------------------------
# Liveness checks, locking, uninstall and ensure_installed
# ---------------------------------------------------------------------------


def test_check_fails_when_capture_runs_without_a_live_drain(pg, ch, config):
    _install(pg, ch, config)
    _trace(pg)
    pg.execute("UPDATE fi_cdc_outbox SET captured_at = now() - interval '1 hour'")
    check = cdc.install(pg, ch, config=config, apply=False)
    assert not check["ready"]
    assert any("outbox-cdc-drain" in p for p in check["problems"])
    _drain(pg, ch, config)
    assert cdc.install(pg, ch, config=config, apply=False)["ready"]


def test_check_runs_the_full_clickhouse_qualification(pg, ch, config):
    _install(pg, ch, config)
    ch.remove("simulate_calls")
    check = cdc.install(pg, ch, config=config, apply=False)
    assert not check["ready"]
    assert any(p.startswith("ClickHouse landing schema") for p in check["problems"])


def test_a_second_drain_skips_while_the_lock_is_held(pg, ch, config, other):
    _install(pg, ch, config)
    other.execute("SELECT pg_advisory_lock(%s)", (cdc.LOCK_KEY,))
    try:
        assert _drain(pg, ch, config) == {
            "skipped": "another writer holds the CDC lock"
        }
    finally:
        other.execute("SELECT pg_advisory_unlock(%s)", (cdc.LOCK_KEY,))


def test_reconcile_waits_for_the_drain_lock_instead_of_racing_it(pg, ch, config, other):
    _install(pg, ch, config)
    keys = [_trace(pg) for _ in range(3)]
    _drain(pg, ch, config)
    ch.rows["tracer_trace"] = [r for r in ch.rows["tracer_trace"] if r["id"] != keys[0]]
    other.execute("SELECT pg_advisory_lock(%s)", (cdc.LOCK_KEY,))
    timer = threading.Timer(
        0.5, lambda: other.execute("SELECT pg_advisory_unlock(%s)", (cdc.LOCK_KEY,))
    )
    timer.start()
    started = time.monotonic()
    cdc.reconcile(pg, ch, tables=("tracer_trace",), full=True)
    timer.join()
    assert time.monotonic() - started >= 0.4
    assert keys[0] in ch.live("tracer_trace")


def test_uninstall_drops_triggers_by_name_everywhere_then_the_outbox(pg, ch, config):
    _install(pg, ch, config)
    # A capture trigger on a table outside today's landing set (for example a
    # usage table deselected since install) must go too, or its writes fail.
    pg.execute("CREATE TABLE extra (id uuid PRIMARY KEY)")
    pg.execute(cdc.create_trigger_sql("extra", "fi_cdc_ins"))

    removed = cdc.uninstall_capture(pg)

    assert "extra.fi_cdc_ins" in removed and len(removed) == 4 * len(TABLES) + 1
    assert not cdc.capture_installed(pg)
    pg.execute("INSERT INTO extra (id) VALUES (%s)", (uuid.uuid4(),))
    _trace(pg)
    assert cdc.uninstall_capture(pg) == []


def test_ensure_installed_follows_the_mode_and_syncs_schedules(
    pg, ch, config, scratch_db, monkeypatch
):
    synced = []
    monkeypatch.setattr(cdc, "load_config", lambda env=None: config)
    monkeypatch.setattr(
        cdc,
        "connect",
        lambda config: (psycopg.connect(**scratch_db, autocommit=True), ch),
    )
    monkeypatch.setattr(ch, "close", lambda: None, raising=False)
    import tfc.temporal.schedules.outbox_cdc as schedules

    monkeypatch.setattr(
        schedules,
        "sync_outbox_cdc_schedules",
        lambda *, enabled: synced.append(enabled),
    )

    result = cdc.ensure_installed({"FI_CDC_MODE": "outbox"}, snapshot_budget_s=0)
    assert result["mode"] == "outbox" and result["ready"]
    assert cdc.broken_capture(cdc.capture_state(pg, TABLES)) == {}

    result = cdc.ensure_installed({"FI_CDC_MODE": "off"})
    assert len(result["removed_triggers"]) == 4 * len(TABLES)
    assert not cdc.capture_installed(pg) and synced == [True, False]
    assert cdc.ensure_installed({"FI_CDC_MODE": "peerdb"})["removed_triggers"] == []
    with pytest.raises(cdc.OutboxCDCError, match="FI_CDC_MODE"):
        cdc.ensure_installed({"FI_CDC_MODE": "outbax"})


def test_decimal_int_bool_and_timestamp_types_serialize_with_the_real_driver(
    pg, ch, config
):
    _install(pg, ch, config)
    assert (
        ch.columns["simulate_agent_version"]["pass_rate"][0] == "Nullable(Decimal(5,2))"
    )
    assert ch.columns["model_hub_cell"]["prompt_tokens"][0] == "Nullable(Int32)"
    version, cell, usage = uuid.uuid4(), uuid.uuid4(), 2**40
    pg.execute(
        "INSERT INTO simulate_agent_version (id, pass_rate) VALUES (%s, %s)",
        (version, Decimal("12.34")),
    )
    pg.execute("INSERT INTO model_hub_cell (id, prompt_tokens) VALUES (%s, 7)", (cell,))
    pg.execute("INSERT INTO usage_apicalllog (id) VALUES (%s)", (usage,))

    _drain(pg, ch, config)

    row = ch.live("simulate_agent_version")[version]
    assert row["pass_rate"] == Decimal("12.34") and row["deleted"] is False
    assert isinstance(row["updated_at"], dt.datetime) and row["updated_at"].tzinfo
    assert ch.live("model_hub_cell")[cell]["prompt_tokens"] == 7
    assert usage in ch.live("usage_apicalllog")  # bigint key, Int64 in CH
