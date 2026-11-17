import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0120_merge_sharedlink_and_agent_observe")]

    # CreateModel reverses to DROP of only this new table (including its indexes).
    # There is no backfill or mutation of existing tables/rows in either direction.
    operations = [
        migrations.CreateModel(
            name="BackgroundTraceAnalysisState",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("trace_id", models.UUIDField()),
                ("generation", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("status", models.CharField(choices=[("retry_pending", "Retry pending"), ("running", "Running"), ("completed", "Completed"), ("aborted", "Aborted")], default="retry_pending", max_length=20)),
                ("result_analysis_id", models.UUIDField(blank=True, editable=False, null=True)),
                ("reason_code", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=models.CASCADE, to="tracer.project")),
            ],
            options={
                "db_table": "tracer_background_trace_analysis_state",
                "constraints": [models.UniqueConstraint(fields=("project", "trace_id"), name="bg_analysis_project_trace_uniq")],
                "indexes": [models.Index(fields=["trace_id"], name="bg_analysis_trace_idx")],
            },
        ),
    ]
