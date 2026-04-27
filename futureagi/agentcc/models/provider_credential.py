import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccProviderCredential(BaseModel):
    """
    Encrypted provider credential for the Agentcc gateway.

    Stores provider API keys (e.g. OpenAI, Anthropic) using Fernet field-level
    encryption.  Non-sensitive config (base_url, models, timeouts) is stored in
    plain-text fields for easy querying and display.

    Replaces the old ``AgentccOrgConfig.providers`` JSONField which stored
    credentials as plaintext inside a JSON blob.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_provider_credentials",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_provider_credentials",
        null=True,
        blank=True,
    )

    # Provider identity
    provider_name = models.CharField(max_length=100)  # "openai", "anthropic", etc.
    display_name = models.CharField(max_length=255, blank=True, default="")

    # Encrypted credentials — Fernet-encrypted dict, e.g. {"api_key": "sk-..."}
    # Use CredentialManager.encrypt() / .decrypt() from integrations.
    encrypted_credentials = models.BinaryField()

    # Non-sensitive provider config
    base_url = models.URLField(max_length=500, blank=True, default="")
    api_format = models.CharField(max_length=50, default="openai")
    models_list = models.JSONField(
        default=list, blank=True
    )  # ["gpt-4o", "gpt-4o-mini"]
    default_timeout_seconds = models.IntegerField(default=60)
    max_concurrent = models.IntegerField(default=100)
    conn_pool_size = models.IntegerField(default=100)
    extra_config = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    last_rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_provider_credential"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider_name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_provider_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["provider_name"]),
        ]

    def __str__(self):
        return f"{self.provider_name} ({self.organization_id})"
