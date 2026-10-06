"""The outbox CDC Temporal activities against a real PostgreSQL and ClickHouse.

Below the activity everything is the production path: ``_config()`` reads the
environment the backend container gets, ``_connections()`` opens the real
drivers (Postgres directly, ClickHouse over HTTP) and closes both, and drain
and reconcile write real ReplacingMergeTree landing tables. Capture and the
landing tables are set up with the installer's own building blocks: the full
``install`` also qualifies ClickHouse dependents that need the native schema,
which test_oss_outbox_cdc_pg.py covers against its ClickHouse double.

Skips without a Postgres that can create a scratch database or without the
test ClickHouse (see conftest's ``_ch_test_owned_database``).
"""

from __future__ import annotations

import os
import time
import uuid

import psycopg
import pytest

from conftest import (
    _ch_test_http_port,
    _ch_test_native_port,
    _ch_test_owned_database,
    _open_ch_test_http_client,
)
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_outbox_cdc as cdc
from tracer.services.clickhouse.tests import test_oss_outbox_cdc_pg as pg_suite
from tracer.tasks import outbox_cdc as tasks

pytestmark = pytest.mark.integration

# The Postgres side of the PG suite: a scratch database with the source tables.
scratch_db = pg_suite.scratch_db
pg = pg_suite.pg
_trace = pg_suite._trace

# Anything that could point the installer at another database or port.
_OVERRIDES = (
    "SRC_PG_HOST",
    "SRC_PG_PORT",
    "SRC_PG_DB",
    "SRC_PG_USER",
    "SRC_PG_PASSWORD",
    "DST_CH_HOST",
    "DST_CH_PORT",
    "DST_CH_DB",
    "DST_CH_USER",
    "DST_CH_PASSWORD",
    "CH25_HOST",
    "CH25_DATABASE",
    "CH25_TCP_PORT",
    "CH25_HTTP_PORT",
    "FI_CH_DATABASE",
    "FI_CH_URL",
)


@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_outbox_tasks_") as database:
        yield database


@pytest.fixture
def ch(ch_database):
    client = _open_ch_test_http_client(database=ch_database)
    for (name,) in client.query("SHOW TABLES").result_rows:
        client.command(f"DROP TABLE {name} SYNC")
    yield client
    client.close()


@pytest.fixture
def backend_env(monkeypatch, scratch_db, ch_database):
    """The backend container's settings, naming the scratch databases."""
    values = {
        "FI_CDC_MODE": "outbox",
        "PG_HOST": scratch_db.get("host") or "localhost",
        "PG_PORT": str(scratch_db.get("port") or 5432),
        "PG_DB": scratch_db["dbname"],
        "PG_USER": scratch_db.get("user") or "postgres",
        "PG_PASSWORD": scratch_db.get("password") or "",
        "CH_HOST": _ch_host(),
        "CH_PORT": str(_ch_test_native_port().port),
        "CH_HTTP_PORT": str(_ch_test_http_port().port),
        "CH_DATABASE": ch_database,
    }
    for name in _OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    tasks._config.cache_clear()
    yield values
    tasks._config.cache_clear()


def _ch_host() -> str:
    return os.environ.get("CH25_HOST", "127.0.0.1")


@pytest.fixture
def installed(pg, ch, backend_env):
    """Capture armed and every landing table created, as ``install`` leaves them."""
    config = tasks._config()
    tables = core.landing_tables(include_usage_schema=config.include_usage_schema)
    source = cdc.inspect_source(cdc._pg_query(pg), source=config.source, tables=tables)
    capture = cdc.install_capture(pg, tables)
    created, _ = cdc.ensure_landing_tables(
        ch, source, include_usage_schema=config.include_usage_schema, pg=pg
    )
    assert sorted(created) == sorted(tables) == sorted(capture["new_state"])
    return tables


def _live(ch, table: str) -> set:
    sql = cdc._ch_live_page_sql(table, after=False, uuid_key=True)
    return {row[0] for row in ch.query(sql, parameters={"limit": 1000}).result_rows}


def _tombstoned(ch, table: str, key) -> bool:
    # FINAL hides is_deleted rows, so read the newest version of the key.
    rows = ch.query(
        f"SELECT argMax(_peerdb_is_deleted, _peerdb_version) FROM {table} "
        "WHERE id = %(id)s",
        parameters={"id": key},
    ).result_rows
    return rows == [(1,)]


