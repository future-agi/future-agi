import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.utils.base_model import BaseModel


class PerformanceReport(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(
        AIModel, on_delete=models.CASCADE, related_name="performance_reports"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="performance_reports"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="performance_reports",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    datasets = models.JSONField(default=list)
    filters = models.JSONField(default=list)
    breakdown = models.JSONField(default=list)
    aggregation = models.CharField(max_length=255)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()

    def __str__(self):
        return self.name
