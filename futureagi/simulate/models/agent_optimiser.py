import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class AgentOptimiser(BaseModel):
    """
    Reusable agent optimiser configuration/instance.
    Represents a specific optimiser that can be used across different test executions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(
        max_length=255,
        help_text="Name of the agent optimiser",
    )

    description = models.TextField(
        null=True,
        blank=True,
        help_text="Description of what this optimiser does",
    )

    configuration = models.JSONField(
        null=True,
        blank=True,
        help_text="Configuration settings for this optimiser",
    )

    class Meta:
        db_table = "agent_optimiser"

    def __str__(self):
        return f"{self.name}"

    @property
    def total_runs(self):
        """Get total number of runs for this optimiser"""
        return self.runs.count()

    @property
    def successful_runs(self):
        """Get count of successful runs"""
        from simulate.models.agent_optimiser_run import AgentOptimiserRun

        return self.runs.filter(
            status=AgentOptimiserRun.OptimiserStatus.COMPLETED
        ).count()

    @property
    def latest_run(self):
        """Get the latest run for this optimiser"""
        return self.runs.order_by("-created_at").first()
