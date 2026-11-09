from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope


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
