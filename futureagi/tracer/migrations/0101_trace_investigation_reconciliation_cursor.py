import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0100_trace_investigation_memory"),
    ]

    operations = [
        migrations.CreateModel(
            name="TraceInvestigationReconciliationCursor",
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
                ("completed_through", models.DateTimeField(blank=True, null=True)),
                ("window_lower", models.DateTimeField(blank=True, null=True)),
                ("window_upper", models.DateTimeField(blank=True, null=True)),
                ("after_created_at", models.DateTimeField(blank=True, null=True)),
                (
                    "after_trace_id",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("last_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
                (
                    "project",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="omega_reconciliation_cursor",
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
                "db_table": "tracer_trace_investigation_reconciliation_cursor",
                "indexes": [
                    models.Index(
                        fields=["completed_through", "project"],
                        name="trace_inv_reconcile_due_idx",
                    )
                ],
            },
        )
    ]
