"""The reconnect migration locks one connection at a time and resumes after a stop.

Migration 0005 adds mcp_server_mcpconnection.reconnect_required, then flags each
connection bound to a retired organization key and journals its prior flags.
Run as one transaction, the column's ALTER TABLE keeps the table's ACCESS
EXCLUSIVE lock until the last connection is flagged, so every read and write of
MCP connections waits for the whole data step. The migration is non-atomic: the
column is added by a short statement of its own, and each connection is flagged
in its own transaction that locks that connection and its journal row.

On real Postgres the real migration runs through ``MigrationExecutor``. A hook
inside the data step (``fingerprint``, called once a connection's flags are
saved and before its journal row is written) lets a second connection do what a
concurrent request does, or stops the run the way a stopped process would. A
run stopped partway keeps the connections it finished, and running it again
flags the rest, each once. A column already there, from a run that stopped
after adding it, is left as it is. The column keeps a database default of
false, from the moment it is added and after the migration finishes, so a pod
of the previous release, which inserts connections without naming the column,
keeps working while the step runs, after a stop and for the rest of the roll.
The column's ALTER TABLE waits a bounded time for the table lock: behind an
open transaction that has read the table it gives up after a few short tries,
adds nothing, and a later run completes.

The executor runs on the real connection wrapper, as ``manage.py migrate`` does:
the hard-erase SQL guard admits a migration's SQL only on the connection its
executor holds, and Django's ``connection`` proxy is not that object.
"""

import gc
import importlib
import threading
import time
import uuid

import psycopg
import pytest
from django.apps import apps
from django.db import ProgrammingError, connections, migrations
from django.db.migrations.executor import MigrationExecutor
from psycopg import sql

from accounts.models import Organization, OrgApiKey, RetirementManifest, User
from accounts.tests.api_secret_migration_probes import applied
from mcp_server.tests.test_database_probe import test_database_connection

connection = connections["default"]
BEFORE = ("mcp_server", "0004_remove_retired_tool_groups")
AFTER = ("mcp_server", "0005_personal_key_reconnect")
MODULE = "mcp_server.migrations.0005_personal_key_reconnect"
# Table locks that leave reads (AccessShareLock) and other connections' row
# locks and writes (RowShareLock, RowExclusiveLock) free to go on.
ROW_LEVEL_ONLY = {"AccessShareLock", "RowShareLock", "RowExclusiveLock"}
# What the migration leaves, as BooleanField(default=False, db_default=False)
# declares: the column keeps its database default of false, so a pod of the
# previous release, which inserts connections without naming the column, keeps
# working for the whole roll.
COLUMN = ("boolean", "NO", "false")
# What the column is while the data step runs, and after a stop: it has its
# database default from the moment it is added.
DURING = ("boolean", "NO", "false")
# Should the column add wait for the table lock without giving up, the test's
# lock holder ends its transaction after this long, so the test fails rather
# than waits forever.
WATCHDOG_SECONDS = 60
LOCK_ROW = "SELECT id FROM mcp_server_mcpconnection WHERE id = %s FOR UPDATE NOWAIT"
READ_FLAGS = (
    "SELECT is_active, reconnect_required FROM mcp_server_mcpconnection WHERE id = %s"
)


class Stopped(Exception):
    """A process that stops partway through the data step."""


def migrate(target):
    MigrationExecutor(connection).migrate([target])


def migrate_to_latest():
    """Every app's leaf, the schema the rest of a test session expects."""
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def seed(affected):
    """``affected`` connections bound to a retired organization key, half of
    them active, and one bound to a personal key, at the schema before 0005."""
    org = Organization.objects.create(name=f"reconnect-{uuid.uuid4().hex[:8]}")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    retired = OrgApiKey.no_workspace_objects.create(
        organization=org, user=user, type="mcp", retired_reason="733", enabled=False
    )
    personal = OrgApiKey.no_workspace_objects.create(
        organization=org, user=user, type="user"
    )
    migrate(BEFORE)
    executor = MigrationExecutor(connection)
    targets = [
        node for node in executor.loader.graph.leaf_nodes() if node[0] != "mcp_server"
    ] + [BEFORE]
    previous = executor.loader.project_state(targets).apps.get_model(
        "mcp_server", "MCPConnection"
    )

    def create(key, active):
        return previous._base_manager.create(
            user_id=user.pk, organization_id=org.pk, api_key_id=key.pk, is_active=active
        ).pk

    rows = {}
    for index in range(affected):
        active = index % 2 == 0
        rows[create(retired, active)] = active
    return rows, create(personal, True)


