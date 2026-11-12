"""Seal every stored API secret (READINESS L03): data only, non-atomic, batched.

Afterwards no row holds a plaintext ``secret_key``, soft-deleted rows included:
each has ``secret_key_hash`` and, for system and ingest keys,
``secret_key_ciphertext`` (``api_key_lookup.RECOVERABLE_TYPES``).
Needs the pepper (FI_API_SECRET_PEPPER) and, for system keys,
INTEGRATION_ENCRYPTION_KEY outside local and test installs; without them it
stops at the first row with the setting's name.

Table size and lock: no transaction spans the run (``atomic = False``, as
accounts 0022). ``backfill_secret_hashes`` takes the rows in primary-key batches
of 500 and writes each batch with one UPDATE, its own short transaction, and
only where a row is still as read (a re-seal on use meanwhile is kept). That
statement takes the table in ROW EXCLUSIVE mode and locks the rows it writes
until it commits; nothing is held between statements. A lookup only reads, so
it never waits on the run (from Django or fi-collector); a re-seal on use of a
row being written waits for that one statement. The run's length grows with the
number of keys (runbook.md gives the measured cost per key).

A row that fails to seal for any reason but configuration is counted, logged by
id and left with its plaintext; the run goes on, the deploy is not stopped, and
``manage.py api_secrets status`` exits 1 until ``backfill`` seals it. A database
error stops the run: the batches it committed stay sealed, and running the
migration again seals the rest, because it only touches rows still holding a
plaintext. That is why the columns come in 0026: a non-atomic migration that
also added them could not be run again after failing half way.

Reverse: system and ingest keys get their plaintext back from the ciphertext. A live user or
MCP key's secret is one-way hashed, so the reverse refuses while one exists:
roll back by restoring the database backup taken before this migration.

Deploy fi-collector from the same release first; it reads either form. See
.omega/program/FA-API-SECRET-HASH/runbook.md.
"""

from django.db import DEFAULT_DB_ALIAS, migrations
from django.db.migrations.exceptions import IrreversibleError

from tfc.migration_database import executor_alias_scope


def seal_plaintext_secrets(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.api_key_lookup import backfill_secret_hashes

        OrgApiKey = apps.get_model("accounts", "OrgApiKey")
        counts = backfill_secret_hashes(OrgApiKey, using=(schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
        if any(counts.values()):
            print(
                f"\n  API secrets: {counts['sealed']} sealed ({counts['system']} system"
                f" keys), {counts['stale_cleared']} stale values cleared,"
                f" {counts['skipped']} already sealed by a request, {counts['failed']} failed"
            )
        if counts["failed"]:
            print(
                f"  {counts['failed']} keys keep their plaintext: see"
                " api_secret_backfill_row_failed in the log, then run"
                " `manage.py api_secrets backfill`."
            )


def restore_system_secrets(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        from accounts.api_key_lookup import RECOVERABLE_TYPES
        from accounts.api_secrets import decrypt_secret

        OrgApiKey = apps.get_model("accounts", "OrgApiKey")
        manager = OrgApiKey._base_manager.db_manager((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
        # System and ingest keys keep a Fernet copy (RECOVERABLE_TYPES; ingest since
        # integration round 7, when dev's agent sandbox keys met L03).
        hash_only = (
            manager.filter(secret_key="", deleted=False)
            .exclude(type__in=RECOVERABLE_TYPES)
            .exclude(secret_key_hash="")
            .count()
        )
        if hash_only:
            raise IrreversibleError(
                f"accounts 0027 cannot be reversed: {hash_only} live user or MCP API "
                "keys keep only a one-way hash of their secret. Restore the database "
                "backup taken before this migration instead."
            )
        rows = (
            manager.filter(secret_key="", type__in=RECOVERABLE_TYPES)
            .exclude(secret_key_ciphertext="")
            .values_list("pk", "secret_key_ciphertext")
        )
        # One short UPDATE per key, each its own transaction (non-atomic migration).
        for pk, ciphertext in rows:
            manager.filter(pk=pk, secret_key="").update(
                secret_key=decrypt_secret(ciphertext)
            )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("accounts", "0026_orgapikey_secret_hash")]

    operations = [
        migrations.RunPython(seal_plaintext_secrets, restore_system_secrets),
    ]
