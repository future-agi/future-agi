import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0137_useralertmonitor_pagerduty_routing_key"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BulkExportJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("row_type", models.CharField(choices=[("traces", "Traces"), ("spans", "Spans"), ("sessions", "Sessions")], max_length=10)),
                ("filters", models.JSONField(default=dict)),
                ("format", models.CharField(choices=[("csv", "CSV"), ("parquet", "Parquet")], default="csv", max_length=10)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")], db_index=True, default="queued", max_length=10)),
                ("row_count", models.PositiveBigIntegerField(default=0)),
                ("file_location", models.CharField(blank=True, default="", max_length=1024)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bulk_exports", to="tracer.project")),
            ],
            options={"db_table": "tracer_bulk_export_job", "ordering": ["-created_at"]},
        ),
    ]
