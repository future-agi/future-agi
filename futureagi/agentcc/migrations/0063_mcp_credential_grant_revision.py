from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_mcp_credential",
    ],
    "mitigation": (
        "Numbers every agentcc_mcp_credential row with scope=shared: within each "
        "(organization_id, server_id), in created_at then id order, grant_revision is "
        "set to 1, 2, 3 and so on. One UPDATE per shared credential, in the "
        "migration's transaction, after grant_revision is added with a database "
        "default of 0. Replay-safe: the numbering is a pure function of the stored "
        "order, so a re-run writes the same values. Reverse is a no-op. Row scale: the "
        "shared MCP credentials of all organizations."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def number_shared_grants(apps, schema_editor):
    """Give every existing shared credential a grant revision, counted in its
    org and server in creation order, so a slot's active one is its newest."""
    with executor_alias_scope(schema_editor):
        Credential = apps.get_model("prism", "AgentccMCPCredential")
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        rows = (
            Credential.objects.using(alias)
            .filter(scope="shared")
            .order_by("organization_id", "server_id", "created_at", "id")
            .values_list("pk", "organization_id", "server_id")
        )
        slot, revision = None, 0
        for pk, org_id, server_id in rows.iterator():
            if (org_id, server_id) != slot:
                slot, revision = (org_id, server_id), 0
            revision += 1
            Credential.objects.using(alias).filter(pk=pk).update(grant_revision=revision)


class Migration(migrations.Migration):
    dependencies = [("prism", "0062_mcp_cleanup_receipts")]

    operations = [
        migrations.AddField(
            model_name="agentccmcpcredential",
            name="grant_revision",
            field=models.PositiveBigIntegerField(default=0, db_default=0),
        ),
        migrations.RunPython(number_shared_grants, migrations.RunPython.noop),
    ]
