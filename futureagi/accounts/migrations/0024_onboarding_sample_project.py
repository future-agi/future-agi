import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_onboarding_goal"),
    ]

    operations = [
        migrations.CreateModel(
            name="OnboardingSampleProject",
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
                ("manifest_id", models.CharField(max_length=96)),
                ("manifest_version", models.CharField(max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("not_created", "not_created"),
                            ("creating", "creating"),
                            ("ready_for_observe", "ready_for_observe"),
                            ("partially_ready", "partially_ready"),
                            ("ready", "ready"),
                            ("hidden", "hidden"),
                            ("unavailable", "unavailable"),
                            ("repair_failed", "repair_failed"),
                        ],
                        db_index=True,
                        default="not_created",
                        max_length=32,
                    ),
                ),
                ("artifact_refs", models.JSONField(blank=True, default=dict)),
                ("missing_artifacts", models.JSONField(blank=True, default=list)),
                ("health", models.JSONField(blank=True, default=dict)),
                ("idempotency_key", models.CharField(db_index=True, max_length=180)),
                ("repair_attempts", models.PositiveIntegerField(default=0)),
                (
                    "last_repair_attempt_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("last_opened_at", models.DateTimeField(blank=True, null=True)),
                (
                    "hidden_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "first_opened_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="first_opened_onboarding_sample_projects",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "last_opened_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="last_opened_onboarding_sample_projects",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_sample_projects",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="onboarding_sample_projects",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_onboarding_sample_project",
                "ordering": ("-last_opened_at", "-updated_at"),
                "indexes": [
                    models.Index(
                        fields=["organization", "workspace", "manifest_id"],
                        name="onb_sample_org_ws_manifest",
                    ),
                    models.Index(
                        fields=["workspace", "status"],
                        name="onb_sample_ws_status",
                    ),
                    models.Index(
                        fields=["workspace", "hidden_at"],
                        name="onb_sample_ws_hidden",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=("workspace", "manifest_id", "manifest_version"),
                        name="onb_sample_unique_manifest",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=("idempotency_key",),
                        name="onb_sample_unique_idempotency",
                    ),
                ],
            },
        ),
    ]