def _activity_sessions(pg) -> int:
    """Postgres sessions the activities' own connect() opened, once they end."""
    deadline = time.monotonic() + 5
    while True:
        (count,) = pg.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND application_name = 'fi_outbox_cdc'"
        ).fetchone()
        if not count or time.monotonic() > deadline:
            return count
        time.sleep(0.05)


def test_config_is_read_from_the_environment_once_per_process(backend_env, monkeypatch):
    config = tasks._config()

    assert (config.source.host, config.source.port, config.source.database) == (
        backend_env["PG_HOST"],
        int(backend_env["PG_PORT"]),
        backend_env["PG_DB"],
    )
    assert (config.destination.database, config.http_port) == (
        backend_env["CH_DATABASE"],
        int(backend_env["CH_HTTP_PORT"]),
    )
    # Parsed once: every 10 s tick reuses it instead of re-validating the DDL.
    monkeypatch.setenv("PG_DB", "somewhere_else")
    assert tasks._config() is config


def test_drain_activity_lands_captured_writes_in_clickhouse(pg, ch, installed):
    kept, deleted, updated = _trace(pg), _trace(pg), _trace(pg, name="before")
    pg.execute("DELETE FROM tracer_trace WHERE id = %s", (deleted,))
    pg.execute("UPDATE tracer_trace SET name = 'after' WHERE id = %s", (updated,))

    result = tasks.drain_outbox_cdc._original_func()

    assert result["errors"] == {} and result["outbox_depth"] == 0
    assert result["pending_snapshots"] == []
    assert _live(ch, "tracer_trace") == {kept, updated}
    assert _tombstoned(ch, "tracer_trace", deleted)
    [(name,)] = ch.query(
        "SELECT name FROM tracer_trace FINAL WHERE id = %(id)s",
        parameters={"id": updated},
    ).result_rows
    assert name == "after"
    # _connections() closed its Postgres session (and the tick left no lock).
    assert _activity_sessions(pg) == 0
    assert tasks.drain_outbox_cdc._original_func()["outbox_depth"] == 0


def test_drain_activity_backfills_a_new_column_into_existing_rows(pg, ch, installed):
    old = _trace(pg)
    tasks.drain_outbox_cdc._original_func()  # also completes the snapshots
    # Django's AddField: existing rows get the default, and no trigger fires.
    pg.execute(
        "ALTER TABLE tracer_trace ADD COLUMN sample_rate double precision "
        "NOT NULL DEFAULT 0.25"
    )
    pg.execute("ALTER TABLE tracer_trace ALTER COLUMN sample_rate DROP DEFAULT")
    new = _trace(pg, sample_rate=0.5)

    result = tasks.drain_outbox_cdc._original_func()

    assert result["errors"] == {} and result["pending_snapshots"] == []
    assert result["added_columns"] == ["tracer_trace.sample_rate"]
    # Without the re-copy the old row reads ClickHouse's Float64 default, 0.
    rows = ch.query("SELECT id, sample_rate FROM tracer_trace FINAL").result_rows
    assert dict(rows) == {old: 0.25, new: 0.5}


def test_reconcile_activity_repairs_writes_capture_never_saw(
    pg, ch, installed, scratch_db
):
    first = _trace(pg)
    tasks.drain_outbox_cdc._original_func()  # also completes the snapshots
    with psycopg.connect(**scratch_db, autocommit=True) as restore:
        with restore.transaction():
            # A restore or bulk load with triggers bypassed: no outbox row.
            restore.execute("SET LOCAL session_replication_role = replica")
            restore.execute("DELETE FROM tracer_trace WHERE id = %s", (first,))
            hidden = uuid.uuid4()
            restore.execute("INSERT INTO tracer_trace (id) VALUES (%s)", (hidden,))
    assert tasks.drain_outbox_cdc._original_func()["outbox_depth"] == 0
    assert _live(ch, "tracer_trace") == {first}

    result = tasks.reconcile_outbox_cdc._original_func()

    assert result["errors"] == {}
    assert sorted(result["swept"]) == sorted(installed)
    assert result["stats"]["tracer_trace.reconciled"] == 2
    assert _live(ch, "tracer_trace") == {hidden}
    assert _tombstoned(ch, "tracer_trace", first)
    assert _activity_sessions(pg) == 0
    # Swept tables are not due again until the daily sweep.
    assert tasks.reconcile_outbox_cdc._original_func()["swept"] == []
