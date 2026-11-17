import uuid

from django.db import models


class BulkExportJob(models.Model):
    """Durable, project-scoped export; filters contains Observe query options."""

    ROW_TYPES = (("traces", "Traces"), ("spans", "Spans"), ("sessions", "Sessions"))
    FORMATS = (("csv", "CSV"), ("parquet", "Parquet"))
    STATUSES = tuple((value, value.title()) for value in ("queued", "running", "completed", "failed"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE, related_name="bulk_exports")
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    schedule = models.ForeignKey("tracer.ExportSchedule", on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    # Immutable destination snapshot: edits/deletion cannot redirect queued jobs.
    destination = models.JSONField(default=dict, blank=True)
    encrypted_credentials = models.BinaryField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    row_type = models.CharField(max_length=10, choices=ROW_TYPES)
    filters = models.JSONField(default=dict)
    format = models.CharField(max_length=10, choices=FORMATS, default="csv")
    status = models.CharField(max_length=10, choices=STATUSES, default="queued", db_index=True)
    row_count = models.PositiveBigIntegerField(default=0)
    file_location = models.CharField(max_length=1024, blank=True, default="")
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_bulk_export_job"
        ordering = ["-created_at"]
