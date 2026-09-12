import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0102_trace_scan_omega_last_claimed_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="TraceInvestigationUsageReceipt",
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
                ("event_id", models.UUIDField(unique=True)),
                (
                    "raw_cost_usd",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=30,
                        null=True,
                    ),
                ),
                (
                    "credit_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=18,
                        max_digits=30,
                        null=True,
                    ),
                ),
                ("event_properties", models.JSONField(default=dict)),
                ("event_payload", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("emitted", "Emitted"),
                            ("unpriced", "Unpriced"),
                            ("skipped", "Skipped"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("status_reason", models.CharField(blank=True, max_length=64)),
                ("delivery_attempts", models.PositiveIntegerField(default=0)),
                ("emitted_at", models.DateTimeField(blank=True, null=True)),
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
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="usage_receipt",
                        to="tracer.traceinvestigationreport",
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
                "db_table": "tracer_trace_investigation_usage_receipt",
                "indexes": [
                    models.Index(
                        fields=["status", "updated_at"],
                        name="trace_inv_usage_pending_idx",
                    ),
                    models.Index(
                        fields=["organization", "created_at"],
                        name="trace_inv_usage_org_idx",
                    ),
                ],
            },
        ),
    ]
