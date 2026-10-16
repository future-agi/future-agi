"""Give agents whose latest version records another provider a current version.

Provider edits used to change the agent in place. Calls now report the provider
of the version they pin, and new calls pin the latest version, so these agents
would report their old provider, or none, on every new call.
"""

from django.db import migrations, transaction
from django.db.models import Max

CREDENTIAL_FIELDS = (
    "provider_type",
    "api_key",
    "api_secret",
    "assistant_id",
    "server_url",
    "agent_name",
    "config_json",
    "max_concurrency",
)


def version_agents_with_a_stale_provider(apps, schema_editor):
    AgentDefinition = apps.get_model("simulate", "AgentDefinition")
    AgentVersion = apps.get_model("simulate", "AgentVersion")
    ProviderCredentials = apps.get_model("simulate", "ProviderCredentials")
    versioned = AgentVersion._base_manager.filter(deleted=False).values(
        "agent_definition_id"
    )
    agents = AgentDefinition._base_manager.filter(deleted=False, id__in=versioned).only(
        "id", "provider"
    )
    for agent in agents.iterator(chunk_size=500):
        versions = AgentVersion._base_manager.filter(agent_definition_id=agent.id)
        latest = versions.filter(deleted=False).order_by("-version_number").first()
        snapshot = latest.configuration_snapshot or {}
        provider = agent.provider or ""
        if (snapshot.get("provider") or "") == provider:
            continue
        # Deleted versions still hold their numbers.
        number = versions.aggregate(last=Max("version_number"))["last"] + 1
        with transaction.atomic():
            version = AgentVersion._base_manager.create(
                agent_definition_id=agent.id,
                organization_id=latest.organization_id,
                workspace_id=latest.workspace_id,
                version_number=number,
                version_name=f"v{number}",
                status=latest.status,
                description=latest.description,
                commit_message="Provider synced to agent",
                configuration_snapshot={**snapshot, "provider": provider},
            )
            credentials = ProviderCredentials._base_manager.filter(
                agent_version_id=latest.id
            ).first()
            if credentials is not None:
                ProviderCredentials._base_manager.create(
                    agent_version_id=version.id,
                    **{
                        field: getattr(credentials, field)
                        for field in CREDENTIAL_FIELDS
                    },
                )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("simulate", "0097_merge_selected_runs_environment_v3"),
    ]

    operations = [
        migrations.RunPython(
            version_agents_with_a_stale_provider, migrations.RunPython.noop
        ),
    ]