def on_each_row(monkeypatch, step):
    """Call ``step(pk, seen)`` inside the data step, once for each connection.

    ``seen`` lists the connections reached so far, in the order reached; the
    last one is being flagged, inside its own transaction."""
    module = importlib.import_module(MODULE)
    real = module.fingerprint
    seen = []

    def fingerprint(row):
        if row.pk not in seen:
            seen.append(row.pk)
            step(row.pk, seen)
        return real(row)

    monkeypatch.setattr(module, "fingerprint", fingerprint)
    return seen


def column():
    """(type, nullable, default) of reconnect_required, or None without it."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = current_schema()"
            " AND table_name = 'mcp_server_mcpconnection'"
            " AND column_name = 'reconnect_required'"
        )
        return cursor.fetchone()


def flags(pk):
    with connection.cursor() as cursor:
        cursor.execute(READ_FLAGS, [pk])
        return cursor.fetchone()


def updated_at(pk):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT updated_at FROM mcp_server_mcpconnection WHERE id = %s", [pk]
        )
        return cursor.fetchone()[0]


def journals():
    """Every reconnect journal row by connection id; one row per connection."""
    rows = list(
        RetirementManifest.objects.filter(service="mcp", action="reconnect").values(
            "object_id",
            "prior_state",
            "applied_state",
            "applied_at",
            "reverted_at",
            "superseded_by",
            "confirmed",
        )
    )
    by_id = {row.pop("object_id"): row for row in rows}
    assert len(by_id) == len(rows), rows
    return by_id


def lock_row(probe, pk):
    """Whether another connection can lock the row now: "locked" or "waited"."""
    try:
        found = probe.execute(LOCK_ROW, [pk]).fetchone()
    except psycopg.errors.LockNotAvailable:
        return "waited"
    return "locked" if found else "missing"


def read_flags(probe, pk):
    """The row's committed flags as another connection reads them now."""
    try:
        return probe.execute(READ_FLAGS, [pk]).fetchone()
    except psycopg.errors.LockNotAvailable:
        return "waited"


def insert_as_previous_release(probe, like):
    """Copy connection ``like`` under a new id from another connection, naming
    every column but reconnect_required, as a pod of the previous release
    inserts a connection: the new id, "waited", or the refusal."""
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
    except psycopg.errors.LockNotAvailable:
        return "waited"
    except psycopg.errors.NotNullViolation as error:
        return f"refused: {error.diag.message_primary}"
    return new


def table_locks(probe, pid):
    """The table-level lock modes ``pid`` holds on mcp_server_mcpconnection."""
    rows = probe.execute(
        "SELECT mode FROM pg_locks WHERE pid = %s AND locktype = 'relation'"
        " AND relation = 'mcp_server_mcpconnection'::regclass",
        [pid],
    ).fetchall()
    return {mode for (mode,) in rows}


def test_the_reconnect_migration_runs_outside_one_transaction():
    migration = importlib.import_module(MODULE).Migration
    assert migration.atomic is False
    data_steps = [
        operation
        for operation in migration.operations
        if isinstance(operation, migrations.RunPython)
    ]
    assert data_steps and all(step.atomic is not True for step in data_steps)


