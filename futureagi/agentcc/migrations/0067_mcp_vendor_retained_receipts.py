"""Keep cleanup evidence after a binding and its connections are deleted."""

import django.db.models.deletion
from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:prism_agentccmcpvendorbinding",
        "postgres:prism_agentccmcpvendorconnection",
        "postgres:prism_agentccmcpvendorreceipt",
    ],
    "mitigation": (
        "Copies onto every prism_agentccmcpvendorreceipt row its connection's id and "
        "consent_generation and its binding's organization_id and server_id, read with "
        "one joined query through prism_agentccmcpvendorconnection and "
        "prism_agentccmcpvendorbinding, saving only those four new columns: one UPDATE "
        "per receipt, in the migration's transaction. The receipt's connection foreign "
        "key then becomes nullable with ON DELETE SET NULL. Replay-safe: the copied "
        "values come from the same rows, so a re-run writes the same values. Reverse "
        "is a no-op for the data step. Row scale: all vendor receipt rows of all "
        "organizations."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def snapshot_receipts(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Receipt = apps.get_model("prism", "AgentccMCPVendorReceipt")
        for receipt in (
            Receipt.objects.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
            .select_related("connection__binding")
            .iterator()
        ):
            connection = receipt.connection
            receipt.organization_id = connection.binding.organization_id
            receipt.server_id = connection.binding.server_id
            receipt.connection_ref = connection.pk
            receipt.consent_generation = connection.consent_generation
            receipt.save(
                using=(schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS),
                update_fields=[
                    "organization_id",
                    "server_id",
                    "connection_ref",
                    "consent_generation",
                ]
            )


class Migration(migrations.Migration):
    dependencies = [("prism", "0066_mcp_vendor_cleanup_obligations")]
    operations = [
        migrations.AddField(
            model_name="agentccmcpvendorreceipt",
            name="organization_id",
            field=models.UUIDField(null=True),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorreceipt",
            name="connection_ref",
            field=models.UUIDField(null=True),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorreceipt",
            name="server_id",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="agentccmcpvendorreceipt",
            name="consent_generation",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(snapshot_receipts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="agentccmcpvendorreceipt",
            name="connection",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="receipts",
                to="prism.agentccmcpvendorconnection",
            ),
        ),
    ]
