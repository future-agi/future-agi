"""The reconnect flag gets its database default where an earlier migration left none.

An earlier version of migration 0005 added
mcp_server_mcpconnection.reconnect_required with
AddField(BooleanField(default=False)), and Django drops the temporary database
default after such an add: on a database that ran it the column is boolean, not
null, with no database default. The current 0005 declares db_default=False, but
Django does not run an applied migration again, so there a pod of the previous
release, which inserts connections without naming the column, is refused.
Migration 0007 sets the default where it is missing and does nothing where it
is present.

On real Postgres the real migration runs through ``MigrationExecutor`` on the
real connection wrapper, as ``manage.py migrate`` does: the hard-erase SQL guard
admits a migration's SQL only on the connection its executor holds. The column
as an earlier 0005 left it is made from a second connection. Where the default
is missing, the ALTER TABLE waits a bounded time for the table lock: behind an
open transaction that has read the table it gives up after a few short tries,
sets nothing, and a later run completes. Where the default is there, the
migration only reads information_schema: no ALTER, no lock that blocks reads or writes.
"""

import importlib
import io
import threading
import time
import uuid

import psycopg
import pytest
from django.core.management import call_command
from django.db import connections, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test.utils import CaptureQueriesContext
from psycopg import sql

from accounts.models import Organization, User
from accounts.tests.api_secret_migration_probes import applied
from mcp_server.tests.test_database_probe import test_database_connection

connection = connections["default"]
BEFORE = ("mcp_server", "0006_oauth_access_revocation")
AFTER = ("mcp_server", "0007_reconnect_required_db_default")
MODULE = "mcp_server.migrations.0007_reconnect_required_db_default"
# The column as an earlier 0005 left it: no database default.
DROP_THE_DEFAULT = (
    'ALTER TABLE "mcp_server_mcpconnection"'
    ' ALTER COLUMN "reconnect_required" DROP DEFAULT'
)
# The column as the rest of the test session expects it, put back after a test
# that removed its default.
PUT_BACK_THE_DEFAULT = (
    'ALTER TABLE "mcp_server_mcpconnection"'
    ' ALTER COLUMN "reconnect_required" SET DEFAULT false'
)
# The statement sqlmigrate prints for 0007.
PRINTED = (
    'ALTER TABLE "mcp_server_mcpconnection"'
    ' ALTER COLUMN "reconnect_required" SET DEFAULT false;'
)
# Statements a run that only reads the default never sends.
NOT_SENT = ("ALTER TABLE", "LOCK TABLE", "SET LOCAL")
# Should the ALTER wait for the table lock without giving up, the test's lock
# holder ends its transaction after this long, so the test fails rather than
# waits forever.
WATCHDOG_SECONDS = 60


def migrate(target):
    MigrationExecutor(connection).migrate([target])


