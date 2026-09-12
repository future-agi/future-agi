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
        ("tracer", "0099_trace_investigation_control"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TraceInvestigationMemoryEvaluation",
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
                ("candidate_digest", models.CharField(max_length=71)),
                ("cohort_id", models.CharField(max_length=255)),
                ("metrics", models.JSONField()),
                ("passed", models.BooleanField()),
                ("holdout_disjoint", models.BooleanField()),
                ("content_digest", models.CharField(max_length=71)),
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
                "db_table": "tracer_trace_investigation_memory_evaluation",
            },
        ),
        migrations.CreateModel(
            name="TraceInvestigationMemorySnapshot",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("candidate", "Candidate"),
                            ("active", "Active"),
                            ("superseded", "Superseded"),
                            ("rejected", "Rejected"),
                        ],
                        max_length=20,
                    ),
                ),
                ("digest", models.CharField(max_length=71)),
                ("entries", models.JSONField(default=list)),
                ("source_feedback_ids", models.JSONField(default=list)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="candidates",
                        to="tracer.traceinvestigationmemorysnapshot",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="omega_memory_snapshots",
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
                "db_table": "tracer_trace_investigation_memory",
            },
        ),
        migrations.CreateModel(
            name="TraceInvestigationMemoryPromotion",
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
                    "action",
                    models.CharField(
                        choices=[("promote", "Promote"), ("rollback", "Rollback")],
                        max_length=20,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=255)),
                ("content_digest", models.CharField(max_length=71)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "evaluation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="tracer.traceinvestigationmemoryevaluation",
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
                (
                    "from_snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outgoing_promotions",
                        to="tracer.traceinvestigationmemorysnapshot",
                    ),
                ),
                (
                    "to_snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="incoming_promotions",
                        to="tracer.traceinvestigationmemorysnapshot",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_trace_investigation_memory_promotion",
            },
        ),
        migrations.AddField(
            model_name="traceinvestigationmemoryevaluation",
            name="candidate",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="evaluations",
                to="tracer.traceinvestigationmemorysnapshot",
            ),
        ),
        migrations.CreateModel(
            name="TraceInvestigationFeedback",
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
                ("occurrence_id", models.UUIDField()),
                ("finding_id", models.CharField(max_length=128)),
                ("idempotency_key", models.CharField(max_length=255)),
                (
                    "feedback_type",
                    models.CharField(
                        choices=[
                            ("confirm_finding", "Confirm Finding"),
                            ("false_positive", "False Positive"),
                            ("attribution_correction", "Attribution Correction"),
                            ("grouping_merge", "Grouping Merge"),
                            ("grouping_split", "Grouping Split"),
                        ],
                        max_length=40,
                    ),
                ),
                ("comment", models.TextField(blank=True)),
                ("review_state", models.CharField(default="reviewed", max_length=20)),
                (
                    "learning_event_id",
                    models.UUIDField(default=uuid.uuid4, unique=True),
                ),
                ("content_digest", models.CharField(max_length=71)),
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
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feedback_events",
                        to="tracer.traceinvestigationreport",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
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
                "db_table": "tracer_trace_investigation_feedback",
                "indexes": [
                    models.Index(
                        fields=["project", "created_at"],
                        name="trace_inv_feedback_project_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "idempotency_key"),
                        name="unique_trace_inv_feedback_key",
                    )
                ],
            },
        ),
        migrations.AddIndex(
            model_name="traceinvestigationmemorysnapshot",
            index=models.Index(
                fields=["project", "status", "created_at"],
                name="trace_inv_memory_project_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationmemorysnapshot",
            constraint=models.UniqueConstraint(
                fields=("organization", "idempotency_key"),
                name="unique_trace_inv_memory_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationmemorysnapshot",
            constraint=models.UniqueConstraint(
                fields=("project", "digest"),
                name="unique_trace_inv_memory_digest",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationmemorysnapshot",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "active")),
                fields=("project",),
                name="unique_trace_inv_active_memory",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationmemorypromotion",
            constraint=models.UniqueConstraint(
                fields=("organization", "idempotency_key"),
                name="unique_trace_inv_memory_promo_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="traceinvestigationmemoryevaluation",
            constraint=models.UniqueConstraint(
                fields=("organization", "idempotency_key"),
                name="unique_trace_inv_memory_eval_key",
            ),
        ),
    ]
