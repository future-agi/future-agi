"""M2: resumable all-row encryption, then complete verification; NO clearing.

Fence/drain all old readers and writers before M1 and keep the fence through M3.
Rollback is the reviewed pre-upgrade backup, never plaintext reconstruction.
"""

from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError

from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import executor_alias_scope


def forwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        storage.closed_call(storage.convert, apps, schema_editor.connection)


def backwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        raise IrreversibleError(storage.ROLLBACK)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("prism", "0075_webhook_credential_columns")]
    operations = [migrations.RunPython(forwards, backwards, atomic=False)]