def migrate_to_latest():
    """Every app's leaf, the schema the rest of a test session expects."""
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def column_default():
    """information_schema's column_default of reconnect_required."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            " AND table_name = 'mcp_server_mcpconnection'"
            " AND column_name = 'reconnect_required'"
        )
        (default,) = cursor.fetchone()
        return default


def drop_the_default(other):
    """The column as an earlier 0005 left it, written from another connection."""
    other.execute(DROP_THE_DEFAULT)
    assert column_default() is None


def put_back_the_default(other):
    if column_default() is None:
        other.execute(PUT_BACK_THE_DEFAULT)


def seed():
    """One connection to copy, at the schema before 0007."""
    org = Organization.objects.create(name=f"reconnect-default-{uuid.uuid4().hex[:8]}")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    migrate(BEFORE)
    executor = MigrationExecutor(connection)
    targets = [
        node for node in executor.loader.graph.leaf_nodes() if node[0] != "mcp_server"
    ] + [BEFORE]
    previous = executor.loader.project_state(targets).apps.get_model(
        "mcp_server", "MCPConnection"
    )
    return previous._base_manager.create(user_id=user.pk, organization_id=org.pk).pk


def insert_without_the_flag(probe, like):
    """Copy connection ``like`` under a new id from another connection, naming
    every column but reconnect_required, as a pod of the previous release
    inserts a connection: the new id, or the refusal."""
    names = [
        name
        for (name,) in probe.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            " AND table_name = 'mcp_server_mcpconnection'"
            " AND column_name <> 'reconnect_required'"
            " ORDER BY ordinal_position"
        ).fetchall()
    ]
    new = uuid.uuid4()
    statement = sql.SQL(
        "INSERT INTO mcp_server_mcpconnection ({columns})"
        " SELECT {values} FROM mcp_server_mcpconnection WHERE id = %s"
    ).format(
        columns=sql.SQL(", ").join(map(sql.Identifier, names)),
        values=sql.SQL(", ").join(
            sql.SQL("%s::uuid") if name == "id" else sql.Identifier(name)
            for name in names
        ),
    )
    try:
        probe.execute(statement, [new, like])
    except psycopg.errors.NotNullViolation as error:
        return f"refused: {error.diag.message_primary}"
    return new


def read_flag(probe, pk):
    (flag,) = probe.execute(
        "SELECT reconnect_required FROM mcp_server_mcpconnection WHERE id = %s", [pk]
    ).fetchone()
    return flag


def sent(captured):
    return [query["sql"] for query in captured.captured_queries]


def changes_sent(statements):
    """The statements that would change or lock the table, of those sent."""
    return [
        statement
        for statement in statements
        if any(word in statement.upper() for word in NOT_SENT)
    ]


def time_each_step(monkeypatch, module):
    """How long each run of the migration's database step takes, in seconds."""
    real = module._set_the_default
    took = []

    def timed(schema_editor):
        start = time.monotonic()
        try:
            return real(schema_editor)
        finally:
            took.append(time.monotonic() - start)

    monkeypatch.setattr(module, "_set_the_default", timed)
    return took


def test_the_migration_changes_no_state_and_runs_outside_one_transaction():
    migration = importlib.import_module(MODULE).Migration
    assert migration.atomic is False
    assert migration.dependencies == [BEFORE]
    # One database step and no state change: the state already declares
    # db_default=False, so makemigrations stays clean.
    (operation,) = migration.operations
    assert isinstance(operation, migrations.SeparateDatabaseAndState)
    assert operation.state_operations == []
    (step,) = operation.database_operations
    assert type(step) is migrations.RunPython
    assert step.reverse_code is migrations.RunPython.noop
    loader = MigrationLoader(None, ignore_no_migrations=True)
    key = ("mcp_server", "mcpconnection")
    before = loader.project_state(BEFORE).models[key]
    after = loader.project_state(AFTER).models[key]
    assert after == before
    assert after.fields["reconnect_required"].db_default is False


