import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0105_tracegroupingattempt_and_more")]

    operations = [
        migrations.AddField(
            model_name="traceerrorgroup",
            name="severity_assessment_status",
            field=models.CharField(default="unassessed", max_length=32),
        ),
        migrations.AddField(
            model_name="traceerrorgroup",
            name="severity_source",
            field=models.CharField(default="legacy", max_length=16),
        ),
        migrations.AddField(
            model_name="traceerrorgroup",
            name="severity_reason",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="TraceGroupingSeverityJob",
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
                ("issue_revision", models.PositiveBigIntegerField()),
                ("policy_version", models.CharField(max_length=64)),
                ("state", models.CharField(default="pending", max_length=32)),
                ("not_before", models.DateTimeField()),
                ("attempt_number", models.PositiveIntegerField(default=0)),
                ("lease_token_digest", models.CharField(blank=True, max_length=64)),
                ("lease_expires_at", models.DateTimeField(null=True)),
                ("snapshot", models.JSONField(default=dict)),
                ("snapshot_digest", models.CharField(blank=True, max_length=71)),
                ("result", models.JSONField(null=True)),
                ("failure_code", models.CharField(blank=True, max_length=100)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.tracegroupingissuestate",
                    ),
                ),
                (
                    "source_attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="tracer.tracegroupingattempt",
                    ),
                ),
                (
                    "receipt",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="tracer.tracegroupingcall",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_grouping_severity_job",
                "indexes": [
                    models.Index(
                        fields=["state", "not_before"], name="group_severity_due_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("issue", "issue_revision", "policy_version"),
                        name="unique_group_severity_revision",
                    )
                ],
            },
        ),
    ]
