import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccBatchJob(models.Model):
    """A console batch submitted to the gateway's /-/batches for an org.

    The gateway keeps batches in memory and runs each item as the API key
    named at submit; this row is the batch -> org link Django checks
    before every get or cancel, the key Django minted for it (revoked at the
    batch's first terminal status, or 24 h after submit), and whether
    batch.completed was sent (``notified``, set exactly once).
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_batch_jobs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    gateway_batch_id = models.CharField(max_length=64)
    # The gateway id of the key minted for this batch (AgentccAPIKey.gateway_key_id).
    gateway_key_id = models.CharField(max_length=255)
    # No choices: the gateway owns its batch statuses; Django adds "failed"
    # only for a batch the gateway does not hold.
    status = models.CharField(max_length=16, default=PROCESSING)
    total = models.PositiveIntegerField(default=0)
    notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    key_revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_batch_job"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "gateway_batch_id"],
                name="unique_agentcc_batch_job_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["notified", "created_at"], name="agentcc_batch_job_open"
            ),
        ]

    def __str__(self):
        return f"batch {self.gateway_batch_id} ({self.status})"
