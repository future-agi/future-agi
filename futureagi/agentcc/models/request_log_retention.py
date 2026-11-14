import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccRequestLogRetention(BaseModel):
    """How long an organization keeps its gateway Request Logs.

    No row, or ``retention_days`` null, means keep forever: an upgrade deletes
    nothing until an admin chooses a number of days.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_request_log_retention",
    )
    retention_days = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_run_started_at = models.DateTimeField(null=True, blank=True)
    last_run_finished_at = models.DateTimeField(null=True, blank=True)
    last_run_deleted_rows = models.BigIntegerField(default=0)
    last_run_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "agentcc_request_log_retention"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_request_log_retention_org",
            ),
        ]

    def __str__(self):
        days = self.retention_days or "forever"
        return f"{self.organization_id}: {days}"
