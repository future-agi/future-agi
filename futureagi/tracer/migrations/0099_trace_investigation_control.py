import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0098_observabilityprovider_poll_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="tracescanconfig",
            name="engine",
            field=models.CharField(
                choices=[("legacy", "Legacy"), ("omega", "Omega")],
                default="legacy",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="tracescanconfig",
            name="omega_limits",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="tracescanconfig",
            name="omega_memory",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="TraceInvestigationJob",
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
                ("trace_id", models.UUIDField()),
                ("root_span_id", models.CharField(max_length=64)),
                ("root_end_time", models.DateTimeField()),
                ("generation", models.PositiveBigIntegerField(default=1)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("waiting", "Waiting"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="waiting",
                        max_length=20,
                    ),
                ),
                ("not_before", models.DateTimeField()),
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
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_investigation_job",
                "indexes": [
                    models.Index(
                        fields=["state", "not_before"],
                        name="trace_inv_job_due_idx",
                    ),
                    models.Index(
                        fields=["project", "state"],
                        name="trace_inv_job_project_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("project", "trace_id"),
                        name="unique_trace_investigation_job",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="TraceInvestigationDelivery",
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
                ("topic", models.CharField(max_length=255)),
                ("partition", models.PositiveIntegerField()),
                ("offset", models.PositiveBigIntegerField()),
                ("event_id", models.UUIDField()),
                ("payload_digest", models.CharField(max_length=71)),
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
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_investigation_delivery",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("topic", "partition", "offset"),
                        name="unique_trace_investigation_delivery",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "event_id"),
                        name="unique_trace_investigation_event",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TraceInvestigationAttempt",
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
                ("generation", models.PositiveBigIntegerField()),
                ("worker_id", models.CharField(max_length=255)),
                ("engine_version", models.CharField(max_length=20)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("claimed", "Claimed"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                            ("expired", "Expired"),
                        ],
                        default="claimed",
                        max_length=20,
                    ),
                ),
                ("lease_token_digest", models.CharField(max_length=64)),
                ("lease_expires_at", models.DateTimeField()),
                ("read_cutoff", models.DateTimeField()),
                ("memory_snapshot_id", models.CharField(max_length=128)),
                ("memory_digest", models.CharField(max_length=71)),
                ("memory", models.JSONField(default=list)),
                ("limits", models.JSONField(default=dict)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("cancellation_reason", models.TextField(blank=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attempts",
                        to="tracer.traceinvestigationjob",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_investigation_attempt",
                "indexes": [
                    models.Index(
                        fields=["status", "lease_expires_at"],
                        name="trace_inv_attempt_lease_idx",
                    ),
                    models.Index(
                        fields=["job", "status"],
                        name="trace_inv_attempt_job_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("job", "generation"),
                        name="unique_trace_investigation_attempt",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="TraceInvestigationReport",
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
                ("idempotency_key", models.CharField(max_length=255)),
                ("result_digest", models.CharField(max_length=71)),
                ("result", models.JSONField()),
                ("occurrences", models.JSONField(default=list)),
                (
                    "grouping_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("not_required", "Not Required"),
                            ("stale", "Stale"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        max_length=20,
                    ),
                ),
                ("active_projection_updated", models.BooleanField(default=False)),
                (
                    "attempt",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report",
                        to="tracer.traceinvestigationattempt",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="tracer.traceinvestigationjob",
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
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_investigation_report",
                "indexes": [
                    models.Index(
                        fields=["project", "grouping_status", "created_at"],
                        name="trace_inv_report_group_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "idempotency_key"),
                        name="unique_trace_investigation_report_key",
                    )
                ],
            },
        ),
    ]
