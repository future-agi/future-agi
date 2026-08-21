"""Tests for the harness-store manager: a real Postgres server, not a container per test.

Everything here talks to whatever HARNESS_STORE_DSN names. Skipped wholesale when that is
unset, because standing up a long-lived Postgres server is not something a laptop run should
require -- CI provides it (see .github/workflows/harness-ci.yml) and that is where this lane
actually runs. The one exception is the save_to test at the bottom: it never opens a
connection, so it always runs.
"""

from __future__ import annotations

import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import pytest

psycopg = pytest.importorskip("psycopg")

from harness.world.stores import (  # noqa: E402
    QuotaExceeded,
    StoreManager,
    StoreManagerError,
)
from harness.world.stores.manager import (  # noqa: E402
    COPY_PREFIX,
    MASTER_PREFIX,
    TEMP_PREFIX,
)

SCRIPTS = ["CREATE TABLE t (id serial primary key, v text)", "INSERT INTO t (v) VALUES ('seed')"]
SNAPSHOT = {"rows": {"t": [{"id": 1, "v": "seed"}, {"id": 2, "v": "second"}]}, "counters": {"t_id_seq": 2}}


def _unique(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(6)}"


def _all_managed(mgr: StoreManager) -> set[str]:
    """Every hm_/hc_/ht_ name that exists right now, for before/after diffing in cleanup."""
    with mgr._admin() as conn:
        return (
            set(mgr._list(conn, MASTER_PREFIX))
            | set(mgr._list(conn, COPY_PREFIX))
            | set(mgr._list(conn, TEMP_PREFIX))
        )


@pytest.fixture
def manager():
    mgr = StoreManager.from_env()
    before = _all_managed(mgr)
    yield mgr
    for name in _all_managed(mgr) - before:
        try:
            mgr.drop(name)
        except Exception:
            pass


@pytest.fixture
def master(manager):
    # materialize now grants to harness_app as part of building the master (see _load), so
    # the role has to exist first -- in production bootstrap() runs once at startup, before
    # anything else.
    manager.bootstrap()
    name = _unique(MASTER_PREFIX)
    manager.materialize(name, SCRIPTS, SNAPSHOT)
    return name


