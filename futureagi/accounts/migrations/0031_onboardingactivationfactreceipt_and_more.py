import uuid

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0030_onboarding_paid_cloud_activation_export_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="OnboardingActivationFactReceipt",
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
                ("export_log_id", models.UUIDField(db_index=True)),
                ("idempotency_key", models.CharField(db_index=True, max_length=220)),
                ("schema_version", models.CharField(db_index=True, max_length=96)),
                (
                    "event_cursor",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                ("organization_id_value", models.UUIDField(db_index=True)),
                ("workspace_id_value", models.UUIDField(db_index=True)),
                (
                    "user_id_value",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                (
                    "deployment_mode",
                    models.CharField(blank=True, default="", max_length=16),
                ),
                (
                    "deployment_region",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=16
                    ),
                ),
                (
                    "plan_tier",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                (
                    "activation_stage",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=96
                    ),
                ),
                (
                    "primary_path",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=32
                    ),
                ),
                ("is_activated", models.BooleanField(db_index=True, default=False)),
                (
                    "lifecycle_campaign_key",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=96
                    ),
                ),
                (
                    "lifecycle_template_key",
                    models.CharField(blank=True, default="", max_length=96),
                ),
                (
                    "lifecycle_status",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "email_next_key",
                    models.CharField(blank=True, default="", max_length=96),
                ),
                ("email_eligible", models.BooleanField(db_index=True, default=False)),
                ("email_suppressed", models.BooleanField(db_index=True, default=False)),
                (
                    "journey_config_schema_version",
                    models.CharField(blank=True, default="", max_length=96),
                ),
                (
                    "primary_cohort_key",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=96
                    ),
                ),
                ("cohort_keys", models.JSONField(blank=True, default=list)),
                ("journey_cohorts", models.JSONField(blank=True, default=list)),
                ("payload_hash", models.CharField(db_index=True, max_length=64)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("evaluated_at", models.DateTimeField(db_index=True)),
                (
                    "received_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "accounts_onboarding_activation_fact_receipt",
                "ordering": ("-received_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["workspace_id_value", "-evaluated_at"],
                        name="onb_fact_ws_eval",
                    ),
                    models.Index(
                        fields=["user_id_value", "-evaluated_at"],
                        name="onb_fact_user_eval",
                    ),
                    models.Index(
                        fields=["activation_stage", "primary_path"],
                        name="onb_fact_stage_path",
                    ),
                    models.Index(
                        fields=["primary_cohort_key", "-evaluated_at"],
                        name="onb_fact_cohort_eval",
                    ),
                    models.Index(
                        fields=["lifecycle_campaign_key", "-evaluated_at"],
                        name="onb_fact_campaign_eval",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=("idempotency_key",),
                        name="onb_fact_unique_idemp",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="OnboardingActivationFactReceiptRejection",
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
                ("reason", models.CharField(db_index=True, max_length=96)),
                ("message", models.CharField(blank=True, default="", max_length=240)),
                (
                    "export_log_id",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                (
                    "idempotency_key",
                    models.CharField(blank=True, default="", max_length=220),
                ),
                (
                    "schema_version",
                    models.CharField(blank=True, default="", max_length=96),
                ),
                (
                    "payload_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "db_table": "accounts_onboarding_activation_fact_receipt_rejection",
                "ordering": ("-received_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["reason", "-received_at"], name="onb_fact_rej_reason_ts"
                    ),
                    models.Index(
                        fields=["idempotency_key", "-received_at"],
                        name="onb_fact_rej_idemp_ts",
                    ),
                ],
            },
        ),
    ]
