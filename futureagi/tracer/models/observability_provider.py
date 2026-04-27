import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class ProviderChoices(models.TextChoices):
    VAPI = "vapi", "Vapi"
    ELEVEN_LABS = "eleven_labs", "Eleven Labs"
    RETELL = "retell", "Retell"
    LIVEKIT = "livekit", "LiveKit"
    OTHERS = "others", "Others"


class ObservabilityProvider(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="observability_providers",
    )
    provider = models.CharField(
        max_length=255,
        choices=ProviderChoices.choices,
    )
    enabled = models.BooleanField(default=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="observability_providers",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="observability_providers",
    )
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True, null=True)

    class Meta:
        db_table = "tracer_observability_provider"
        indexes = [
            models.Index(
                fields=[
                    "project",
                    "last_fetched_at",
                    "enabled",
                    "organization",
                    "workspace",
                ]
            ),
        ]

    def __str__(self):
        return f"{self.provider} for {self.project.name}"