@pytest.mark.django_db(transaction=True)
def test_other_connections_can_be_read_and_locked_while_the_data_step_runs(
    monkeypatch,
):
    probe = test_database_connection()
    try:
        rows, control = seed(affected=3)
        observed = {}

        def step(pk, seen):
            if len(seen) != 2:
                return
            # The first connection is flagged; the second is being flagged now,
            # under its own row lock; the third is not reached yet.
            finished = seen[0]
            not_reached = next(key for key in rows if key not in seen)
            observed.update(
                table_locks=table_locks(probe, connection.connection.info.backend_pid),
                lock={
                    "control": lock_row(probe, control),
                    "finished": lock_row(probe, finished),
                    "not_reached": lock_row(probe, not_reached),
                },
                read={
                    "control": read_flags(probe, control),
                    "finished": read_flags(probe, finished),
                },
                insert=insert_as_previous_release(probe, control),
            )

        on_each_row(monkeypatch, step)
        migrate(AFTER)
        monkeypatch.undo()

        assert observed["lock"] == {
            "control": "locked",
            "finished": "locked",
            "not_reached": "locked",
        }, observed
        # The finished connection's flags are committed before the run ends.
        assert observed["read"] == {
            "control": (True, False),
            "finished": (False, True),
        }, observed
        # The connection being flagged holds its row's lock: the table locks
        # seen are its backend's, and none of them stops another connection.
        assert "RowExclusiveLock" in observed["table_locks"], observed
        assert observed["table_locks"] <= ROW_LEVEL_ONLY, observed
        # A pod of the previous release adds a connection while the step runs.
        inserted = observed["insert"]
        assert isinstance(inserted, uuid.UUID), observed
        assert applied(AFTER) and column() == COLUMN
        assert all(flags(pk) == (False, True) for pk in rows)
        assert flags(control) == (True, False)
        # Not bound to a retired key: the data step leaves it as inserted.
        assert flags(inserted) == (True, False)
        assert str(inserted) not in journals()
    finally:
        monkeypatch.undo()
        probe.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_a_run_stopped_partway_keeps_what_it_finished_and_a_rerun_flags_the_rest_once(
    monkeypatch,
):
    probe = test_database_connection()
    try:
        rows, control = seed(affected=4)
        control_updated = updated_at(control)
        before = {pk: updated_at(pk) for pk in rows}

        def step(pk, seen):
            if len(seen) > 2:
                raise Stopped(pk)

        seen = on_each_row(monkeypatch, step)
        with pytest.raises(Stopped):
            migrate(AFTER)
        monkeypatch.undo()
        # Close the stopped run's cursor over the connection ids now, while no
        # transaction is open, as the end of a stopped process would.
        gc.collect()
        finished = seen[:2]

        # The column, added before the data step, stays after the stop, with
        # its database default.
        assert column() == DURING
        assert not applied(AFTER)
        # A pod of the previous release still adds connections after the stop.
        inserted = insert_as_previous_release(probe, control)
        assert isinstance(inserted, uuid.UUID), inserted
        assert flags(inserted) == (True, False)
        kept = journals()
        assert set(kept) == {str(pk) for pk in finished}, (kept, seen)
        for pk, active in rows.items():
            if pk in finished:
                assert flags(pk) == (False, True)
            else:
                # The connection being flagged at the stop is rolled back.
                assert flags(pk) == (active, False)
                assert updated_at(pk) == before[pk]
        flagged_at = {pk: updated_at(pk) for pk in finished}

        migrate(AFTER)

        assert applied(AFTER) and column() == COLUMN
        saved = journals()
        assert set(saved) == {str(pk) for pk in rows}
        for pk, active in rows.items():
            assert flags(pk) == (False, True)
            journal = saved[str(pk)]
            assert journal["prior_state"] == {
                "is_active": active,
                "reconnect_required": False,
            }
            assert journal["confirmed"] and journal["reverted_at"] is None
            assert journal["superseded_by"] == ""
        for pk in finished:
            # Flagged once: the second run left these and their journal rows.
            assert saved[str(pk)] == kept[str(pk)]
            assert updated_at(pk) == flagged_at[pk]
        assert flags(control) == (True, False)
        assert updated_at(control) == control_updated
        assert str(control) not in saved
        assert flags(inserted) == (True, False)
        assert str(inserted) not in saved
    finally:
        monkeypatch.undo()
        probe.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_the_column_add_is_a_no_op_when_the_column_is_already_there():
    # The column as a run that stopped after adding it left it: written from
    # another connection, as that process did.
    other = test_database_connection(lock_timeout="5s")
    try:
        rows, control = seed(affected=1)
        other.execute(
            'ALTER TABLE "mcp_server_mcpconnection"'
            ' ADD COLUMN "reconnect_required" boolean DEFAULT false NOT NULL'
        )
        try:
            migrate(AFTER)
        except ProgrammingError as error:
            pytest.fail(f"the migration refused a column already there: {error}")
        assert applied(AFTER)
        assert column() == COLUMN
        assert all(flags(pk) == (False, True) for pk in rows)
        assert flags(control) == (True, False)
    finally:
        if not applied(AFTER):
            other.execute(
                'ALTER TABLE "mcp_server_mcpconnection"'
                ' DROP COLUMN IF EXISTS "reconnect_required"'
            )
        other.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_a_previous_release_insert_works_after_the_migration_finishes():
    probe = test_database_connection()
    try:
        rows, control = seed(affected=1)
        migrate(AFTER)
        assert applied(AFTER)

        # A pod of the previous release still serving once the migration is
        # done adds a connection without naming the column.
        inserted = insert_as_previous_release(probe, control)
        assert isinstance(inserted, uuid.UUID), inserted
        assert flags(inserted) == (True, False)
        # information_schema: the column keeps its database default of false
        # and stays not null.
        assert column() == ("boolean", "NO", "false") == COLUMN
        # The data step left the new row alone and flagged the affected one.
        assert all(flags(pk) == (False, True) for pk in rows)
        assert str(inserted) not in journals()
    finally:
        probe.close()
        migrate_to_latest()


