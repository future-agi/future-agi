import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_onboarding_sample_project"),
    ]

    operations = [
        migrations.CreateModel(
            name="OnboardingLifecycleEvaluationLog",
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
                ("run_id", models.UUIDField(db_index=True)),
                (
                    "campaign_key",
                    models.CharField(blank=True, max_length=96, null=True),
                ),
                (
                    "campaign_group",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                (
                    "template_key",
                    models.CharField(blank=True, max_length=96, null=True),
                ),
                (
                    "template_version",
                    models.CharField(blank=True, max_length=32, null=True),
                ),
                ("activation_stage", models.CharField(max_length=96)),
                (
                    "primary_path",
                    models.CharField(blank=True, max_length=32, null=True),
                ),
                (
                    "recommendation_id",
                    models.CharField(blank=True, max_length=96, null=True),
                ),
                (
                    "target_action_id",
                    models.CharField(blank=True, max_length=96, null=True),
                ),
                (
                    "target_success_event",
                    models.CharField(blank=True, max_length=96, null=True),
                ),
                ("target_url", models.TextField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("eligible", "eligible"),
                            ("suppressed", "suppressed"),
                            ("skipped", "skipped"),
                            ("error", "error"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "suppression_reason",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("suppression_details", models.JSONField(blank=True, default=dict)),
                ("eligible_at", models.DateTimeField(blank=True, null=True)),
                (
                    "evaluated_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now
                    ),
                ),
                (
                    "activation_state_snapshot",
                    models.JSONField(blank=True, default=dict),
                ),
                ("registry_snapshot", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_evaluations",
                        to="accounts.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_evaluations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_evaluations",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_onboarding_lifecycle_evaluation_log",
                "ordering": ("-evaluated_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["user", "-evaluated_at"], name="onb_life_user_ts"
                    ),
                    models.Index(
                        fields=["workspace", "-evaluated_at"], name="onb_life_ws_ts"
                    ),
                    models.Index(
                        fields=["workspace", "campaign_key", "-evaluated_at"],
                        name="onb_life_ws_campaign_ts",
                    ),
                    models.Index(
                        fields=["workspace", "status", "-evaluated_at"],
                        name="onb_life_ws_status_ts",
                    ),
                    models.Index(
                        fields=["campaign_key", "status", "-evaluated_at"],
                        name="onb_life_campaign_status",
                    ),
                    models.Index(
                        fields=["suppression_reason", "-evaluated_at"],
                        name="onb_life_reason_ts",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("run_id", "user", "workspace", "campaign_key"),
                        name="onb_life_unique_run_campaign",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="OnboardingLifecyclePreference",
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
                ("onboarding_enabled", models.BooleanField(default=True)),
                ("first_action_recovery_enabled", models.BooleanField(default=True)),
                ("sample_bridge_enabled", models.BooleanField(default=True)),
                ("next_loop_enabled", models.BooleanField(default=True)),
                ("daily_digest_enabled", models.BooleanField(default=False)),
                ("reactivation_enabled", models.BooleanField(default=False)),
                (
                    "snoozed_until",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "unsubscribed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_preferences",
                        to="accounts.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_lifecycle_preferences",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_onboarding_lifecycle_preference",
                "ordering": ("-updated_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["user", "organization"], name="onb_life_pref_user_org"
                    ),
                    models.Index(
                        fields=["user", "organization", "workspace"],
                        name="onb_life_pref_user_org_ws",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("deleted", False), ("workspace__isnull", False)
                        ),
                        fields=("user", "organization", "workspace"),
                        name="onb_life_pref_unique_ws",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("deleted", False), ("workspace__isnull", True)
                        ),
                        fields=("user", "organization"),
                        name="onb_life_pref_unique_user",
                    ),
                ],
            },
        ),
    ]
