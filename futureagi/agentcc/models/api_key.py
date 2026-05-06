import uuid

from django.db import models

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccAPIKey(BaseModel):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"

    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_api_keys",
        blank=False,
        null=False,
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_api_keys",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        "prism.AgentccProject",
        on_delete=models.SET_NULL,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="agentcc_api_keys",
        null=True,
        blank=True,
    )
    gateway_key_id = models.CharField(max_length=255)
    key_prefix = models.CharField(max_length=20, blank=True, default="")
    key_hash = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=ACTIVE,
    )
    allowed_models = models.JSONField(default=list, blank=True)
    allowed_providers = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agentcc_api_key"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gateway_key_id"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_api_key_id",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["gateway_key_id"]),
            models.Index(fields=["key_hash"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.key_prefix}...)"
