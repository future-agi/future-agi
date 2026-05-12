import uuid

from django.db import models
from django.utils import timezone

from simulate.models.agent_optimiser import AgentOptimiser
from tfc.utils.base_model import BaseModel


class AgentOptimiserRun(BaseModel):
    """
    Individual execution/run of an agent optimiser.
    Tracks the complete lifecycle of a single optimization run.
    """

    class OptimiserStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    agent_optimiser = models.ForeignKey(
        AgentOptimiser, on_delete=models.CASCADE, related_name="runs"
    )

    status = models.CharField(
        max_length=20,
        choices=OptimiserStatus.choices,
        default=OptimiserStatus.PENDING,
    )

    input_data = models.JSONField(default=dict, blank=True)

    result = models.JSONField(null=True, blank=True)

    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "agent_optimiser_run"
        indexes = [
            models.Index(fields=["status"], name="idx_optimiser_run_status"),
        ]

    def __str__(self):
        return f"{self.agent_optimiser.name} - {self.status}"

    def mark_as_running(self):
        """Mark the optimiser run as running"""
        self.status = self.OptimiserStatus.RUNNING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_completed(self, result=None):
        """Mark the optimiser run as completed"""
        self.status = self.OptimiserStatus.COMPLETED
        if result:
            self.result = result
        self.save(update_fields=["status", "result", "updated_at"])

    def mark_as_failed(self, error_info=None):
        """Mark the optimiser run as failed"""
        self.status = self.OptimiserStatus.FAILED
        if error_info:
            # Store error info in metadata
            if not self.metadata:
                self.metadata = {}
            self.metadata["error"] = error_info
        self.save(update_fields=["status", "metadata", "updated_at"])
