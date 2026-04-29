import uuid

from django.db import models

from model_hub.models.optimize_dataset import OptimizeDataset
from tfc.utils.base_model import BaseModel


class DatasetOptimizationStep(BaseModel):
    """
    Represents a single step within a dataset optimization run.
    Follows the same pattern as simulate.models.AgentPromptOptimiserRunStep.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    optimization_run = models.ForeignKey(
        OptimizeDataset, on_delete=models.CASCADE, related_name="steps"
    )
    step_number = models.IntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "dataset_optimization_step"
        unique_together = ("optimization_run", "step_number")

    def __str__(self):
        return f"{self.optimization_run.id} - {self.name} - {self.status}"

    def mark_as_running(self):
        """Mark the optimization step as running"""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_completed(self):
        """Mark the optimization step as completed"""
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_failed(self):
        """Mark the optimization step as failed"""
        self.status = self.Status.FAILED
        self.save(update_fields=["status", "updated_at"])
