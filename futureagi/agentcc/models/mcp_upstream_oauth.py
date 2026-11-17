"""Per-user upstream OAuth for MCP servers (M25x): the gateway as an OAuth
client of the vendor's authorization server.

The tokens themselves live in the vault (``AgentccMCPCredential``, scope
``user``, kind ``oauth``). These models hold what the connect flow and the
gateway's token fetch need around them.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from agentcc.models.api_key import AgentccAPIKey
from agentcc.models.mcp_credential import URL_MAX_LENGTH, EncryptedAtRestModel


class AgentccMCPConnectState(EncryptedAtRestModel):
    """One connect in progress: created by the start, consumed by the
    callback, whatever its outcome. ``state`` and the browser cookie are kept
    only as hashes; the PKCE verifier is encrypted. The client is named by
    its id, kind and authentication method, never by its secret: an org or
    platform client is resolved again for the code exchange."""

    ENCRYPTED_FIELDS = ("encrypted_verifier",)

    class ClientKind(models.TextChoices):
        CIMD = "cimd"
        DCR = "dcr"
        ORG = "org"
        PLATFORM = "platform"

    class ClientAuth(models.TextChoices):
        NONE = "none"
        BASIC = "client_secret_basic"
        POST = "client_secret_post"

    class Mode(models.TextChoices):
        # The member's own account (scope user), or the organization's one
        # account an Owner or Admin signs in (scope shared, CN24).
        USER = "user"
        SHARED = "shared"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    server_id = models.CharField(max_length=64)
    state_hash = models.CharField(max_length=64, unique=True)
    cookie_hash = models.CharField(max_length=64)
    # Only the individual-authenticated route may attest a new start. Older
    # in-flight states must be restarted, never promoted by the callback.
    individual_authenticated = models.BooleanField(default=False, db_default=False)
    # Snapshot at start; Disconnect's durable fence outlives this one-use row.
    cancellation_generation = models.PositiveBigIntegerField(default=0, db_default=0)
    encrypted_verifier = models.TextField()
    issuer = models.CharField(max_length=512)
    token_endpoint = models.CharField(max_length=URL_MAX_LENGTH)
    client_id = models.CharField(max_length=URL_MAX_LENGTH)
    client_kind = models.CharField(max_length=8, choices=ClientKind.choices)
    client_auth = models.CharField(
        max_length=24, choices=ClientAuth.choices, default=ClientAuth.NONE
    )
    # A K2 pilot sign-in (connector_k2): the org's own registration only. The
    # grant it stores records this (``oauth_org_only``).
    org_only = models.BooleanField(default=False, db_default=False)
    resource = models.CharField(max_length=URL_MAX_LENGTH)
    redirect_uri = models.CharField(max_length=URL_MAX_LENGTH)
    origin = models.CharField(max_length=URL_MAX_LENGTH)
    scopes = models.JSONField(default=list, blank=True)
    iss_required = models.BooleanField(default=False)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.USER)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_connect_state"

    def __str__(self):
        return f"connect {self.pk} {self.server_id} user={self.user_id}"

    __repr__ = __str__


class AgentccMCPUpstreamClient(models.Model):
    """The client an authorization server registered for us through DCR,
    reused for later connects to it. A public client: there is no secret."""

    issuer = models.CharField(max_length=512)
    redirect_uri = models.CharField(max_length=URL_MAX_LENGTH)
    client_id = models.CharField(max_length=URL_MAX_LENGTH)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_upstream_client"
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "redirect_uri"],
                name="agentcc_mcp_upstream_client_one_per_issuer",
            )
        ]

    def __str__(self):
        return f"{self.issuer} client"


class AgentccMCPUpstreamTokenAudit(models.Model):
    """One authenticated call to the gateway's token fetch: who, for which
    server, and the outcome. Never the token."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.CharField(max_length=64)
    user_id = models.CharField(max_length=64)
    server_id = models.CharField(max_length=64)
    outcome = models.CharField(max_length=16)
    credential = models.ForeignKey(
        "prism.AgentccMCPCredential",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_upstream_token_audit"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["org_id", "created_at"], name="agentcc_mcp_token_audit_org"
            )
        ]

    def __str__(self):
        return f"token fetch {self.server_id} user={self.user_id} {self.outcome}"


class AgentccMCPPersonalKey(models.Model):
    """A key its owner marked personal: on servers that connect per user, the
    key acts as that user. Only the key's owner creates or removes this row,
    and the user it names is the key's own user, never a value a tenant sets."""

    api_key = models.OneToOneField(
        AgentccAPIKey, on_delete=models.CASCADE, related_name="mcp_personal"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_mcp_personal_key"

    def __str__(self):
        return f"personal key {self.api_key_id} user={self.user_id}"
