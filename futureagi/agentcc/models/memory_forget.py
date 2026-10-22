import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccMemoryForget(models.Model):
    """GDPR evidence of one "forget subject" fan-out to an org's memory servers.

    Only the subject's hash is stored. ``receipts`` holds one
    ``{server, status, attempts, vendor_request_id}`` per server, so a
    partial failure stays visible. Kept until the org is deleted; gateway log
    retention never purges it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_memory_forgets",
    )
    subject_hash = models.CharField(max_length=64)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    # No choices: the writer of these rows owns the status values, and a new
    # one must not need a migration.
    status = models.CharField(max_length=16, default="pending")
    receipts = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_memory_forget"
        ordering = ["-created_at"]

    def __str__(self):
        return f"forget {self.pk} ({self.status})"
