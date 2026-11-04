import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key"),
        ("simulate", "0105_alter_harnessagentversionruntime_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentversionsetup",
            name="origin",
            field=models.CharField(
                choices=[("build", "Build"), ("reading", "Reading")],
                default="build",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="agentdefinition",
            name="found_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AgentScan",
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
                ("source", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("found", "Found"),
                            ("none", "None"),
                            ("error", "Error"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=8,
                    ),
                ),
                ("report", models.JSONField(blank=True, null=True)),
                ("failure", models.JSONField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
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
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "simulate_agent_scan",
            },
        ),
    ]
