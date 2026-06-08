import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_notificationchannel_notificationdeliverylog_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="OnboardingQualityAction",
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
                ("product_path", models.CharField(db_index=True, max_length=32)),
                ("action_key", models.CharField(max_length=160)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "open"),
                            ("completed", "completed"),
                            ("dismissed", "dismissed"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                ("label", models.CharField(max_length=180)),
                ("body", models.CharField(blank=True, default="", max_length=300)),
                ("route", models.TextField(blank=True, default="/dashboard/home")),
                (
                    "fallback_route",
                    models.TextField(blank=True, default="/dashboard/get-started"),
                ),
                (
                    "source_type",
                    models.CharField(
                        blank=True,
                        default="workspace",
                        max_length=64,
                    ),
                ),
                ("source_id", models.CharField(blank=True, default="", max_length=128)),
                ("is_sample", models.BooleanField(db_index=True, default=False)),
                ("opened_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("assigned_at", models.DateTimeField(blank=True, null=True)),
                (
                    "completed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "dismissed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("last_event_at", models.DateTimeField(db_index=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "assigned_to",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="assigned_onboarding_quality_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_onboarding_quality_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_quality_actions",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_quality_actions",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_onboarding_quality_action",
                "ordering": ("-last_event_at", "-created_at"),
                "indexes": [
                    models.Index(
                        fields=["organization", "workspace", "product_path", "status"],
                        name="onb_qact_org_ws_path_status",
                    ),
                    models.Index(
                        fields=["workspace", "status", "-last_event_at"],
                        name="onb_qact_ws_status_ts",
                    ),
                    models.Index(
                        fields=["workspace", "source_type", "source_id"],
                        name="onb_qact_ws_source",
                    ),
                    models.Index(
                        fields=["assigned_to", "status", "-last_event_at"],
                        name="onb_qact_assignee_status",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=(
                            "organization",
                            "workspace",
                            "product_path",
                            "action_key",
                        ),
                        name="onb_qact_unique_action",
                    )
                ],
            },
        ),
    ]