class TestStoreManagerLive:
    pytestmark = pytest.mark.skipif(
        not os.environ.get("HARNESS_STORE_DSN"),
        reason="harness-store not running (HARNESS_STORE_DSN unset)",
    )

    def test_from_env_requires_dsn(self, monkeypatch):
        monkeypatch.delenv("HARNESS_STORE_DSN", raising=False)
        with pytest.raises(StoreManagerError, match="HARNESS_STORE_DSN"):
            StoreManager.from_env()

    def test_dsn_redacted_on_connect_failure(self, manager):
        parts = urlsplit(manager.dsn)
        bad = urlunsplit(
            parts._replace(netloc=f"{parts.username}:wrong-password@{parts.hostname}:{parts.port}")
        )
        with pytest.raises(StoreManagerError) as exc_info:
            StoreManager(bad).masters()
        message = str(exc_info.value)
        assert parts.hostname in message
        assert "wrong-password" not in message
        # The chained psycopg error is what a traceback actually prints; a leak that only
        # avoided the top-level message would still show the password on every failure.
        assert "wrong-password" not in str(exc_info.value.__cause__)

    def test_bootstrap_idempotent(self, manager):
        manager.bootstrap()
        manager.bootstrap()
        with manager._admin() as conn:
            row = conn.execute(
                "SELECT rolconnlimit FROM pg_roles WHERE rolname = 'harness_app'"
            ).fetchone()
        assert row[0] == 120

    def test_statement_timeout_kills_runaway(self, manager):
        fast = StoreManager(manager.dsn, statement_timeout="1s")
        fast.bootstrap()
        try:
            name = _unique(MASTER_PREFIX)
            fast.materialize(name, SCRIPTS, SNAPSHOT)
            copy = fast.clone(name)
            start = time.monotonic()
            with pytest.raises(psycopg.errors.QueryCanceled):
                with psycopg.connect(
                    fast.app_dsn_for(copy), autocommit=True, connect_timeout=5
                ) as conn:
                    conn.execute("SELECT pg_sleep(30)")
            assert time.monotonic() - start < 10
        finally:
            # ALTER ROLE ... SET statement_timeout is server-wide, not per-manager; leaving
            # it at 1s would make every later test's app-role connection flaky.
            manager.bootstrap()

    def test_materialize_verify_and_clone_roundtrip(self, manager, master):
        copy = manager.clone(master)
        with psycopg.connect(manager.dsn_for(copy), autocommit=True) as conn:
            rows = conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
            assert rows == [(1, "seed"), (2, "second")]
            new_id = conn.execute("INSERT INTO t (v) VALUES ('third') RETURNING id").fetchone()[0]
        assert new_id == 3

        # Proves the GRANTs made during materialize survived CREATE DATABASE ... TEMPLATE:
        # harness_app, not just the admin role, can read what the scenario just wrote.
        with psycopg.connect(manager.app_dsn_for(copy), autocommit=True) as conn:
            rows = conn.execute("SELECT id, v FROM t ORDER BY id").fetchall()
            assert rows == [(1, "seed"), (2, "second"), (3, "third")]

    def test_materialize_is_idempotent(self, manager, master):
        manager.materialize(master, SCRIPTS, SNAPSHOT)
        assert manager.masters().count(master) == 1

    def test_materialize_failure_drops_temp(self, manager):
        manager.bootstrap()
        name = _unique(MASTER_PREFIX)
        bad_snapshot = {"rows": {"missing_table": [{"id": 1}]}, "counters": {}}
        with manager._admin() as conn:
            before_temps = set(manager._list(conn, TEMP_PREFIX))

        with pytest.raises(StoreManagerError):
            manager.materialize(name, SCRIPTS, bad_snapshot)
        assert name not in manager.masters()

        # Scoped to "no new temp survived this call", not "the server has none at all" --
        # a persistent-volume store or another test's concurrent build must not fail this.
        with manager._admin() as conn:
            after_temps = set(manager._list(conn, TEMP_PREFIX))
        assert after_temps <= before_temps

    def test_probe_then_immediate_clone(self, manager, master):
        with psycopg.connect(manager.dsn_for(master), autocommit=True) as conn:
            conn.execute("SELECT 1")
        copy = manager.clone(master)
        assert copy in manager.copies()

    def test_clone_with_connected_template_recovers(self, manager, master):
        held = psycopg.connect(manager.dsn_for(master), autocommit=True)
        held.execute("SELECT 1")
        copy = manager.clone(master)
        assert copy in manager.copies()
        with pytest.raises(psycopg.Error):
            held.execute("SELECT 1")

    def test_concurrent_clones(self, manager, master):
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = [future.result() for future in [pool.submit(manager.clone, master) for _ in range(20)]]
        assert len(set(results)) == 20

    def test_concurrent_materialize_single_winner(self, manager):
        manager.bootstrap()
        name = _unique(MASTER_PREFIX)
        with manager._admin() as conn:
            before_temps = set(manager._list(conn, TEMP_PREFIX))

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(manager.materialize, name, SCRIPTS, SNAPSHOT) for _ in range(4)]
            for future in futures:
                future.result()
        assert manager.masters().count(name) == 1

        with manager._admin() as conn:
            after_temps = set(manager._list(conn, TEMP_PREFIX))
        assert after_temps <= before_temps

    def test_clone_missing_master_is_loud(self, manager):
        with pytest.raises(StoreManagerError, match="materialize"):
            manager.clone("hm_never_made")

    def test_copy_quota(self, manager, master):
        limited = StoreManager(manager.dsn, copies_limit=len(manager.copies()) + 1)
        copy = limited.clone(master)
        with pytest.raises(QuotaExceeded) as exc_info:
            limited.clone(master)
        assert exc_info.value.kind == "copy"
        limited.drop(copy)
        assert limited.clone(master)

    def test_drop_idempotent_and_guarded(self, manager, master):
        copy = manager.clone(master)
        manager.drop(copy)
        manager.drop(copy)

        with pytest.raises(StoreManagerError):
            manager.drop("postgres")
        with pytest.raises(StoreManagerError):
            manager.drop("template1")

        with manager._admin() as conn:
            names = {row[0] for row in conn.execute("SELECT datname FROM pg_database").fetchall()}
        assert {"postgres", "template1"} <= names

    def test_sweep_temps(self, manager):
        dead = "ht_1_dead1"
        with manager._admin() as conn:
            conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(dead)))
        dropped = manager.sweep_temps(older_than_seconds=0)
        assert dead in dropped

        fresh = f"{TEMP_PREFIX}{int(time.time())}_x"
        try:
            with manager._admin() as conn:
                conn.execute(
                    psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(fresh))
                )
            survivors = manager.sweep_temps(older_than_seconds=3600)
            assert fresh not in survivors
            with manager._admin() as conn:
                names = {row[0] for row in conn.execute("SELECT datname FROM pg_database").fetchall()}
            assert fresh in names
        finally:
            manager.drop(fresh)


def test_save_to_uses_applied_scripts_not_docker(tmp_path, monkeypatch):
    import harness.world.stores.postgres as postgres_module
    from harness.world.stores import Held
    from harness.world.stores.postgres import PostgresStore

    assert not hasattr(postgres_module, "docker")

    store = PostgresStore.__new__(PostgresStore)
    store.applied = ["CREATE TABLE x (id int)"]

    def _boom(*args, **kwargs):
        raise AssertionError("docker should never be called by save_to")

    monkeypatch.setattr(postgres_module, "docker", _boom, raising=False)

    calls = []
    monkeypatch.setattr(Held, "save_to", lambda self, path: calls.append(path))

    store.save_to(tmp_path)

    assert calls == [tmp_path]
    schema = (tmp_path / postgres_module.SCHEMA).read_text()
    assert schema == "\n\n".join(store.applied)
