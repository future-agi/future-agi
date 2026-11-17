"""Immutable experiment-level evaluator bindings and durable run outcomes."""

import uuid

from django.db import models


class ExperimentSummaryBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "model_hub.ExperimentsTable",
        on_delete=models.CASCADE,
        related_name="summary_bindings",
    )
    name = models.CharField(max_length=255)
    version = models.ForeignKey(
        "model_hub.EvalTemplateVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    definition = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class ExperimentSummaryResult(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        "model_hub.ExperimentRun",
        on_delete=models.CASCADE,
        related_name="summary_results",
    )
    binding = models.ForeignKey(
        ExperimentSummaryBinding,
        on_delete=models.CASCADE,
        related_name="results",
    )
    version = models.ForeignKey(
        "model_hub.EvalTemplateVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    score = models.FloatField(null=True)
    reason = models.TextField(default="", blank=True)
    metadata = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, default="", blank=True)
    attempt = models.PositiveIntegerField(default=0)
    dispatched = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "binding", "version"],
                name="experiment_summary_run_binding_version_unique",
            ),
        ]
