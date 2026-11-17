"""State of the virtual-server provisioning API for Omega EE (CN22a).

Hosted Omega EE attaches a connection to a gateway virtual server instead of
holding vendor tokens (``docs/gateway/connectors/omega-ee-attach.md``). Three
records back that API:

- ``AgentccEEServiceCredential``: the credential EE provisions with, one
  current per org (AM-C18 Q1). It is made for EE alone, works only on the
  provisioning API, rotates and revokes on its own, and is audited as EE. Only
  its SHA-256 is stored: the secret is shown once, when it is issued.
- ``AgentccEEAgentKey``: a gateway virtual key the provisioning API minted for
  one workspace (spec section 3.7). Ensure binds only such a key (rule 3), so
  the binding, not key metadata a tenant could write, says which keys EE may
  use and revoke.
- ``AgentccEEVirtualServer``: the virtual server one EE connection got, keyed
  by (org, workspace, connection). The virtual server itself lives in the
  org config (``mcp.virtual_servers.<slug>``); this row remembers which slug
  and id belong to the connection, its mode and its agent key.
"""

import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models.api_key import AgentccAPIKey


class AgentccEEServiceCredential(models.Model):
    """EE's provisioning credential for one organization.

    EE sends ``api_key`` as ``X-Api-Key`` and the secret as ``X-Secret-Key``.
    Rotating issues a new pair and retires the old one at once; revoking
    retires it with no successor.
    """

    class Status(models.TextChoices):
        ACTIVE = "active"
        ROTATED = "rotated"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_ee_service_credentials",
    )
    api_key = models.CharField(max_length=64, unique=True)
    secret_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.ACTIVE
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_ee_service_credential"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(status="active"),
                name="agentcc_ee_one_active_service_credential",
            ),
        ]

    def __str__(self):
        return f"EE service credential {self.api_key} ({self.status})"


class AgentccEEAgentKey(models.Model):
    """A gateway virtual key the provisioning API minted for one workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    api_key = models.OneToOneField(
        AgentccAPIKey, on_delete=models.CASCADE, related_name="ee_agent_key"
    )
    minted_by = models.ForeignKey(
        AgentccEEServiceCredential,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    # CN22d: the Idempotency-Key (a UUIDv7) EE sent with the mint, so that EE
    # can find the key again after a lost answer (the workspace's key list
    # and its ?mint_idempotency_key= filter). An identifier, not a secret:
    # with EE's service credential it gets at most a 409 that names the key,
    # never the key. Null for keys minted before CN22d.
    mint_idempotency_key = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agentcc_ee_agent_key"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "workspace"], name="agentcc_ee_agent_key_org_ws"
            ),
            models.Index(
                fields=["workspace", "mint_idempotency_key"],
                name="agentcc_ee_agent_key_mint_key",
            ),
        ]

    def __str__(self):
        return f"EE agent key {self.api_key_id} for workspace {self.workspace_id}"


class AgentccEEVirtualServer(models.Model):
    """The virtual server one EE connection was given.

    ``detached_at`` is set when a detach saved the removal but the gateway did
    not accept it yet: the row stays so that detaching again pushes again.
    """

    class Mode(models.TextChoices):
        AGENT = "agent"
        USER = "user"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    connection_id = models.CharField(max_length=128)
    mode = models.CharField(max_length=8, choices=Mode.choices)
    slug = models.CharField(max_length=63)
    vs_id = models.CharField(max_length=29)
    agent_key = models.ForeignKey(
        AgentccEEAgentKey,
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    detached_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agentcc_ee_virtual_server"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "workspace", "connection_id"],
                name="agentcc_ee_one_virtual_server_per_connection",
            ),
            models.UniqueConstraint(
                fields=["organization", "slug"],
                name="agentcc_ee_virtual_server_slug",
            ),
            models.UniqueConstraint(
                fields=["organization", "vs_id"],
                name="agentcc_ee_virtual_server_id",
            ),
        ]

    def __str__(self):
        return f"EE virtual server {self.vs_id} ({self.mode}) for {self.connection_id}"
