import hashlib

from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_mcp_approval",
    ],
    "mitigation": (
        "Sets approval_digest to the SHA-256 hex digest of approval_id on every "
        "agentcc_mcp_approval row, one save per row, after the nullable column is "
        "added; the column then becomes NOT NULL and unique, and approval_id becomes a "
        "text column. All in the migration's transaction, so a failure, for example "
        "two rows with the same approval_id, rolls back the whole migration. "
        "Replay-safe: the digest is a pure function of approval_id, so a re-run writes "
        "the same values. Reverse is a no-op for the data step. Row scale: one row per "
        "recorded MCP approval, all organizations."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def fill_digests(apps, schema_editor):
    Approval = apps.get_model("prism", "AgentccMCPApproval")
    for row in Approval.objects.using(schema_editor.connection.alias).all().iterator():
        row.approval_digest = hashlib.sha256(row.approval_id.encode()).hexdigest()
        row.save(update_fields=["approval_digest"])


class Migration(migrations.Migration):
    dependencies = [("prism", "0074_api_key_provisioning_state")]
    operations = [
        migrations.AddField(
            model_name="agentccmcpapproval",
            name="approval_digest",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(fill_digests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agentccmcpapproval",
            name="approval_digest",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name="agentccmcpapproval",
            name="approval_id",
            field=models.TextField(),
        ),
    ]
