import uuid

from django.db import models

from simulate.models.agent_prompt_optimiser_run import AgentPromptOptimiserRun
from tfc.utils.base_model import BaseModel


class PromptTrial(BaseModel):
    """
    Represents a single trial in an agent prompt optimiser run.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_prompt_optimiser_run = models.ForeignKey(
        AgentPromptOptimiserRun,
        on_delete=models.CASCADE,
        related_name="trials",
    )
    trial_number = models.IntegerField()
    is_baseline = models.BooleanField(default=False)
    prompt = models.TextField(null=True, blank=True)
    average_score = models.FloatField()
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "prompt_trial"
        unique_together = ("agent_prompt_optimiser_run", "trial_number")
        ordering = ["trial_number"]

    def __str__(self):
        label = "Baseline" if self.is_baseline else f"Trial {self.trial_number}"
        return f"{self.agent_prompt_optimiser_run_id} - {label} (score: {self.average_score:.2f})"
