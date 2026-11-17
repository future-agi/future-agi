import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0138_bulk_export_job"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExportSchedule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("row_type", models.CharField(choices=[("traces", "Traces"), ("spans", "Spans"), ("sessions", "Sessions")], max_length=10)),
                ("filters", models.JSONField(default=dict)),
                ("format", models.CharField(choices=[("csv", "CSV"), ("parquet", "Parquet")], default="csv", max_length=10)),
                ("destination", models.JSONField(default=dict)),
                ("encrypted_credentials", models.BinaryField()),
                ("interval", models.CharField(choices=[("hourly", "Hourly"), ("daily", "Daily")], max_length=10)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("enabled", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="export_schedules", to="tracer.project")),
            ],
            options={"db_table": "tracer_export_schedule", "ordering": ["-created_at"]},
        ),
        migrations.AddField(
            model_name="bulkexportjob", name="schedule",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="jobs", to="tracer.exportschedule"),
        ),
        migrations.AddField(
            model_name="bulkexportjob", name="destination",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="bulkexportjob", name="encrypted_credentials",
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bulkexportjob", name="window_end",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
