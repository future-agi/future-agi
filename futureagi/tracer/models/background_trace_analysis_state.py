"""Durable background lifecycle, independent of replaceable analysis findings."""

import uuid

from django.db import models


class BackgroundTraceAnalysisState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    # Traces are ClickHouse-owned; never add a PostgreSQL Trace FK here.
    trace_id = models.UUIDField()
    generation = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20,
        choices=[
            ("retry_pending", "Retry pending"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("aborted", "Aborted"),
        ],
        default="retry_pending",
    )
    # Reserved result UUID equals generation; published atomically with completed.
    result_analysis_id = models.UUIDField(null=True, blank=True, editable=False)
    reason_code = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracer_background_trace_analysis_state"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "trace_id"], name="bg_analysis_project_trace_uniq"
            ),
        ]
        indexes = [models.Index(fields=["trace_id"], name="bg_analysis_trace_idx")]
