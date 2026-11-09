"""Fenced release only: stop ALL old writers before running M1 -> M2 -> M3.

Bounded transactions permit resume after a failed batch. Never decrypt data back
into the old column on reverse; recovery is the reviewed pre-upgrade backup.
"""

from django.conf import settings
from django.db import migrations, transaction
from django.db.migrations.exceptions import IrreversibleError

from integrations.services.credentials import CredentialManager
from simulate.services.agent_api_key_storage import decrypt_api_key, encrypt_api_key
from tfc.migration_database import executor_alias_scope

BATCH_SIZE = 250


def forwards(apps, schema_editor):
    # Frozen preflight, including empty/replayed tables: no committed batch may
    # clear plaintext using a process-local key that will die on restart.
    try:
        if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
            raise ValueError
        CredentialManager._get_fernet()
    except Exception:
        raise ValueError(
            "Agent credential migration requires a valid persistent "
            "INTEGRATION_ENCRYPTION_KEY shared by all processes and restarts; "
            "configure it before retrying (not an ephemeral key)"
        ) from None
    with executor_alias_scope(schema_editor):
        using = schema_editor.connection.alias
        agent = apps.get_model("simulate", "AgentDefinition")
        rows = agent._base_manager.using(using)
        last_pk = None
        cleared_count = 0
        while True:
            with transaction.atomic(using=using):
                batch = rows.order_by("pk")
                if last_pk is not None:
                    batch = batch.filter(pk__gt=last_pk)
                batch = list(batch.select_for_update()[:BATCH_SIZE])
                if not batch:
                    break
                for row in batch:
                    legacy = row.api_key
                    encrypted = row.encrypted_api_key
                    if encrypted:
                        clear = decrypt_api_key(encrypted)
                        if legacy not in (None, "") and legacy != clear:
                            raise ValueError("Agent credential migration conflict")
                    elif legacy not in (None, ""):
                        encrypted = encrypt_api_key(legacy)
                    else:
                        encrypted = None
                    # Authenticate before clearing; exact raw strings, no strip,
                    # base64 decode, prefix trust, or ProviderCredentials writes.
                    if encrypted:
                        clear = decrypt_api_key(encrypted)
                        if legacy not in (None, "") and clear != legacy:
                            raise ValueError("Agent credential migration conflict")
                    if legacy is not None or encrypted != row.encrypted_api_key:
                        rows.filter(pk=row.pk).update(
                            encrypted_api_key=encrypted, api_key=None
                        )
                        cleared_count += 1
                last_pk = batch[-1].pk
        # Full unfiltered verification, still alias-bound and memory-bounded.
        # The release fence forbids old writers inserting behind the PK cursor.
        verified_count = 0
        for row in rows.order_by("pk").iterator(chunk_size=BATCH_SIZE):
            if row.api_key is not None:
                raise ValueError("Agent credential migration incomplete")
            decrypt_api_key(row.encrypted_api_key)
            verified_count += 1
        print(
            f"Agent credential migration: verified {verified_count} rows; "
            f"cleared {cleared_count} legacy values"
        )


def backwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        using = schema_editor.connection.alias
        agent = apps.get_model("simulate", "AgentDefinition")
        if (
            agent._base_manager.using(using)
            .exclude(encrypted_api_key__isnull=True)
            .exclude(encrypted_api_key="")
            .exists()
        ):
            raise IrreversibleError(
                "Cannot remove encrypted agent credentials; use reviewed backup recovery"
            )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("simulate", "0100_agent_definition_encrypted_api_key")]
    operations = [migrations.RunPython(forwards, backwards, atomic=False)]
