"""Durable per-row admission accounting shared by parallel graph nodes."""

import uuid

from django.db import models


class ExperimentRowBudget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "model_hub.ExperimentsTable", on_delete=models.CASCADE
    )
    row = models.ForeignKey("model_hub.Row", on_delete=models.CASCADE)
    variant = models.CharField(max_length=100)
    graph_execution_id = models.UUIDField(null=True, blank=True, db_index=True)
    limits = models.JSONField(default=dict)
    usage = models.JSONField(default=dict)
    reservations = models.JSONField(default=dict)
    deadline = models.DateTimeField(null=True, blank=True)
    exhausted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "row", "variant"],
                name="experiment_row_budget_variant",
            )
        ]
