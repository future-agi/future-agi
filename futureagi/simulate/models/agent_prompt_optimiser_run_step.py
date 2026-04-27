import uuid

from django.db import models

from simulate.models.agent_prompt_optimiser_run import AgentPromptOptimiserRun
from tfc.utils.base_model import BaseModel


class AgentPromptOptimiserRunStep(BaseModel):
    """
    Represents a single step within an agent prompt optimiser run.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_prompt_optimiser_run = models.ForeignKey(
        AgentPromptOptimiserRun, on_delete=models.CASCADE, related_name="steps"
    )
    step_number = models.IntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "agent_prompt_optimiser_run_step"
        unique_together = ("agent_prompt_optimiser_run", "step_number")

    def __str__(self):
        return f"{self.agent_prompt_optimiser_run.id} - {self.name} - {self.status}"

    def mark_as_running(self):
        """Mark the optimiser run step as running"""
        self.status = self.Status.RUNNING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_completed(self):
        """Mark the optimiser run step as completed"""
        self.status = self.Status.COMPLETED
        self.save(update_fields=["status", "updated_at"])

    def mark_as_failed(self):
        """Mark the optimiser run step as failed"""
        self.status = self.Status.FAILED
        self.save(update_fields=["status", "updated_at"])
