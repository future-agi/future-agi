from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organization


class AgentccMCPToolRevision(models.Model):
    """One seen definition of an upstream MCP tool, for pinning and quarantine.

    ``revision`` is the sha256 hex of the tool's canonical definition. A
    change or a new tool stays ``pending`` until an admin approves the diff.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        SUPERSEDED = "superseded"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_tool_revisions",
        db_index=False,  # covered by the unique constraint below
    )
    server_id = models.CharField(max_length=64)
    tool = models.CharField(max_length=255)
    revision = models.CharField(max_length=64)
    # name, title, description, schemas and annotations
    definition = models.JSONField(default=dict, blank=True)
    previous_revision = models.CharField(max_length=64, blank=True, default="")
    # [{field, before, after}], as in mcp-revision-v1
    diff = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_mcp_tool_revision"
        ordering = ["-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "server_id", "tool", "revision"],
                name="agentcc_mcp_revision_unique",
            ),
        ]

    def __str__(self):
        return f"{self.server_id}/{self.tool}@{self.revision[:12]} ({self.status})"
