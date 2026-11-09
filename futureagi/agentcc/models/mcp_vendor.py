"""Partner bindings and end-user sessions. Credentials never appear in read APIs."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from agentcc.models.mcp_credential import EncryptedAtRestModel


class AgentccMCPVendorBinding(EncryptedAtRestModel):
    ENCRYPTED_FIELDS = ("encrypted_bearer",)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    server_id = models.CharField(max_length=64)
    vendor = models.CharField(
        max_length=16, choices=[("pipedream", "Pipedream"), ("composio", "Composio")]
    )
    credential = models.ForeignKey(
        "prism.AgentccMCPCredential", on_delete=models.RESTRICT
    )
    identity_policy = models.CharField(
        max_length=24,
        default="member",
        choices=[("member", "Member"), ("member_or_asserted", "Member or asserted")],
    )
    project_id = models.CharField(max_length=128, blank=True, default="")
    environment = models.CharField(max_length=16, default="development")
    app_slug = models.CharField(max_length=100, blank=True, default="")
    toolkits = models.JSONField(default=list)
    tools = models.JSONField(default=dict)
    enabled = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    encrypted_bearer = models.TextField(null=True, blank=True)
    bearer_expires_at = models.DateTimeField(null=True, blank=True)
    bearer_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "server_id"],
                name="mcp_vendor_binding_org_server",
            )
        ]

    def save(self, *args, **kwargs):
        credential = self.credential
        if (
            credential.organization_id != self.organization_id
            or credential.server_id != self.server_id
            or credential.scope != "shared"
        ):
            raise ValidationError(
                "The vendor vault credential must belong to this organization and server's shared scope."
            )
        super().save(*args, **kwargs)


class AgentccMCPVendorConnection(EncryptedAtRestModel):
    ENCRYPTED_FIELDS = ("encrypted_session", "encrypted_endpoint")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    binding = models.ForeignKey(
        AgentccMCPVendorBinding, on_delete=models.CASCADE, related_name="connections"
    )
    end_user_key = models.CharField(max_length=80)
    source = models.CharField(
        max_length=8, choices=[("member", "Member"), ("asserted", "Asserted")]
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Only a vendor reference, never its token or session URL.
    vendor_account_ref = models.CharField(max_length=256, blank=True, default="")
    encrypted_session = models.TextField(null=True, blank=True)
    encrypted_endpoint = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        default="pending",
        choices=[
            ("pending", "Pending"),
            ("active", "Active"),
            ("revoked", "Revoked"),
            ("needs_reauth", "Needs reauthentication"),
        ],
    )
    version = models.PositiveIntegerField(default=1)
    consent_generation = models.PositiveIntegerField(default=1)
    connect_pending = models.BooleanField(default=False)
    connect_lease_id = models.UUIDField(null=True, blank=True)
    connect_expires_at = models.DateTimeField(null=True, blank=True)
    vendor_revoke_pending = models.BooleanField(default=False)
    offboard_requested = models.BooleanField(default=False)
    gateway_purge_pending = models.BooleanField(default=False)
    vendor_revoke_outcome = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def vendor_subject(self):
        # A new consent generation must not re-adopt surviving partner accounts.
        if self.consent_generation == 1:
            return self.end_user_key
        return f"{self.end_user_key}_v{self.consent_generation}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["binding", "end_user_key"],
                name="mcp_vendor_connection_identity",
            )
        ]


class AgentccMCPVendorCleanup(EncryptedAtRestModel):
    """Durable per-consent handles; a new consent cannot discard old cleanup."""

    ENCRYPTED_FIELDS = ("encrypted_session",)
    connection = models.ForeignKey(
        AgentccMCPVendorConnection,
        on_delete=models.CASCADE,
        related_name="cleanup_obligations",
    )
    consent_generation = models.PositiveIntegerField()
    vendor_account_ref = models.CharField(max_length=256, blank=True, default="")
    encrypted_session = models.TextField(null=True, blank=True)
    outcome = models.CharField(max_length=16, default="pending")
    version = models.PositiveIntegerField(default=1)
    in_flight = models.BooleanField(default=False)
    reason = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "consent_generation"],
                name="mcp_vendor_cleanup_generation",
            )
        ]


class AgentccMCPVendorReceipt(models.Model):
    connection = models.ForeignKey(
        AgentccMCPVendorConnection,
        on_delete=models.SET_NULL,
        null=True,
        related_name="receipts",
    )
    # Plain identifiers intentionally outlive binding/connection deletion.
    organization_id = models.UUIDField(null=True)
    connection_ref = models.UUIDField(null=True)
    server_id = models.CharField(max_length=64, default="")
    consent_generation = models.PositiveIntegerField(default=1)
    step = models.CharField(max_length=32)
    outcome = models.CharField(max_length=16)
    reason = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


class AgentccMCPVendorAudit(models.Model):
    organization_id = models.UUIDField()
    server_id = models.CharField(max_length=64)
    end_user_hash = models.CharField(max_length=16)
    outcome = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
