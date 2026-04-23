import uuid

from django.db import models

from mcp_server.constants import SESSION_STATUS_CHOICES, TRANSPORT_CHOICES


class MCPSession(models.Model):
    """Tracks active and historical MCP sessions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        "mcp_server.MCPConnection",
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_sessions",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_sessions",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_sessions",
    )
    status = models.CharField(
        max_length=20,
        choices=SESSION_STATUS_CHOICES,
        default="active",
    )
    transport = models.CharField(
        max_length=20,
        choices=TRANSPORT_CHOICES,
        default="streamable_http",
    )
    client_name = models.CharField(max_length=100, blank=True, default="")
    client_version = models.CharField(max_length=50, blank=True, default="")
    client_os = models.CharField(max_length=50, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    tool_call_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(
                fields=["user", "status"],
                name="idx_mcp_session_user_status",
            ),
            models.Index(
                fields=["organization", "status"],
                name="idx_mcp_session_org_status",
            ),
        ]

    def __str__(self):
        return f"MCPSession({self.id}, {self.status})"