def test_the_migration_state_and_the_model_declare_the_database_default():
    migration = importlib.import_module(MODULE).Migration
    added = [
        operation
        for outer in migration.operations
        if isinstance(outer, migrations.SeparateDatabaseAndState)
        for operation in outer.state_operations
        if isinstance(operation, migrations.AddField)
        and operation.name == "reconnect_required"
    ]
    assert len(added) == 1, added
    state_field = added[0].field
    model_field = apps.get_model("mcp_server", "MCPConnection")._meta.get_field(
        "reconnect_required"
    )
    assert state_field.db_default is False
    assert model_field.db_default is False
    assert state_field.default is False and model_field.default is False
    # Same class and arguments: the state matches the model, field for field.
    assert state_field.deconstruct()[1:] == model_field.deconstruct()[1:]


@pytest.mark.django_db(transaction=True)
def test_the_column_add_gives_up_on_a_busy_table_and_a_rerun_completes():
    module = importlib.import_module(MODULE)
    holder = test_database_connection()
    watchdog = threading.Timer(WATCHDOG_SECONDS, holder.execute, ["ROLLBACK"])
    try:
        rows, control = seed(affected=2)
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            (session_lock_timeout,) = cursor.fetchone()
        # An open transaction that has read the table, as a slow request's
        # would: the column's ALTER TABLE cannot take its lock until it ends.
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
        waits = module.ADD_ATTEMPTS * module.LOCK_TIMEOUT_SECONDS
        assert module.ADD_ATTEMPTS > 1
        assert 0.9 * waits <= gave_up_after < WATCHDOG_SECONDS, gave_up_after
        # One plain error that says what happened and what to do; no SQL.
        message = str(stopped.value)
        assert "busy" in message, message
        assert "running the migration again is safe" in message.lower(), message
        assert "ALTER" not in message and '"' not in message, message
        assert stopped.value.__cause__ is None
        assert stopped.value.__context__ is None
        # Nothing changed: no column, nothing journaled, not recorded.
        assert column() is None
        assert journals() == {}
        assert not applied(AFTER)
        # The wait bound lasted only its own transaction.
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            assert cursor.fetchone() == (session_lock_timeout,)

        holder.execute("ROLLBACK")
        migrate(AFTER)

        assert applied(AFTER) and column() == COLUMN
        assert all(flags(pk) == (False, True) for pk in rows)
        assert flags(control) == (True, False)
        assert set(journals()) == {str(pk) for pk in rows}
    finally:
        watchdog.cancel()
        holder.close()
        migrate_to_latest()
