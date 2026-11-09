import uuid

from django.db import DEFAULT_DB_ALIAS, migrations, models
import django.db.models.deletion

from tfc.migration_database import executor_alias_scope


def pending_receipts(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Credential = apps.get_model("prism", "AgentccMCPCredential")
        Receipt = apps.get_model("prism", "AgentccMCPCleanupReceipt")
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        rows = Credential.objects.using(alias).filter(kind="oauth").filter(
            models.Q(oauth_revoke_pending=True) | models.Q(gateway_purge_pending=True)
        )
        for row in rows.iterator():
            if row.oauth_revoke_pending:
                Credential.objects.using(alias).filter(pk=row.pk).update(oauth_revoke_outcome="pending")
            for step, pending in (("vendor_revocation", row.oauth_revoke_pending),
                                  ("gateway_purge", row.gateway_purge_pending)):
                if pending:
                    Receipt.objects.using(alias).create(
                        credential_id=row.pk, trigger="migration", step=step,
                        outcome="pending", reason="recorded",
                    )


class Migration(migrations.Migration):
    dependencies = [("prism", "0061_mcp_personal_principal_cleanup")]

    operations = [
        migrations.AddField(
            model_name="agentccmcpcredential", name="oauth_revoke_outcome",
            field=models.CharField(
                max_length=16, choices=[("pending", "Pending"), ("confirmed", "Confirmed"),
                                        ("unconfirmed", "Unconfirmed")],
                blank=True, default="", db_default="",
            ),
        ),
        migrations.CreateModel(
            name="AgentccMCPCleanupReceipt",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("trigger", models.CharField(max_length=32)),
                ("step", models.CharField(max_length=32)),
                ("outcome", models.CharField(max_length=16)),
                ("reason", models.CharField(max_length=32, blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("credential", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="cleanup_receipts", to="prism.agentccmcpcredential",
                )),
            ],
            options={"db_table": "agentcc_mcp_cleanup_receipt", "ordering": ["created_at"]},
        ),
        migrations.RunPython(pending_receipts, migrations.RunPython.noop),
    ]
