import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0103_harness_job_verify_of"),
    ]

    operations = [
        migrations.CreateModel(
            name="HostedHarnessBuilderTurnUsage",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("outcome", models.CharField(default="", max_length=16)),
                ("usage", models.JSONField(default=dict)),
                ("report", models.JSONField(blank=True, null=True)),
                (
                    "command_message",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="builder_turn_usage",
                        to="simulate.hostedharnessconversationmessage",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="builder_turn_usages",
                        to="simulate.hostedharnessjob",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_hosted_harness_builder_turn_usage",
                "indexes": [
                    models.Index(fields=["job"], name="idx_hbturn_job"),
                ],
            },
        ),
    ]
