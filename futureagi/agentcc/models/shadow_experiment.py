import uuid

from django.conf import settings
from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccShadowExperiment(BaseModel):
    """Represents a shadow testing experiment that mirrors production traffic to a secondary model."""

    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_COMPLETED, "Completed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_shadow_experiments",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_shadow_experiments",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    source_model = models.CharField(
        max_length=255, help_text="Production model being tested against"
    )
    shadow_model = models.CharField(
        max_length=255, help_text="Shadow model receiving mirrored traffic"
    )
    shadow_provider = models.CharField(
        max_length=128, help_text="Provider for the shadow model"
    )
    sample_rate = models.FloatField(
        default=0.1, help_text="Fraction of traffic to mirror (0.0–1.0)"
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE
    )
    total_comparisons = models.IntegerField(default=0)
    config = models.JSONField(null=True, blank=True, help_text="Extra configuration")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agentcc_shadow_experiments",
    )

    class Meta:
        db_table = "agentcc_shadow_experiment"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                condition=models.Q(deleted=False),
                name="unique_agentcc_shadow_experiment_name",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.source_model} → {self.shadow_model})"
