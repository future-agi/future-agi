from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_mcp_credential",
    ],
    "mitigation": (
        "On agentcc_mcp_credential rows with scope=user, kind=oauth and "
        "status=revoked, sets the new gateway_purge_pending column to true, and also "
        "sets the new oauth_revoke_pending column to true where encrypted_refresh is "
        "neither null nor empty. Two UPDATE statements in the migration's transaction, "
        "after the boolean columns are added with a database default of false. "
        "Replay-safe: both statements set a fixed value on a fixed filter, so a re-run "
        "changes nothing. Reverse is a no-op. Row scale: the revoked personal OAuth "
        "MCP credentials of all organizations."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def preserve_existing_cleanup(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        credential = apps.get_model("prism", "AgentccMCPCredential")
        revoked = credential.objects.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)).filter(
            scope="user", kind="oauth", status="revoked"
        )
        # Older generic revoke and failed-purge paths recorded no cleanup receipt.
        # Preserve a retry action instead of assuming those caches were evicted.
        revoked.update(gateway_purge_pending=True)
        revoked.exclude(encrypted_refresh__isnull=True).exclude(
            encrypted_refresh=""
        ).update(oauth_revoke_pending=True)


class Migration(migrations.Migration):
    dependencies = [("prism", "0060_mcp_connect_shared_mode")]
    operations = [
        migrations.AddField(
            model_name="agentccmcpconnectstate",
            name="individual_authenticated",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AddField(
            model_name="agentccmcpcredential",
            name="oauth_revoke_pending",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.AddField(
            model_name="agentccmcpcredential",
            name="gateway_purge_pending",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.RunPython(preserve_existing_cleanup, migrations.RunPython.noop),
    ]
