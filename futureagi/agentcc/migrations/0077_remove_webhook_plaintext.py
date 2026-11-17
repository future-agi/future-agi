"""M3: reverify ALL originals; atomically remove plaintext LAST and constrain.

Requires PostgreSQL transactional DDL and the same maintenance fence as M2.
Logical DROP does not erase historic WAL, snapshots, replicas or backups.
"""

from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError

from agentcc.migrations import _integration_key_guard as key_guard
from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_organization",
        "postgres:agentcc_webhook",
    ],
    "mitigation": (
        "In one transaction (atomic=True): verifies every agentcc_webhook row's "
        "encrypted_headers and encrypted_secret against the headers and secret columns "
        "(primary-key batches of 250, rows locked FOR UPDATE, organization ids checked "
        "in accounts_organization), drops the headers and secret columns, makes the "
        "three storage columns NOT NULL, adds two check constraints (storage version "
        "1, non-empty encrypted values), then reads every row again and checks that "
        "each stored value decrypts. The NOT NULL changes and constraints scan the "
        "table under the transaction's lock. Replay-safe: the data steps are read-only "
        "checks, and a failure at any step rolls back the whole migration. Reverse "
        "refuses (IrreversibleError). Row scale: one row per webhook endpoint, all "
        "organizations. Needs a stable INTEGRATION_ENCRYPTION_KEY; refuses to start "
        "without one when there is anything to encrypt."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def verify_before_remove(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # Before the plaintext columns are dropped: any row would leave only
        # its encrypted copy.
        webhooks = apps.get_model("prism", "AgentccWebhook")._base_manager.using(
            schema_editor.connection.alias
        )
        key_guard.require_stable_key(webhooks.exists)
        storage.closed_call(storage.verify_all, apps, schema_editor.connection)


def check_after_remove(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        storage.closed_call(storage.final_check, apps, schema_editor.connection)


def backwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # Last operation runs first on reverse, before recreating plaintext.
        raise IrreversibleError(storage.ROLLBACK)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("prism", "0076_encrypt_webhook_credentials")]
    operations = [
        migrations.RunPython(verify_before_remove, backwards),
        migrations.RemoveField("agentccwebhook", "headers"),
        migrations.RemoveField("agentccwebhook", "secret"),
        migrations.AlterField(
            "agentccwebhook", "encrypted_headers", models.BinaryField(editable=False)
        ),
        migrations.AlterField(
            "agentccwebhook", "encrypted_secret", models.BinaryField(editable=False)
        ),
        migrations.AlterField(
            "agentccwebhook",
            "credential_storage_version",
            models.PositiveSmallIntegerField(editable=False),
        ),
        migrations.AddConstraint(
            "agentccwebhook",
            models.CheckConstraint(
                condition=models.Q(credential_storage_version=1),
                name="webhook_credentials_v1",
            ),
        ),
        migrations.AddConstraint(
            "agentccwebhook",
            models.CheckConstraint(
                condition=~models.Q(encrypted_headers=b"")
                & ~models.Q(encrypted_secret=b""),
                name="webhook_credentials_nonempty",
            ),
        ),
        migrations.RunPython(check_after_remove, backwards),
    ]
