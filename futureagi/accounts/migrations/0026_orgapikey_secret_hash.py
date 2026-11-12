"""Add the columns an API secret is sealed into (READINESS L03): schema only.

``secret_key_hash``, ``secret_key_hint`` and (system keys only)
``secret_key_ciphertext``. Migration 0027 seals every row: it is a separate,
non-atomic migration, so no lock it takes outlives one of its batches.

Table size and lock: ``ADD COLUMN ... DEFAULT '' NOT NULL`` changes only the
catalog on PostgreSQL 11 and later (every stack runs 16): no row is rewritten,
whatever the table's size. It takes the table's ACCESS EXCLUSIVE lock until its
transaction commits, so that transaction is one ALTER TABLE for the three
columns and nothing else (``add_columns``): it commits as the statement returns,
before Django records the migration. Measured in fix round 3 (lane receipts
``component/migration-0026-shapes.json``): three AddField operations in an
atomic migration held the lock 0.29-1.51 s on a loaded host (Django re-renders
the migration state between them), and one ALTER TABLE inside the atomic
migration still held it up to 0.95 s while Django recorded the migration; this
shape held it 1.0-1.6 ms (``component/migration-scale.json``).

Taking the lock waits for every open transaction that has read the table, and
lookups queue behind that wait, so ``lock_timeout`` bounds it to 5 s: past that
the migration stops with "canceling statement due to lock timeout", has changed
nothing, and can simply be run again. ``SET LOCAL`` lasts until that transaction
ends, which holds under PgBouncer's transaction pooling too.

The migration is non-atomic, so the ALTER commits before the migration is
recorded; ``IF NOT EXISTS`` (and ``IF EXISTS`` in reverse) keeps it re-runnable
when a process stops between the two. The fields' state is declared apart
(``state_operations``); a test pins the columns to the model's fields.

The new columns keep a database DEFAULT '' (db_default): a pod of the previous
release inserts keys without naming them while the deploy rolls.

Reverse: drops the columns, the same way. Reversing 0027 first restores system
keys' plaintext, and refuses while a live user or MCP key keeps only a hash.
"""

from django.db import migrations, models, transaction

from tfc.migration_database import executor_alias_scope

_BOUND_THE_LOCK_WAIT = "SET LOCAL lock_timeout = '5s'"
_ADD_COLUMNS = """
ALTER TABLE "accounts_orgapikey"
    ADD COLUMN IF NOT EXISTS "secret_key_hash" varchar(128) DEFAULT '' NOT NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_hint" varchar(32) DEFAULT '' NOT NULL,
    ADD COLUMN IF NOT EXISTS "secret_key_ciphertext" text DEFAULT '' NOT NULL
"""
_DROP_COLUMNS = """
ALTER TABLE "accounts_orgapikey"
    DROP COLUMN IF EXISTS "secret_key_ciphertext" CASCADE,
    DROP COLUMN IF EXISTS "secret_key_hint" CASCADE,
    DROP COLUMN IF EXISTS "secret_key_hash" CASCADE
"""


def _alone_in_a_transaction(schema_editor, statement):
    """``statement`` with the lock bound, committed as soon as it returns."""
    with transaction.atomic(using=schema_editor.connection.alias):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(_BOUND_THE_LOCK_WAIT)
            cursor.execute(statement)


def add_columns(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        _alone_in_a_transaction(schema_editor, _ADD_COLUMNS)


def drop_columns(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        _alone_in_a_transaction(schema_editor, _DROP_COLUMNS)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        (
            "accounts",
            "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_columns, drop_columns)],
            state_operations=[
                # Emits no SQL: Django keeps no database default for `default=`.
                migrations.AlterField(
                    model_name="orgapikey",
                    name="secret_key",
                    field=models.CharField(blank=True, default="", max_length=255),
                ),
                migrations.AddField(
                    model_name="orgapikey",
                    name="secret_key_hash",
                    field=models.CharField(
                        blank=True, db_default="", default="", max_length=128
                    ),
                ),
                migrations.AddField(
                    model_name="orgapikey",
                    name="secret_key_hint",
                    field=models.CharField(
                        blank=True, db_default="", default="", max_length=32
                    ),
                ),
                migrations.AddField(
                    model_name="orgapikey",
                    name="secret_key_ciphertext",
                    field=models.TextField(blank=True, db_default="", default=""),
                ),
            ],
        ),
    ]
