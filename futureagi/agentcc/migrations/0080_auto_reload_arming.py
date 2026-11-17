"""Add an empty gateway arming sidecar; no money or consent backfill."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0079_auto_reload_payment_method"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccAutoReloadArming",
            fields=[
                (
                    "organization",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="agentcc_auto_reload_arming",
                        serialize=False,
                        to="accounts.organization",
                    ),
                ),
                ("epoch", models.PositiveBigIntegerField(default=0)),
                ("below_threshold", models.BooleanField(default=False)),
                ("intent_id", models.CharField(blank=True, default="", max_length=64)),
            ],
            options={"db_table": "agentcc_auto_reload_arming"},
        ),
    ]
