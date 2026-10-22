"""Django as the OAuth 2.1 authorization server for MCP clients."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Organization
from agentcc.models.mcp_credential import URL_MAX_LENGTH, EncryptedAtRestModel


class AgentccMCPOAuthClient(models.Model):
    """An MCP client: pre-registered, a client ID metadata document, or DCR."""

    class Kind(models.TextChoices):
        CIMD = "cimd"
        DCR = "dcr"
        PREREGISTERED = "preregistered"

    client_id = models.CharField(max_length=URL_MAX_LENGTH, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    metadata = models.JSONField(default=dict, blank=True)
    redirect_uris = models.JSONField(default=list, blank=True)
    application_type = models.CharField(max_length=16, blank=True, default="")
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_oauth_client"

    def __str__(self):
        return f"{self.client_id} ({self.kind})"


class AgentccMCPOAuthConsent(models.Model):
    """A user's consent for a client to reach one resource in one org."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_oauth_consents",
    )
    client = models.ForeignKey(
        AgentccMCPOAuthClient, on_delete=models.CASCADE, related_name="consents"
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_oauth_consents",
    )
    scopes = models.JSONField(default=list, blank=True)
    resource = models.CharField(max_length=URL_MAX_LENGTH)
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_mcp_oauth_consent"

    def __str__(self):
        return f"consent {self.pk} client={self.client_id} user={self.user_id}"


class AgentccMCPOAuthRefreshToken(models.Model):
    """A refresh token, stored only as its hash; each use rotates it."""

    token_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_oauth_refresh_tokens",
    )
    client = models.ForeignKey(
        AgentccMCPOAuthClient, on_delete=models.CASCADE, related_name="refresh_tokens"
    )
    # The org the consent was given in; a refresh re-checks membership in it.
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_mcp_oauth_refresh_tokens",
    )
    resource = models.CharField(max_length=URL_MAX_LENGTH)
    expires_at = models.DateTimeField()
    rotated_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_oauth_refresh_token"

    def __str__(self):
        # The hash is not the token, but it is still left out.
        return f"refresh token {self.pk} client={self.client_id} user={self.user_id}"


class AgentccMCPOAuthSigningKey(EncryptedAtRestModel):
    """An access-token signing key; the private half is stored encrypted."""

    ENCRYPTED_FIELDS = ("encrypted_private",)

    class Alg(models.TextChoices):
        ES256 = "ES256"

    class Status(models.TextChoices):
        ACTIVE = "active"
        RETIRING = "retiring"
        RETIRED = "retired"

    kid = models.CharField(max_length=64, unique=True)
    alg = models.CharField(max_length=8, choices=Alg.choices, default=Alg.ES256)
    encrypted_private = models.TextField()
    public_jwk = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_oauth_signing_key"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kid} {self.alg} ({self.status})"

    def __repr__(self):
        return f"<AgentccMCPOAuthSigningKey {self.pk} {self}>"
