import uuid

from django.db import models


class MCPOAuthRefreshFamily(models.Model):
    """A chain of rotated refresh tokens that started from one authorization.

    Revoking the family invalidates every refresh token in it. A family is
    revoked when a refresh token that was already rotated is presented again
    (reuse detection) or when a new authorization replaces it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        "mcp_server.MCPConnection",
        on_delete=models.CASCADE,
        related_name="oauth_refresh_families",
    )
    client_id = models.CharField(max_length=100)
    # Tool groups the user consented to for this authorization. A refreshed
    # access token never carries more than this. None for families created
    # before it was recorded; filled in on their next refresh.
    scope = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return f"MCPOAuthRefreshFamily({self.id}, revoked={bool(self.revoked_at)})"


class MCPOAuthRefreshToken(models.Model):
    """One refresh token in a family. Only a SHA-256 hash of the token is kept."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    family = models.ForeignKey(
        MCPOAuthRefreshFamily,
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return (
            f"MCPOAuthRefreshToken(family={self.family_id}, used={bool(self.used_at)})"
        )
