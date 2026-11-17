"""Keep Disconnect cancellation independent of one-use OAuth state rows."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0070_merge_mcp_line_siem_hec"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="AgentccMCPConnectGeneration",
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
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "agentcc_mcp_connect_generation",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "user", "server_id"),
                        name="agentcc_mcp_connect_generation_identity",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="agentccmcpconnectstate",
            name="cancellation_generation",
            field=models.PositiveBigIntegerField(default=0, db_default=0),
        ),
    ]
