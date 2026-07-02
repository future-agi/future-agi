from django.db import migrations

BATCH_SIZE = 500
CREDENTIAL_FIELDS = frozenset({"api_key", "livekit_api_key", "livekit_api_secret"})


def backfill_agent_version(apps, schema_editor):
    ProviderCredentials = apps.get_model("simulate", "ProviderCredentials")
    AgentVersion = apps.get_model("simulate", "AgentVersion")

    qs = ProviderCredentials.objects.filter(
        agent_definition__isnull=False,
        agent_version__isnull=True,
    ).select_related("agent_definition")
    total = qs.count()
    processed = 0
    for creds in qs.iterator(chunk_size=BATCH_SIZE):
        version = (
            AgentVersion.objects.filter(agent_definition=creds.agent_definition)
            .order_by("-version_number")
            .first()
        )
        if version:
            ProviderCredentials.objects.filter(pk=creds.pk).update(
                agent_version=version
            )
        processed += 1
        if processed % BATCH_SIZE == 0 and processed < total:
            schema_editor.connection.commit()


def reverse_backfill_agent_version(apps, schema_editor):
    """Move credentials back from agent_version FK to agent_definition FK.

    Clears any credential that already holds the target agent_definition_id
    first (the FK is a OneToOneField, so only one can exist per agent def).
    The displaced credential is kept as version-only rather than orphaned.
    """
    ProviderCredentials = apps.get_model("simulate", "ProviderCredentials")

    qs = ProviderCredentials.objects.filter(
        agent_version__isnull=False,
        agent_definition__isnull=True,
    ).select_related("agent_version")
    total = qs.count()
    processed = 0
    for creds in qs.iterator(chunk_size=BATCH_SIZE):
        agent_def_id = creds.agent_version.agent_definition_id
        # Displace any existing credential holding this agent_definition_id
        # (OneToOneField constraint allows only one). Keep it as version-only
        # rather than orphaning it.
        ProviderCredentials.objects.filter(
            agent_definition_id=agent_def_id
        ).exclude(pk=creds.pk).update(
            agent_definition=None,
        )
        ProviderCredentials.objects.filter(pk=creds.pk).update(
            agent_version=None,
            agent_definition_id=agent_def_id,
        )
        processed += 1
        if processed % BATCH_SIZE == 0 and processed < total:
            schema_editor.connection.commit()


def remove_credential_fields_from_snapshots(apps, schema_editor):
    AgentVersion = apps.get_model("simulate", "AgentVersion")

    qs = AgentVersion.objects.all()
    total = qs.count()
    processed = 0
    for version in qs.iterator(chunk_size=BATCH_SIZE):
        snapshot = version.configuration_snapshot
        if not snapshot:
            processed += 1
            continue
        if not any(k in snapshot for k in CREDENTIAL_FIELDS):
            processed += 1
            continue
        for key in CREDENTIAL_FIELDS:
            snapshot.pop(key, None)
        AgentVersion.objects.filter(pk=version.pk).update(
            configuration_snapshot=snapshot
        )
        processed += 1
        if processed % BATCH_SIZE == 0 and processed < total:
            schema_editor.connection.commit()


# NOTE: reverse of remove_credential_fields_from_snapshots is intentionally
# noop — credential values were removed from the snapshot JSON and cannot be
# reconstructed. The canonical source is now ProviderCredentials.


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("simulate", "0075_add_agent_version_to_provider_credentials"),
    ]

    operations = [
        migrations.RunPython(
            backfill_agent_version,
            reverse_code=reverse_backfill_agent_version,
            atomic=False,
        ),
        migrations.RunPython(
            remove_credential_fields_from_snapshots,
            reverse_code=migrations.RunPython.noop,
            atomic=False,
        ),
    ]
