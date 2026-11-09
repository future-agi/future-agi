"""M1: additive expansion inside ONE fenced release; never serve this state."""

from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError

from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import executor_alias_scope


def forwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        storage.closed_call(storage.expansion_check, apps, schema_editor.connection)


def backwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # M2 may have committed batches without being recorded applied. Refuse
        # BEFORE AddField reversal could discard that retained ciphertext.
        raise IrreversibleError(storage.ROLLBACK)


class Migration(migrations.Migration):
    dependencies = [("prism", "0074_api_key_provisioning_state")]
    operations = [
        migrations.AddField(
            "agentccwebhook",
            "encrypted_headers",
            models.BinaryField(null=True, editable=False),
        ),
        migrations.AddField(
            "agentccwebhook",
            "encrypted_secret",
            models.BinaryField(null=True, editable=False),
        ),
        migrations.AddField(
            "agentccwebhook",
            "credential_storage_version",
            models.PositiveSmallIntegerField(null=True, editable=False),
        ),
        migrations.RunPython(forwards, backwards),
    ]
