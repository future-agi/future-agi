import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0031_orgapikey_coding_agent"),
        ("simulate", "0103_harness_job_verify_of"),
    ]

    operations = [
        migrations.CreateModel(
            name="HostedSandboxLifetime",
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
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("chat", "Chat"),
                            ("platform_check", "Platform check"),
                            ("fix_engine", "Fix engine"),
                        ],
                        max_length=16,
                    ),
                ),
                ("provider", models.CharField(max_length=32)),
                ("provider_ref", models.CharField(max_length=255)),
                ("owner_id", models.UUIDField()),
                ("started_at", models.DateTimeField()),
                ("lifetime_seconds", models.PositiveIntegerField()),
                ("armed_at", models.DateTimeField()),
                ("lifetime_ends_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(null=True)),
                ("end_reason", models.CharField(blank=True, default="", max_length=64)),
                (
                    "failure_domain",
                    models.CharField(blank=True, default="", max_length=32),
                ),
                (
                    "billed_minutes",
                    models.DecimalField(decimal_places=4, max_digits=12, null=True),
                ),
                ("settled_at", models.DateTimeField(null=True)),
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
                "db_table": "simulate_hosted_sandbox_lifetime",
                "indexes": [
                    models.Index(
                        condition=models.Q(("ended_at__isnull", True)),
                        fields=["lifetime_ends_at"],
                        name="idx_hsandbox_open",
                    ),
                    models.Index(
                        condition=models.Q(
                            ("ended_at__isnull", False), ("settled_at__isnull", True)
                        ),
                        fields=["updated_at"],
                        name="idx_hsandbox_unsettled",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("provider_ref", "provider"),
                        name="uniq_hosted_sandbox_lifetime_ref",
                    )
                ],
            },
        ),
    ]
