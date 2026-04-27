import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccRoutingPolicy(BaseModel):
    """Standalone routing policy with version history."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_routing_policies",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_routing_policies",
    )

    class Meta:
        db_table = "agentcc_routing_policy"
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_routing_policy_version",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({'active' if self.is_active else 'inactive'})"
