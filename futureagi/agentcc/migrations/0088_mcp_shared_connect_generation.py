"""Add durable org/server Disconnect generations without changing USER identities."""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0087_mcp_credential_oauth_resource"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccMCPSharedConnectGeneration",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("server_id", models.CharField(max_length=64)),
                ("generation", models.PositiveBigIntegerField(default=0)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "db_table": "agentcc_mcp_shared_connect_generation",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "server_id"),
                        name="agentcc_mcp_shared_connect_generation_identity",
                    )
                ],
            },
        ),
    ]
