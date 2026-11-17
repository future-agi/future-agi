import time

from django.db import (
    DEFAULT_DB_ALIAS,
    OperationalError,
    migrations,
    models,
    transaction,
)
from django.utils import timezone

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:mcp_server_mcpconnection",
        "postgres:accounts_retirement_manifest",
        "postgres:accounts_orgapikey",
    ],
    "mitigation": (
        "The migration is not atomic. It adds "
        "mcp_server_mcpconnection.reconnect_required (boolean, not null, default "
        "false) with one ALTER TABLE ADD COLUMN IF NOT EXISTS that gives it a "
        "database default of false, committed as it returns. The column keeps that "
        "database default, so a pod of the previous release, which inserts "
        "connections without naming the column, can insert during and after the "
        "roll. The ALTER TABLE runs alone in a short transaction under SET LOCAL "
        "lock_timeout 5s, so behind an open transaction it waits at most 5 s for "
        "the table lock and then gives up rather than queue. After 3 such waits, 2 "
        "s apart, the migration stops with one error saying the connections table "
        "was busy; it has added nothing, and it can be run again: IF NOT EXISTS "
        "makes a re-run harmless. Then, for each "
        "MCP connection whose API key was retired as an organization key "
        "(accounts_orgapikey.retired_reason 733), it sets is_active false and "
        "reconnect_required true and records the prior flags in one "
        "accounts_retirement_manifest journal row (service mcp, action reconnect). "
        "Each connection is its own transaction that locks only that connection row "
        "and its journal row, and commits before the next one. A run stopped midway "
        "keeps the connections it finished and resumes on a re-run: a connection "
        "that already has a journal row is skipped when unchanged, and one changed "
        "since is marked superseded and left as it is. The reverse restores the "
        "journaled flags only where the connection is unchanged since the step, "
        "then drops the column. It refuses a connection with no journal or an "
        "incomplete one: the connections it restored before that stay restored, "
        "the migration stays recorded as applied, and running the reverse again "
        "resumes. Rows touched: the MCP connections bound to retired organization "
        "keys, at most one per person and workspace."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}

FLAGS = ("is_active", "reconnect_required")

# The column as Django adds BooleanField(default=False, db_default=False), with
# IF NOT EXISTS so a re-run after a stop does not fail on a column already
# there. It keeps its database default: a pod of the previous release inserts
# connections without naming it while the deploy rolls.
#
# ADD COLUMN with a constant default changes only the catalog on PostgreSQL 11
# and later, but it takes the table's ACCESS EXCLUSIVE lock, and while it waits
# for that lock behind an open transaction every later reader of the table
# waits behind it. So it runs alone in a short transaction whose lock wait is
# bounded (SET LOCAL lasts until that transaction ends), and it is tried a few
# times with a short pause between; then the migration stops, having added
# nothing, and can be run again.
LOCK_TIMEOUT_SECONDS = 5
ADD_ATTEMPTS = 3
PAUSE_SECONDS = 2
LOCK_NOT_AVAILABLE = "55P03"
_BOUND_THE_LOCK_WAIT = f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SECONDS}s'"
_ADD_COLUMN = (
    'ALTER TABLE "mcp_server_mcpconnection" ADD COLUMN IF NOT EXISTS '
    '"reconnect_required" boolean DEFAULT false NOT NULL'
)
_DROP_COLUMN = (
    'ALTER TABLE "mcp_server_mcpconnection" DROP COLUMN IF EXISTS '
    '"reconnect_required" CASCADE'
)
TABLE_BUSY = (
    "The MCP connections table was busy: the reconnect migration waited for its "
    f"lock {ADD_ATTEMPTS} times and added nothing. Running the migration again "
    "is safe."
)


def _lock_not_available(error):
    """Whether the database refused ``error``'s statement on its lock_timeout."""
    diag = getattr(error.__cause__, "diag", None)
    return getattr(diag, "sqlstate", None) == LOCK_NOT_AVAILABLE


def _add_the_column(schema_editor):
    """The column's ADD alone under a bounded lock wait, tried a few times."""
    for attempt in range(1, ADD_ATTEMPTS + 1):
        try:
            with transaction.atomic(using=schema_editor.connection.alias):
                with schema_editor.connection.cursor() as cursor:
                    cursor.execute(_BOUND_THE_LOCK_WAIT)
                    cursor.execute(_ADD_COLUMN)
            return
        except OperationalError as error:
            if not _lock_not_available(error):
                raise
        if attempt < ADD_ATTEMPTS:
            time.sleep(PAUSE_SECONDS)
    # Raised outside the handler: one plain error, without the statement.
    raise RuntimeError(TABLE_BUSY)


