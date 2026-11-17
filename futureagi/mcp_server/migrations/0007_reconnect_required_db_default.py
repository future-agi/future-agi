import time

from django.db import OperationalError, migrations, transaction

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:mcp_server_mcpconnection"],
    "mitigation": (
        "The migration is not atomic and changes no rows. It gives the "
        "reconnect_required column of the MCP connections table its database "
        "default of false, which the model and migration 0005 already declare, "
        "where an earlier version of 0005 added the column without keeping one. "
        "It first reads the column default from information_schema. Where a "
        "default is already there, as on a fresh install or after an earlier "
        "run, it sends no ALTER and takes no lock blocking reads or writes. Else it "
        "tries one ALTER TABLE ... ALTER COLUMN ... SET DEFAULT false, alone in "
        "a short transaction under SET LOCAL lock_timeout 5s; the statement "
        "changes only the catalog and rewrites no rows, and behind an open "
        "transaction it waits at most 5 s for the table lock and then gives up "
        "rather than keep waiting. After 3 such waits, 2 s apart, the migration "
        "stops with one error saying the connections table was busy and the "
        "default was not set; it has changed nothing, is not recorded as "
        "applied, and can be run again. The reverse changes nothing: the column "
        "keeps its database default. Rows touched: none."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}

# An earlier version of 0005 added reconnect_required with
# AddField(BooleanField(default=False)), and Django drops the temporary database
# default after such an add: a database that ran it has the column boolean, not
# null, with no database default, while the model and the current 0005 declare
# db_default=False. Django does not run an applied migration again, so this one
# sets the default where it is missing and leaves it where it is present (a
# fresh database gets it from the current 0005).
#
# SET DEFAULT changes only the catalog, but it takes the table's ACCESS
# EXCLUSIVE lock, and while it waits for that lock behind an open transaction
# every later reader of the table waits behind it. So it runs alone in a short
# transaction whose lock wait is bounded (SET LOCAL lasts until that
# transaction ends), and it is tried a few times with a short pause between;
# then the migration stops, having set nothing, and can be run again.
TABLE = "mcp_server_mcpconnection"
COLUMN = "reconnect_required"
LOCK_TIMEOUT_SECONDS = 5
ATTEMPTS = 3
PAUSE_SECONDS = 2
LOCK_NOT_AVAILABLE = "55P03"
_READ_THE_DEFAULT = (
    "SELECT column_default FROM information_schema.columns"
    " WHERE table_schema = current_schema() AND table_name = %s"
    " AND column_name = %s"
)
_BOUND_THE_LOCK_WAIT = f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SECONDS}s'"
_SET_THE_DEFAULT = (
    'ALTER TABLE "mcp_server_mcpconnection" ALTER COLUMN "reconnect_required" '
    "SET DEFAULT false"
)
TABLE_BUSY = (
    "The MCP connections table was busy, so the database default of its "
    f"reconnect flag was not set after {ATTEMPTS} tries; running the migration "
    "again is safe."
)


def _lock_not_available(error):
    """Whether the database refused ``error``'s statement on its lock_timeout."""
    diag = getattr(error.__cause__, "diag", None)
    return getattr(diag, "sqlstate", None) == LOCK_NOT_AVAILABLE


def _set_the_default(schema_editor):
    """The column's database default, set only where it is missing."""
    if schema_editor.collect_sql:
        # sqlmigrate: print the statement; read and change nothing.
        schema_editor.execute(_SET_THE_DEFAULT, params=None)
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(_READ_THE_DEFAULT, [TABLE, COLUMN])
        row = cursor.fetchone()
    if row is not None and row[0] is not None:
        # Already there (fresh database or earlier run): no ALTER, no blocking lock.
        return
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with transaction.atomic(using=schema_editor.connection.alias):
                with schema_editor.connection.cursor() as cursor:
                    cursor.execute(_BOUND_THE_LOCK_WAIT)
                    cursor.execute(_SET_THE_DEFAULT)
            return
        except OperationalError as error:
            if not _lock_not_available(error):
                raise
        if attempt < ATTEMPTS:
            time.sleep(PAUSE_SECONDS)
    # Raised outside the handler: one plain error, without the statement.
    raise RuntimeError(TABLE_BUSY)


def set_the_default(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        _set_the_default(schema_editor)


class Migration(migrations.Migration):
    # Not one transaction: the ALTER TABLE runs alone in its own short
    # transaction (see _set_the_default), and the read before it takes no lock
    # that blocks reads or writes.
    atomic = False

    dependencies = [
        ("mcp_server", "0006_oauth_access_revocation"),
    ]
    operations = [
        # No state change: the state already declares db_default=False (0005).
        # The step sits in SeparateDatabaseAndState so that sqlmigrate prints
        # its statement; Django writes a top-level RunPython as "cannot be
        # written as SQL" without calling it.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(set_the_default, migrations.RunPython.noop)
            ],
        ),
    ]
