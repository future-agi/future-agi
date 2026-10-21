import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0103_merge_error_feed_and_evaltask")]

    operations = [
        migrations.CreateModel(
            name="TraceGroupingFeatureJob",
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
                ("policy_version", models.CharField(max_length=64)),
                ("publication_result_digest", models.CharField(max_length=71)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                            ("superseded", "Superseded"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("not_before", models.DateTimeField()),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="grouping_feature_jobs",
                        to="tracer.traceinvestigationreport",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_grouping_feature_job",
                "indexes": [
                    models.Index(
                        fields=["state", "not_before"], name="group_feature_due_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("report", "policy_version"),
                        name="unique_group_feature_report_policy",
                    )
                ],
            },
        ),
    ]
