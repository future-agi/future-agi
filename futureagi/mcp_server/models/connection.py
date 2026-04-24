import uuid

from django.db import models

from mcp_server.constants import CONNECTION_MODE_CHOICES
from tfc.utils.base_model import BaseModel


class MCPConnection(BaseModel):
    """Represents a user's MCP configuration for a workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_connections",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_connections",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mcp_connections",
    )
    connection_mode = models.CharField(
        max_length=20,
        choices=CONNECTION_MODE_CHOICES,
        default="remote",
    )
    is_active = models.BooleanField(default=True)
    oauth_token_encrypted = models.TextField(null=True, blank=True)
    oauth_refresh_token_encrypted = models.TextField(null=True, blank=True)
    oauth_token_expires_at = models.DateTimeField(null=True, blank=True)
    api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_connections",
    )
    client_name = models.CharField(max_length=100, blank=True, default="")
    client_version = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "workspace"],
                condition=models.Q(deleted=False),
                name="unique_mcp_connection_per_user_workspace",
            ),
        ]

    def __str__(self):
        return f"MCPConnection({self.user}, {self.workspace})"
