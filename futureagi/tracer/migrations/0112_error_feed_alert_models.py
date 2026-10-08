import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "accounts",
            "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
        ),
        ("integrations", "0005_slack_integration"),
        ("tracer", "0111_grouping_budget_wait"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ErrorFeedAlertRule",
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
                ("name", models.CharField(max_length=255)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "trigger_type",
                    models.CharField(
                        choices=[
                            ("new_issue", "New issue"),
                            ("severity_reached", "Severity reached"),
                            ("escalating", "Issue escalating"),
                            ("occurrences_crossed", "Occurrences crossed"),
                        ],
                        max_length=32,
                    ),
                ),
                ("trigger_value", models.JSONField(blank=True, null=True)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("slack_channel_id", models.CharField(max_length=80)),
                ("slack_channel_name", models.CharField(max_length=255)),
                ("cooldown_seconds", models.PositiveIntegerField(default=3600)),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.project",
                    ),
                ),
                (
                    "slack_connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="integrations.integrationconnection",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_error_feed_alert_rule",
                "indexes": [
                    models.Index(
                        fields=["organization", "workspace", "enabled"],
                        name="feed_alert_scope_idx",
                    ),
                    models.Index(
                        fields=["project", "trigger_type"],
                        name="feed_alert_project_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ErrorFeedIssueEvent",
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
                ("event_kind", models.CharField(max_length=32)),
                ("source_key", models.CharField(max_length=255, unique=True)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.traceerrorgroup",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.project",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_error_feed_issue_event",
                "indexes": [
                    models.Index(
                        fields=["processed_at", "created_at"],
                        name="feed_issue_event_due_idx",
                    ),
                    models.Index(
                        fields=["cluster", "created_at"],
                        name="feed_issue_event_cluster_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ErrorFeedAlertDelivery",
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
                ("channel_id", models.CharField(max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("retry", "Retry"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("slack_ts", models.CharField(blank=True, max_length=80)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.traceerrorgroup",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.errorfeedissueevent",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.errorfeedalertrule",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_error_feed_alert_delivery",
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="feed_alert_delivery_due_idx",
                    ),
                    models.Index(
                        fields=["rule", "cluster", "sent_at"],
                        name="feed_alert_delivery_cool_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("rule", "event", "channel_id"),
                        name="unique_feed_alert_delivery",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ErrorFeedAlertIssueState",
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
                ("last_queued_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.traceerrorgroup",
                    ),
                ),
                (
                    "last_event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="tracer.errorfeedissueevent",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.errorfeedalertrule",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_error_feed_alert_issue_state",
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted", False)),
                        fields=("rule", "cluster"),
                        name="unique_feed_alert_issue_state",
                    )
                ],
            },
        ),
    ]
