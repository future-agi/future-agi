"""Keep prior consent handles when a connection is superseded."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0065_mcp_vendor_optimistic_lifecycle")]

    operations = [
        migrations.CreateModel(
            name="AgentccMCPVendorCleanup",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("consent_generation", models.PositiveIntegerField()),
                (
                    "vendor_account_ref",
                    models.CharField(blank=True, default="", max_length=256),
                ),
                ("encrypted_session", models.TextField(blank=True, null=True)),
                ("outcome", models.CharField(default="pending", max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cleanup_obligations",
                        to="prism.agentccmcpvendorconnection",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("connection", "consent_generation"),
                        name="mcp_vendor_cleanup_generation",
                    )
                ]
            },
        ),
    ]
