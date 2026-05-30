import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0029_onboarding_quality_action_due_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="OnboardingPaidCloudActivationExportLog",
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
                    "deployment_mode",
                    models.CharField(
                        choices=[
                            ("cloud", "cloud"),
                            ("ee", "ee"),
                            ("oss", "oss"),
                        ],
                        max_length=16,
                    ),
                ),
                ("run_id", models.UUIDField(db_index=True)),
                ("region", models.CharField(blank=True, default="", max_length=16)),
                ("plan_tier", models.CharField(blank=True, default="", max_length=64)),
                ("schema_version", models.CharField(max_length=96)),
                ("event_cursor", models.CharField(max_length=160)),
                ("idempotency_key", models.CharField(db_index=True, max_length=220)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ready", "ready"),
                            ("suppressed", "suppressed"),
                            ("exported", "exported"),
                            ("failed", "failed"),
                        ],
                        db_index=True,
                        default="ready",
                        max_length=16,
                    ),
                ),
                (
                    "suppression_reason",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("fact_payload", models.JSONField(blank=True, default=dict)),
                (
                    "exported_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "evaluated_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_paid_cloud_activation_export_logs",
                        to="accounts.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="onboarding_paid_cloud_activation_export_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_paid_cloud_activation_export_logs",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_onboarding_paid_cloud_activation_export_log",
                "ordering": ("-evaluated_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["status", "evaluated_at"],
                        name="onb_paid_exp_status_eval",
                    ),
                    models.Index(
                        fields=["run_id", "status"],
                        name="onb_paid_exp_run_status",
                    ),
                    models.Index(
                        fields=["workspace", "status", "-evaluated_at"],
                        name="onb_paid_exp_ws_status",
                    ),
                    models.Index(
                        fields=["workspace", "event_cursor"],
                        name="onb_paid_exp_ws_cursor",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=("organization", "workspace", "idempotency_key"),
                        name="onb_paid_exp_unique_idemp",
                    )
                ],
            },
        ),
    ]
