import uuid

from django.db import models


class MCPOAuthClient(models.Model):
    """Registered OAuth client applications (Cursor, Claude Code, etc.)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.CharField(max_length=100, unique=True)
    client_secret_hash = models.CharField(max_length=255)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"MCPOAuthClient({self.name})"
