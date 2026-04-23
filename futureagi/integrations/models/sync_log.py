import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class SyncStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"
    RATE_LIMITED = "rate_limited", "Rate Limited"
    NO_NEW_DATA = "no_new_data", "No New Data"


class SyncLog(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        "integrations.IntegrationConnection",
        on_delete=models.CASCADE,
        related_name="sync_logs",
    )

    status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.PARTIAL,
    )
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    # Operation counts
    traces_fetched = models.PositiveIntegerField(default=0)
    traces_created = models.PositiveIntegerField(default=0)
    traces_updated = models.PositiveIntegerField(default=0)
    spans_synced = models.PositiveIntegerField(default=0)
    scores_synced = models.PositiveIntegerField(default=0)

    # Error details
    error_message = models.TextField(null=True, blank=True)
    error_details = models.JSONField(default=dict, blank=True)

    # Time window queried
    sync_from = models.DateTimeField(null=True, blank=True)
    sync_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integrations_sync_log"
        ordering = ("-started_at",)
        indexes = [
            models.Index(
                fields=["connection", "started_at"],
                name="idx_synclog_conn_started",
            ),
            models.Index(
                fields=["connection", "status"],
                name="idx_synclog_conn_status",
            ),
        ]

    def __str__(self):
        return f"SyncLog {self.id} ({self.status}) for {self.connection_id}"
