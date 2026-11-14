"""Opt-in SQL contract tests against the disposable hosted-CI PostgreSQL service.

Normal discovery never imports psycopg or connects. Explicit opt-in with a bad
prerequisite is an ERROR, not a skip. These tests are not product qualification.
"""
import os
from pathlib import Path
import platform
import sys
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.test_update_standalone_state import probe_module

PROBE_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000"
ADMIN_OPTIONS = "-c default_transaction_read_only=off -c statement_timeout=5000 -c lock_timeout=1000"


def native_settings():
    """Validate every safety gate before importing a client or opening a socket."""
    required = {"FAGI_UPDATE_PG_NATIVE_TEST": "1", "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted", "PGHOST": "127.0.0.1",
                "PGDATABASE": "updater_probe"}
    if any(os.environ.get(key) != value for key, value in required.items()):
        raise RuntimeError("native PostgreSQL tests require explicit isolated hosted-CI settings")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("native PostgreSQL tests require Linux/x86_64")
    port = os.environ.get("PGPORT", "")
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError("native PostgreSQL tests require a service port")
    if not os.environ.get("PGUSER") or not os.environ.get("PGPASSWORD"):
        raise RuntimeError("native PostgreSQL tests require synthetic service credentials")
    return dict(host="127.0.0.1", hostaddr="127.0.0.1", port=int(port), dbname="updater_probe",
                user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"], connect_timeout=5)


class RecordingConnection:
    """Pass through real SQL/results; hooks coordinate the independent DDL session."""
    def __init__(self, real, *, before=None, after=None):
        self.real, self.before, self.after = real, before, after
        self.info = real.info
        self.queries, self.origins = [], []
        self.rollbacks = 0
        self.status_after_rollback = None

    def __enter__(self):
        self.real.__enter__()
        return self

    def __exit__(self, *args):
        return self.real.__exit__(*args)

    def rollback(self):
        self.rollbacks += 1
        self.real.rollback()
        self.status_after_rollback = self.real.info.transaction_status

    def cursor(self):
        owner = self
        class Cursor:
            def __init__(self): self.real = owner.real.cursor()
            def __enter__(self):
                self.real.__enter__()
                return self
            def __exit__(self, *args): return self.real.__exit__(*args)
            def __getattr__(self, name): return getattr(self.real, name)
            def execute(self, query, params=None, **kwargs):
                owner.queries.append((query, params, kwargs))
                if owner.before:
                    owner.before(self.real, query, params)
                result = self.real.execute(query, params, **kwargs)
                if query.startswith("SELECT t.tableoid"):
                    pg = self.real.pgresult
                    owner.origins.append((pg.nfields, pg.ftable(0), pg.ftablecol(0)))
                if owner.after:
                    owner.after(self.real, query, params)
                return result
        return Cursor()


@unittest.skipUnless(os.environ.get("FAGI_UPDATE_PG_NATIVE_TEST") == "1", "isolated native PostgreSQL CI only")
class NativePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = native_settings()
        import psycopg
        from psycopg import sql
        cls.pg, cls.sql = psycopg, sql
        if psycopg.__version__ != "3.3.2":
            raise RuntimeError("native PostgreSQL tests require psycopg 3.3.2")
        with psycopg.connect(**cls.settings, options=ADMIN_OPTIONS, autocommit=True) as db:
            if db.info.server_version // 10000 != 16:
                raise RuntimeError("native PostgreSQL tests require PostgreSQL 16")
            # Hosted service port forwarding can give the server a container IP;
            # pin the CLIENT destination, not inet_server_addr() behind that NAT.
            identity = db.execute("SELECT current_database()").fetchone()
            if identity != ("updater_probe",) or db.info.host != "127.0.0.1":
                raise RuntimeError("native PostgreSQL service identity mismatch")
            print("PostgreSQL server_version={} psycopg={} libpq={}".format(
                db.info.server_version, psycopg.__version__, psycopg.pq.version()))

    def setUp(self):
        self.probe = probe_module()
        self.schema = "probe_" + uuid.uuid4().hex[:16]
        self.schemas, self.roles, self.servers = [self.schema], [], []
        self.created_fdw = False
        self.connections = []
        self.admin = self.pg.connect(**self.settings, options=ADMIN_OPTIONS, autocommit=True)
        self.addCleanup(self.cleanup)
        self.admin.execute(self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema)))

    def cleanup(self):
        # All fixture ownership is unique; never connect to or clean another DB.
        try:
            for schema in self.schemas:
                self.admin.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(schema)))
            for server in self.servers:
                self.admin.execute(self.sql.SQL("DROP SERVER IF EXISTS {} CASCADE").format(self.sql.Identifier(server)))
            for role in self.roles:
                self.admin.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(role)))
            if self.created_fdw:
                self.admin.execute("DROP EXTENSION postgres_fdw")
        finally:
            self.admin.close()

    def ident(self, name, schema=None):
        return self.sql.Identifier(schema or self.schema, name)

    def execute(self, template, *names):
        return self.admin.execute(self.sql.SQL(template).format(*(self.ident(name) for name in names)))

    def table(self, name="items", count=0):
        self.execute("CREATE TABLE {} (id integer) WITH (autovacuum_enabled=false)", name)
        if count:
            self.admin.execute(self.sql.SQL("INSERT INTO {} SELECT generate_series(1,%s)").format(self.ident(name)), (count,))
        return name

    def oid(self, name, schema=None):
        return self.admin.execute("SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s", (schema or self.schema, name)).fetchone()[0]

    def size(self, oid):
        return self.admin.execute("SELECT pg_catalog.pg_total_relation_size(%s::pg_catalog.oid)", (oid,)).fetchone()[0]

    def measure(self, *names, before=None, after=None, role=None):
        def connect():
            real = self.pg.connect(**self.settings, options=PROBE_OPTIONS)
            try:
                if role:
                    real.autocommit = True
                    real.execute(self.sql.SQL("SET ROLE {}").format(self.sql.Identifier(role)))
                    real.autocommit = False
                wrapper = RecordingConnection(real, before=before, after=after)
                self.connections.append(wrapper)
                return wrapper
            except BaseException:
                real.close()
                raise
        try:
            # Replace only the connection seam. Query text, metadata validation,
            # result parsing, and transaction/rollback logic are shipping code.
            with patch.object(self.probe, "postgres_connection", side_effect=connect):
                return self.probe.postgres_table_sizes([self.schema + "." + name for name in names])
        finally:
            if self.connections:
                connection = self.connections[-1]
                self.assertEqual(connection.rollbacks, 1)
                self.assertEqual(connection.status_after_rollback, self.pg.pq.TransactionStatus.IDLE)
                self.assertTrue(connection.real.closed)

    def assert_measurement(self, name, count):
        oid = self.oid(name)
        measured = self.measure(name)
        self.assertEqual(measured, [dict(engine="postgres", table=self.schema + "." + name,
                                         rows=count, bytes=self.size(oid))])
        connection = self.connections[-1]
        self.assertEqual(connection.origins, [(2, oid, -6)])
        metadata = [(q, p) for q, p, _ in connection.queries if "pg_catalog.pg_class" in q]
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0][1], (oid,))
        self.assertIn("WHERE c.oid=%s::pg_catalog.oid", metadata[0][0])
        self.assertEqual([kw for q, _, kw in connection.queries if q.startswith("SELECT t.tableoid")],
                         [{"prepare": False}])

    def test_unknown_statistics_empty_and_nonempty_use_visible_rows(self):
        for name, actual in (("empty", 0), ("nonempty", 7)):
            self.table(name, actual)
            stats = self.admin.execute("SELECT reltuples FROM pg_class WHERE oid=%s", (self.oid(name),)).fetchone()[0]
            self.assertEqual(stats, -1)
            self.assert_measurement(name, actual)

    def test_stale_zero_and_positive_statistics_never_drive_admission(self):
        self.table()
        self.execute("ANALYZE {}", "items")
        self.execute("INSERT INTO {} SELECT generate_series(1,3)", "items")
        self.assertEqual(self.admin.execute("SELECT reltuples FROM pg_class WHERE oid=%s", (self.oid("items"),)).fetchone()[0], 0)
        self.assert_measurement("items", 3)
        self.execute("ANALYZE {}", "items")
        self.execute("INSERT INTO {} SELECT generate_series(4,7)", "items")
        self.assertEqual(self.admin.execute("SELECT reltuples FROM pg_class WHERE oid=%s", (self.oid("items"),)).fetchone()[0], 3)
        self.assert_measurement("items", 7)
        self.execute("DELETE FROM {}", "items")
        self.assert_measurement("items", 0)

    def test_unlogged_table_and_identifier_truncation_boundary(self):
        self.execute("CREATE UNLOGGED TABLE {} (id integer)", "u" * 63)
        self.assert_measurement("u" * 63, 0)
        with self.assertRaises(self.probe.ProbeError):
            self.measure("u" * 63, "u" * 64)
        self.assertFalse(any(q.startswith("SELECT t.tableoid") for q, _, _ in self.connections[-1].queries))

    def test_index_and_toast_bytes_use_the_counted_oid(self):
        self.execute("CREATE TABLE {} (id integer PRIMARY KEY, payload text) WITH (autovacuum_enabled=false)", "large")
        self.execute("INSERT INTO {} SELECT i,(SELECT string_agg(md5(i::text || ':' || g::text),'') "
                     "FROM generate_series(1,2000) g) FROM generate_series(1,4) i", "large")
        oid = self.oid("large")
        indexes, toast = self.admin.execute("SELECT pg_indexes_size(oid),pg_total_relation_size(reltoastrelid) "
                                           "FROM pg_class WHERE oid=%s", (oid,)).fetchone()
        self.assertGreater(indexes, 0)
        self.assertGreater(toast, 8192)
        self.assert_measurement("large", 4)

    def test_empty_and_populated_materialized_views_preserve_origin(self):
        self.execute("CREATE MATERIALIZED VIEW {} AS SELECT 1 AS id WHERE false", "empty_mv")
        self.execute("CREATE MATERIALIZED VIEW {} AS SELECT generate_series(1,3) AS id", "full_mv")
        self.assert_measurement("empty_mv", 0)
        self.assert_measurement("full_mv", 3)

    def test_unpopulated_materialized_view_refuses_instead_of_fake_zero(self):
        self.execute("CREATE MATERIALIZED VIEW {} AS SELECT 1 AS id WITH NO DATA", "unpopulated")
        with self.assertRaises(self.pg.errors.ObjectNotInPrerequisiteState):
            self.measure("unpopulated")

    def test_nonowner_and_forced_owner_rls_raise_instead_of_filtered_count(self):
        self.table(count=3)
        role = self.schema + "_reader"
        self.roles.append(role)
        self.admin.execute(self.sql.SQL("CREATE ROLE {} NOSUPERUSER NOBYPASSRLS NOLOGIN").format(self.sql.Identifier(role)))
        self.admin.execute(self.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(self.sql.Identifier(self.schema), self.sql.Identifier(role)))
        self.admin.execute(self.sql.SQL("GRANT SELECT ON {} TO {}").format(self.ident("items"), self.sql.Identifier(role)))
        self.execute("ALTER TABLE {} ENABLE ROW LEVEL SECURITY", "items")
        self.execute("CREATE POLICY filtered ON {} USING (id=1)", "items")
        with self.assertRaises(self.pg.errors.InsufficientPrivilege) as error:
            self.measure("items", role=role)
        self.assertIn("row-level security", str(error.exception))
        self.admin.execute(self.sql.SQL("ALTER TABLE {} OWNER TO {}").format(self.ident("items"), self.sql.Identifier(role)))
        self.execute("ALTER TABLE {} FORCE ROW LEVEL SECURITY", "items")
        with self.assertRaises(self.pg.errors.InsufficientPrivilege) as error:
            self.measure("items", role=role)
        self.assertIn("row-level security", str(error.exception))

    def test_inheritance_parent_and_child_both_refuse(self):
        self.table("parent", 2)
        self.execute("CREATE TABLE {} () INHERITS ({})", "child", "parent")
        self.execute("INSERT INTO {} VALUES (3)", "child")
        for name in ("parent", "child"):
            with self.subTest(name=name), self.assertRaises(self.probe.ProbeError):
                self.measure(name)

    def test_partition_root_and_leaf_both_refuse(self):
        self.execute("CREATE TABLE {} (id integer) PARTITION BY RANGE (id)", "root")
        self.execute("CREATE TABLE {} PARTITION OF {} FOR VALUES FROM (0) TO (10)", "leaf", "root")
        self.execute("INSERT INTO {} VALUES (1)", "root")
        for name in ("root", "leaf"):
            with self.subTest(name=name), self.assertRaises((self.probe.ProbeError, self.pg.errors.FeatureNotSupported)):
                self.measure(name)

    def test_views_and_foreign_relations_reject_at_parse_without_fdw_access(self):
        self.execute("CREATE VIEW {} AS SELECT 1 AS id", "view_items")
        with self.assertRaises(self.pg.errors.FeatureNotSupported):
            self.measure("view_items")
        self.created_fdw = not self.admin.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='postgres_fdw')").fetchone()[0]
        self.admin.execute("CREATE EXTENSION IF NOT EXISTS postgres_fdw")
        server = self.schema + "_foreign"
        self.servers.append(server)
        self.admin.execute(self.sql.SQL("CREATE SERVER {} FOREIGN DATA WRAPPER postgres_fdw OPTIONS "
            "(host '192.0.2.1', port '1', dbname 'unreachable', connect_timeout '1')").format(self.sql.Identifier(server)))
        self.admin.execute(self.sql.SQL("CREATE USER MAPPING FOR CURRENT_USER SERVER {} OPTIONS (user 'synthetic')").format(self.sql.Identifier(server)))
        self.table("parent", 1)
        self.admin.execute(self.sql.SQL("CREATE FOREIGN TABLE {} () INHERITS ({}) SERVER {} "
            "OPTIONS (schema_name 'public', table_name 'unreachable')").format(
                self.ident("foreign_child"), self.ident("parent"), self.sql.Identifier(server)))
        started = time.monotonic()
        # An FDW connection failure must NOT satisfy either assertion: the direct
        # foreign table is rejected by TABLESAMPLE parse analysis (SQLSTATE 0A000),
        # while ONLY keeps the parent's foreign descendant out of planning.
        with self.assertRaises(self.pg.errors.FeatureNotSupported):
            self.measure("foreign_child")
        with self.assertRaises(self.probe.ProbeError):
            self.measure("parent")
        self.assertLess(time.monotonic() - started, 8)

    def test_count_lock_blocks_relation_rename_and_drop_until_rollback(self):
        self.table(count=2)
        self.admin.execute("SET lock_timeout='150ms'")
        for template in ("ALTER TABLE {} RENAME TO renamed", "DROP TABLE {}"):
            attempted = []
            def after(cursor, query, params):
                if query.startswith("SELECT t.tableoid"):
                    with self.assertRaises(self.pg.errors.LockNotAvailable):
                        self.execute(template, "items")
                    attempted.append(True)
            with self.subTest(ddl=template):
                self.assertEqual(self.measure("items", after=after)[0]["rows"], 2)
                self.assertEqual(attempted, [True])
        # The final rollback releases AccessShare, not just the Python cursor.
        self.execute("ALTER TABLE {} RENAME TO renamed", "items")
        self.execute("DROP TABLE {}", "renamed")

    def test_schema_rename_and_replacement_cannot_mix_count_and_size(self):
        self.table(count=2)
        original = self.oid("items")
        moved = self.schema + "_moved"
        self.schemas.append(moved)
        def after(cursor, query, params):
            if query.startswith("SELECT t.tableoid"):
                with self.admin.transaction():
                    self.admin.execute(self.sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                        self.sql.Identifier(self.schema), self.sql.Identifier(moved)))
                    self.admin.execute(self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema)))
                    self.table("items", 30)
        with self.assertRaises(self.probe.ProbeError):
            self.measure("items", after=after)
        connection = self.connections[-1]
        self.assertEqual(connection.origins, [(2, original, -6)])
        self.assertEqual([p for q, p, _ in connection.queries if "pg_catalog.pg_class" in q], [(original,)])
        self.assertEqual(self.oid("items", moved), original)
        self.assertNotEqual(self.oid("items"), original)

    def test_empty_child_attached_after_count_is_seen_by_fresh_metadata(self):
        self.table("parent", 2)
        self.table("child")
        attached = []
        def after(cursor, query, params):
            if query.startswith("SELECT t.tableoid"):
                self.execute("ALTER TABLE {} INHERIT {}", "child", "parent")
                attached.append(True)  # autocommit completed before catalog SELECT
        with self.assertRaises(self.probe.ProbeError):
            self.measure("parent", after=after)
        self.assertEqual(attached, [True])
        self.assertTrue(self.admin.execute("SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhparent=%s)",
                                          (self.oid("parent"),)).fetchone()[0])

    def test_production_lock_timeout_rolls_back(self):
        self.table()
        with self.admin.transaction():
            self.execute("ALTER TABLE {} ADD COLUMN held integer", "items")
            started = time.monotonic()
            with self.assertRaises(self.pg.errors.LockNotAvailable):
                self.measure("items")
            self.assertLess(time.monotonic() - started, 8)

    def test_production_statement_timeout_rolls_back(self):
        self.table()
        checked = []
        def before(cursor, query, params):
            if query.startswith("SELECT t.tableoid"):
                cursor.execute("SELECT current_setting('transaction_read_only'),current_setting('statement_timeout'),"
                               "current_setting('lock_timeout'),current_setting('row_security'),"
                               "current_setting('transaction_isolation'),current_setting('search_path')")
                self.assertEqual(cursor.fetchone(), ("on", "5s", "1s", "off", "read committed", "pg_catalog"))
                checked.append(True)
                cursor.execute("SELECT pg_catalog.pg_sleep(6)")
        started = time.monotonic()
        with self.assertRaises(self.pg.errors.QueryCanceled):
            self.measure("items", before=before)
        self.assertEqual(checked, [True])
        self.assertLess(time.monotonic() - started, 10)

    def test_second_table_failure_rolls_back_without_partial_return(self):
        self.table("first", 2)
        sentinel = object()
        result = sentinel
        with self.assertRaises(self.pg.errors.UndefinedTable):
            result = self.measure("first", "missing")
        self.assertIs(result, sentinel)
        self.assertEqual(len(self.connections[-1].origins), 1)
        self.execute("DROP TABLE {}", "first")  # first table's lock was released too


if __name__ == "__main__":
    unittest.main()