def add_column(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        _add_the_column(schema_editor)


def drop_column(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        schema_editor.execute(_DROP_COLUMN, params=None)


def fingerprint(row):
    result = {field: getattr(row, field) for field in FLAGS}
    result.update(deleted=row.deleted, updated_at=row.updated_at.isoformat())
    for field in ("user_id", "organization_id", "api_key_id"):
        value = getattr(row, field)
        result[field] = str(value) if value is not None else None
    return result


def flag_reconnect(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (
            schema_editor.connection.alias
            if schema_editor is not None
            else DEFAULT_DB_ALIAS
        )
        connections = apps.get_model("mcp_server", "MCPConnection")._base_manager.using(
            alias
        )
        journals = apps.get_model("accounts", "RetirementManifest").objects.using(alias)
        ids = connections.filter(api_key__retired_reason="733").values_list(
            "pk", flat=True
        )
        for pk in ids.iterator():
            with transaction.atomic(using=alias):
                row = connections.select_for_update().get(pk=pk)
                saved = (
                    journals.select_for_update()
                    .filter(service="mcp", action="reconnect", object_id=str(pk))
                    .first()
                )
                if saved is not None and saved.superseded_by:
                    continue
                if saved is not None and saved.reverted_at is None:
                    if saved.applied_state != fingerprint(row):
                        saved.superseded_by = "object_changed"
                        saved.save(using=alias, update_fields=["superseded_by"])
                    continue
                prior = {field: getattr(row, field) for field in FLAGS}
                row.is_active, row.reconnect_required = False, True
                row.updated_at = timezone.now()
                # Sender-specific live handlers are bypassed; global signals inherit this scope.
                row.save(using=alias, update_fields=[*FLAGS, "updated_at"])
                values = {
                    "prior_state": prior,
                    "applied_state": fingerprint(row),
                    "applied_at": timezone.now(),
                    "reverted_at": None,
                    "superseded_by": "",
                    "confirmed": True,
                }
                if saved is None:
                    journals.create(
                        service="mcp", action="reconnect", object_id=str(pk), **values
                    )
                else:
                    for field, value in values.items():
                        setattr(saved, field, value)
                    saved.save(using=alias, update_fields=list(values))


def restore_connections(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (
            schema_editor.connection.alias
            if schema_editor is not None
            else DEFAULT_DB_ALIAS
        )
        connections = apps.get_model("mcp_server", "MCPConnection")._base_manager.using(
            alias
        )
        journals = apps.get_model("accounts", "RetirementManifest").objects.using(alias)
        ids = connections.filter(api_key__retired_reason="733").values_list(
            "pk", flat=True
        )
        for pk in ids.iterator():
            with transaction.atomic(using=alias):
                row = connections.select_for_update().get(pk=pk)
                saved = (
                    journals.select_for_update()
                    .filter(service="mcp", action="reconnect", object_id=str(pk))
                    .first()
                )
                if saved is None:
                    raise RuntimeError(
                        "Cannot restore a connection without its retirement journal"
                    )
                if saved.superseded_by or saved.reverted_at is not None:
                    continue
                if (
                    not saved.confirmed
                    or not isinstance(saved.prior_state, dict)
                    or set(saved.prior_state) != set(FLAGS)
                    or any(
                        type(saved.prior_state[field]) is not bool for field in FLAGS
                    )
                ):
                    raise RuntimeError(
                        "Cannot restore an incomplete connection retirement journal"
                    )
                if saved.applied_state != fingerprint(row):
                    saved.superseded_by = "object_changed"
                    saved.save(using=alias, update_fields=["superseded_by"])
                    continue
                for field, value in saved.prior_state.items():
                    setattr(row, field, value)
                row.updated_at = timezone.now()
                row.save(using=alias, update_fields=[*FLAGS, "updated_at"])
                saved.reverted_at = timezone.now()
                saved.save(using=alias, update_fields=["reverted_at"])


class Migration(migrations.Migration):
    # Not one transaction: the column's ALTER TABLE would otherwise keep the
    # table's ACCESS EXCLUSIVE lock until the data step ends. Each connection
    # in flag_reconnect and restore_connections is its own transaction.
    atomic = False

    dependencies = [
        ("mcp_server", "0004_remove_retired_tool_groups"),
        ("accounts", "0034_retirement_manifest"),
    ]
    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="mcpconnection",
                    name="reconnect_required",
                    field=models.BooleanField(default=False, db_default=False),
                ),
            ],
            # The column as AddField adds it, with its database default kept,
            # committed as it returns; the add gives up on a busy table (see
            # _add_the_column). IF EXISTS keeps the reverse re-runnable too.
            database_operations=[migrations.RunPython(add_column, drop_column)],
        ),
        migrations.RunPython(flag_reconnect, restore_connections),
    ]
