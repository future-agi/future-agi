"""Durable bindings from a variant to its exact Temporal execution."""

from django.db import models


class ExperimentRunControl(models.Model):
    experiment_dataset = models.ForeignKey(
        "model_hub.ExperimentDatasetTable",
        on_delete=models.CASCADE,
        related_name="control_executions",
    )
    workflow_id = models.CharField(max_length=255)
    workflow_run_id = models.UUIDField(unique=True)
    config_id = models.UUIDField()
    kind = models.CharField(max_length=10)
    phase = models.CharField(max_length=16, default="RUNNING")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
