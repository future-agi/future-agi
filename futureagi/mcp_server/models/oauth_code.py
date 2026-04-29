import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class MCPOAuthCode(models.Model):
    """Temporary authorization codes (10 min TTL)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True, db_index=True)
    client = models.ForeignKey(
        "mcp_server.MCPOAuthClient",
        on_delete=models.CASCADE,
        related_name="auth_codes",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_auth_codes",
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    redirect_uri = models.URLField()
    scope = models.JSONField(default=list)  # List of tool group slugs
    state = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    @property
    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=10)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"MCPOAuthCode({self.client.name}, {self.user})"
