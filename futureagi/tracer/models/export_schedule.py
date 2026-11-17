import uuid

from django.db import models

from tracer.models.bulk_export import BulkExportJob


class ExportSchedule(models.Model):
    """Customer destination settings and the last successfully exported window."""

    INTERVALS = (("hourly", "Hourly"), ("daily", "Daily"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE, related_name="export_schedules")
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    row_type = models.CharField(max_length=10, choices=BulkExportJob.ROW_TYPES)
    filters = models.JSONField(default=dict)
    format = models.CharField(max_length=10, choices=BulkExportJob.FORMATS, default="csv")
    destination = models.JSONField(default=dict)
    encrypted_credentials = models.BinaryField()
    interval = models.CharField(max_length=10, choices=INTERVALS)
    last_run_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracer_export_schedule"
        ordering = ["-created_at"]
