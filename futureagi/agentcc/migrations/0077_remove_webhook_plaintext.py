"""M3: reverify ALL originals; atomically remove plaintext LAST and constrain.

Requires PostgreSQL transactional DDL and the same maintenance fence as M2.
Logical DROP does not erase historic WAL, snapshots, replicas or backups.
"""

from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError

from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import executor_alias_scope


def verify_before_remove(apps, schema_editor):
    with executor_alias_scope(schema_editor):
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
