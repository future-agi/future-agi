"""Permanently retire credentials identified by Enterprise provisioning records."""

from django.db import DEFAULT_DB_ALIAS, migrations
from django.utils import timezone

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_api_key",
        "postgres:agentcc_ee_agent_key",
        "postgres:agentcc_ee_service_credential",
    ],
    "mitigation": (
        "Sets status=revoked and revoked_at on every agentcc_ee_service_credential row "
        "not already revoked, then sets status=revoked and updated_at on every "
        "agentcc_api_key row whose id appears in agentcc_ee_agent_key.api_key_id and "
        "is not already revoked. Two UPDATE statements in the migration's transaction. "
        "Replay-safe: rows already revoked are excluded, so a re-run changes nothing. "
        "Reverse is a no-op: the rows stay revoked. Row scale: the Enterprise service "
        "credentials and the Enterprise-bound API keys of all organizations."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def retire_credentials(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        credentials = apps.get_model("prism", "AgentccEEServiceCredential")
        bindings = apps.get_model("prism", "AgentccEEAgentKey")
        keys = apps.get_model("prism", "AgentccAPIKey")
        now = timezone.now()
        credentials.objects.using(alias).exclude(status="revoked").update(
            status="revoked", revoked_at=now
        )
        # The authoritative binding includes older rows without an issuer or user.
        bound_ids = bindings.objects.using(alias).values_list("api_key_id", flat=True)
        keys._base_manager.using(alias).filter(pk__in=bound_ids).exclude(
            status="revoked"
        ).update(status="revoked", updated_at=now)


class Migration(migrations.Migration):
    dependencies = [("prism", "0057_mcp_credential_origin")]
    # Credentials stay retired across an image rollback. Recovery is not reissuance.
    operations = [migrations.RunPython(retire_credentials, migrations.RunPython.noop)]
