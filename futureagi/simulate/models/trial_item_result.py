import uuid

from django.db import models

from simulate.models.prompt_trial import PromptTrial
from simulate.models.test_execution import CallExecution
from tfc.utils.base_model import BaseModel


class TrialItemResult(BaseModel):
    """
    Represents the result for a single CallExecution within a PromptTrial.

    Each PromptTrial runs the prompt against multiple CallExecutions.
    This model stores the result for each CallExecution, including
    the overall score, reason, and the input/output of the simulated conversation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prompt_trial = models.ForeignKey(
        PromptTrial,
        on_delete=models.CASCADE,
        related_name="trial_items",
    )
    call_execution = models.ForeignKey(
        CallExecution,
        on_delete=models.CASCADE,
        related_name="trial_item_results",
    )
    score = models.FloatField()
    reason = models.TextField(null=True, blank=True)
    input_text = models.TextField(null=True, blank=True)
    output_text = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "trial_item_result"
        unique_together = ("prompt_trial", "call_execution")
        indexes = [
            models.Index(
                fields=["prompt_trial"],
                name="idx_trial_item_prompt_trial",
            ),
        ]

    def __str__(self):
        return f"{self.prompt_trial_id} - {self.call_execution_id} (score: {self.score:.2f})"
