import uuid

from django.db import models

from mcp_server.constants import RESPONSE_STATUS_CHOICES


class MCPUsageRecord(models.Model):
    """Individual tool call records for analytics and auditing."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        "mcp_server.MCPSession",
        on_delete=models.CASCADE,
        related_name="usage_records",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_usage",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_usage",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_usage",
    )
    tool_name = models.CharField(max_length=100, db_index=True)
    tool_group = models.CharField(max_length=50)
    request_params = models.JSONField(default=dict)
    response_status = models.CharField(
        max_length=20,
        choices=RESPONSE_STATUS_CHOICES,
    )
    error_message = models.TextField(blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)
    embedded_agent_used = models.BooleanField(default=False)
    embedded_agent_tokens = models.PositiveIntegerField(default=0)
    called_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-called_at"]
        indexes = [
            models.Index(
                fields=["organization", "called_at"],
                name="idx_mcp_usage_org_time",
            ),
            models.Index(
                fields=["session", "called_at"],
                name="idx_mcp_usage_session_time",
            ),
            models.Index(
                fields=["tool_name", "called_at"],
                name="idx_mcp_usage_tool_time",
            ),
            models.Index(
                fields=["user", "called_at"],
                name="idx_mcp_usage_user_time",
            ),
        ]

    def __str__(self):
        return f"MCPUsageRecord({self.tool_name}, {self.response_status})"
