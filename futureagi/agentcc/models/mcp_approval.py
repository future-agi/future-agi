from django.conf import settings
from django.db import models

from accounts.models import Organization


class AgentccMCPApproval(models.Model):
    """The audit row of one gateway tool-call approval.

    The gateway holds the live approval; Django writes this row for each
    decision. It binds the org, virtual server, tool, argument hash and run.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        DENIED = "denied"
        EXPIRED = "expired"
        USED = "used"

    approval_id = models.CharField(max_length=64, unique=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_approvals",
    )
    virtual_server_id = models.CharField(max_length=64)
    tool = models.CharField(max_length=255)
    args_hash = models.CharField(max_length=64)
    agent_run_id = models.CharField(max_length=128, blank=True, default="")
    api_key_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    requested_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "agentcc_mcp_approval"
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.approval_id} {self.tool} ({self.status})"
