import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class AgentccPromptTemplate(BaseModel):
    """Prompt template integrated with the gateway for variable substitution."""

    ENV_DEV = "dev"
    ENV_STAGING = "staging"
    ENV_PROD = "prod"

    ENV_CHOICES = [
        (ENV_DEV, "Development"),
        (ENV_STAGING, "Staging"),
        (ENV_PROD, "Production"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_prompt_templates",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    template = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=255, blank=True, default="")
    environment = models.CharField(
        max_length=20,
        choices=ENV_CHOICES,
        default=ENV_DEV,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_prompt_templates",
    )

    class Meta:
        db_table = "agentcc_prompt_template"
        ordering = ["name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version", "environment"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_prompt_template_version",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["name"]),
            models.Index(fields=["environment"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.environment})"