@pytest.mark.django_db(transaction=True)
def test_the_default_is_set_where_an_earlier_migration_left_none():
    probe = test_database_connection()
    other = test_database_connection(lock_timeout="5s")
    try:
        like = seed()
        drop_the_default(other)
        # As an earlier 0005 left it: an insert that does not name the column
        # is refused.
        refused = insert_without_the_flag(probe, like)
        assert isinstance(refused, str) and refused.startswith("refused"), refused

        migrate(AFTER)

        assert applied(AFTER)
        assert column_default() == "false"
        # A pod of the previous release adds a connection without naming the
        # column, and the row has the flag false.
        inserted = insert_without_the_flag(probe, like)
        assert isinstance(inserted, uuid.UUID), inserted
        assert read_flag(probe, inserted) is False
    finally:
        probe.close()
        put_back_the_default(other)
        other.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_where_the_default_is_there_the_migration_sends_no_alter_and_blocks_no_reader(
    monkeypatch,
):
    module = importlib.import_module(MODULE)
    holder = test_database_connection()
    watchdog = threading.Timer(WATCHDOG_SECONDS, holder.execute, ["ROLLBACK"])
    try:
        migrate(BEFORE)
        # A fresh database: the current 0005 gave the column its default.
        assert column_default() == "false"
        took = time_each_step(monkeypatch, module)
        # An open transaction that has read the table, as a slow request's
        # would: an ALTER TABLE could not take its lock until it ends.
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM mcp_server_mcpconnection").fetchone()
        watchdog.start()
        with CaptureQueriesContext(connection) as captured:
            migrate(AFTER)
        watchdog.cancel()

        # Done while the holder's transaction is still open, in a small part
        # of one lock wait.
        assert holder.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert len(took) == 1, took
        assert took[0] < module.LOCK_TIMEOUT_SECONDS / 5, took
        statements = sent(captured)
        assert any("information_schema.columns" in s for s in statements), statements
        assert changes_sent(statements) == [], statements
        assert applied(AFTER)
        assert column_default() == "false"
    finally:
        watchdog.cancel()
        monkeypatch.undo()
        holder.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_a_busy_table_stops_the_migration_and_a_rerun_sets_the_default():
    module = importlib.import_module(MODULE)
    other = test_database_connection(lock_timeout="5s")
    holder = test_database_connection()
    watchdog = threading.Timer(WATCHDOG_SECONDS, holder.execute, ["ROLLBACK"])
    try:
        migrate(BEFORE)
        drop_the_default(other)
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            (session_lock_timeout,) = cursor.fetchone()
        # An open transaction that has read the table, as a slow request's
        # would: the ALTER TABLE cannot take its lock until it ends.
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM mcp_server_mcpconnection").fetchone()
        watchdog.start()
        start = time.monotonic()
        with pytest.raises(RuntimeError) as stopped:
            migrate(AFTER)
        gave_up_after = time.monotonic() - start
        watchdog.cancel()

        # It gave up by itself, after its short waits, not when the holder
        # ended its transaction.
        waits = module.ATTEMPTS * module.LOCK_TIMEOUT_SECONDS
        assert module.ATTEMPTS > 1
        assert 0.9 * waits <= gave_up_after < WATCHDOG_SECONDS, gave_up_after
        # One plain sentence that says what happened and what to do; no SQL,
        # nothing chained.
        message = str(stopped.value)
        assert "busy" in message and "not set" in message, message
        assert "running the migration again is safe" in message.lower(), message
        assert message.endswith(".") and "." not in message[:-1], message
        assert "ALTER" not in message and '"' not in message, message
        assert stopped.value.__cause__ is None
        assert stopped.value.__context__ is None
        # Nothing changed, and 0007 is not recorded.
        assert column_default() is None
        assert not applied(AFTER)
        # The wait bound lasted only its own transaction.
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            assert cursor.fetchone() == (session_lock_timeout,)

        holder.execute("ROLLBACK")
        migrate(AFTER)

        assert applied(AFTER)
        assert column_default() == "false"
    finally:
        watchdog.cancel()
        holder.close()
        put_back_the_default(other)
        other.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_the_reverse_keeps_the_default_and_applying_again_sends_no_alter():
    other = test_database_connection(lock_timeout="5s")
    try:
        migrate(BEFORE)
        drop_the_default(other)
        migrate(AFTER)
        assert column_default() == "false"

        with CaptureQueriesContext(connection) as reverse:
            migrate(BEFORE)

        assert not applied(AFTER)
        assert column_default() == "false"
        assert changes_sent(sent(reverse)) == [], sent(reverse)

        with CaptureQueriesContext(connection) as again:
            migrate(AFTER)

        assert applied(AFTER)
        assert column_default() == "false"
        statements = sent(again)
        # It read the default, found it, and left it.
        assert any("information_schema.columns" in s for s in statements), statements
        assert changes_sent(statements) == [], statements
    finally:
        put_back_the_default(other)
        other.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_sqlmigrate_prints_the_alter_and_changes_nothing():
    other = test_database_connection(lock_timeout="5s")
    try:
        migrate(BEFORE)
        drop_the_default(other)
        out = io.StringIO()

        call_command("sqlmigrate", AFTER[0], AFTER[1], stdout=out)

        printed = out.getvalue()
        assert PRINTED in printed.splitlines(), printed
        assert "CANNOT BE WRITTEN AS SQL" not in printed, printed
        # Printed, not run: the default is still missing and 0007 is not
        # recorded.
        assert column_default() is None
        assert not applied(AFTER)
    finally:
        put_back_the_default(other)
        other.close()
        migrate_to_latest()
