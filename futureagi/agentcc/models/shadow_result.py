import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class AgentccShadowResult(BaseModel):
    """A single captured shadow comparison: production response vs shadow response."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "prism.AgentccShadowExperiment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="agentcc_shadow_results",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="agentcc_shadow_results",
        null=True,
        blank=True,
    )
    request_id = models.CharField(max_length=255, db_index=True)
    source_model = models.CharField(max_length=255)
    shadow_model = models.CharField(max_length=255)
    source_response = models.TextField(blank=True, default="")
    shadow_response = models.TextField(blank=True, default="")
    source_latency_ms = models.IntegerField(default=0)
    shadow_latency_ms = models.IntegerField(default=0)
    source_tokens = models.IntegerField(default=0)
    shadow_tokens = models.IntegerField(default=0)
    source_status_code = models.IntegerField(default=200)
    shadow_status_code = models.IntegerField(default=200)
    shadow_error = models.TextField(blank=True, default="")
    prompt_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        db_table = "agentcc_shadow_result"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["experiment", "-created_at"]),
        ]

    def __str__(self):
        return f"Shadow result {self.request_id} ({self.source_model} vs {self.shadow_model})"
