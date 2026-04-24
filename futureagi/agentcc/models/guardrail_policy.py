import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccGuardrailPolicy(BaseModel):
    """
    Reusable, named guardrail policy — a collection of guardrail checks
    that can be scoped to global, per-project, or per-key level.
    Policies are merged into the org config and pushed to the gateway.
    """

    SCOPE_GLOBAL = "global"
    SCOPE_PROJECT = "project"
    SCOPE_KEY = "key"
    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "Global"),
        (SCOPE_PROJECT, "Project"),
        (SCOPE_KEY, "Key"),
    ]

    MODE_ENFORCE = "enforce"
    MODE_MONITOR = "monitor"
    MODE_CHOICES = [
        (MODE_ENFORCE, "Enforce"),
        (MODE_MONITOR, "Monitor"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_guardrail_policies",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        default=SCOPE_GLOBAL,
    )
    checks = models.JSONField(default=list, blank=True)
    encrypted_check_configs = models.BinaryField(null=True, blank=True)
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_ENFORCE,
    )
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=100)
    applied_keys = models.JSONField(default=list, blank=True)
    applied_projects = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "agentcc_guardrail_policy"
        ordering = ["priority", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_guardrail_policy_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
        ]

    def __str__(self):
        return (
            f"{self.name} ({self.scope}, {'active' if self.is_active else 'inactive'})"
        )
