from django.db import migrations

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:simulate_agent_definition",
        "postgres:simulate_agent_version",
        "postgres:simulate_provider_credentials",
    ],
    "mitigation": (
        "forward runs two data steps outside a transaction (atomic=False) on the "
        "migration's own connection. The first reads each "
        "simulate_provider_credentials row that has an agent definition and no agent "
        "version, joined to simulate_agent_definition (select_related), looks up that "
        "definition's newest simulate_agent_version by version_number and, when one "
        "exists, points the credentials row at it with one UPDATE. The second removes "
        "the api_key, livekit_api_key and livekit_api_secret keys from every "
        "simulate_agent_version configuration_snapshot that holds one, with one UPDATE "
        "per changed row. Both read with iterator(chunk_size=500). A database that "
        "applied 1.58.0 already has this migration recorded and does not run it again. "
        "On a fresh install these tables have no rows when it runs, so nothing is "
        "written. Safe to run again: work done before a failure stays done, the first "
        "step selects only credentials with no agent version yet, and the second skips "
        "snapshots with none of those keys, so a rerun continues where a failed run "
        "stopped. The first step's reverse moves each credentials row that has an "
        "agent version and no agent definition to that version's agent definition, "
        "clearing its agent version and first unlinking any other credentials row from "
        "that definition; rows the forward step pointed at a version keep it. The "
        "second's reverse is a no-op, so removed key values are not restored. Row "
        "scale: one row per provider credential and one per agent version; none of "
        "these is a registered high-volume table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


BATCH_SIZE = 500
CREDENTIAL_FIELDS = frozenset({"api_key", "livekit_api_key", "livekit_api_secret"})


def backfill_agent_version(apps, schema_editor):
    ProviderCredentials = apps.get_model("simulate", "ProviderCredentials")
    AgentVersion = apps.get_model("simulate", "AgentVersion")

    qs = ProviderCredentials.objects.using(schema_editor.connection.alias).filter(
        agent_definition__isnull=False,
        agent_version__isnull=True,
    ).select_related("agent_definition")
    total = qs.count()
    processed = 0
    for creds in qs.iterator(chunk_size=BATCH_SIZE):
        version = (
            AgentVersion.objects.using(schema_editor.connection.alias).filter(agent_definition=creds.agent_definition)
            .order_by("-version_number")
            .first()
        )
        if version:
            ProviderCredentials.objects.using(schema_editor.connection.alias).filter(pk=creds.pk).update(
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

    qs = AgentVersion.objects.using(schema_editor.connection.alias).all()
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
        AgentVersion.objects.using(schema_editor.connection.alias).filter(pk=version.pk).update(
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
