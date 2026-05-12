import uuid

from django.db import models

from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.trial_item_result import TrialItemResult
from tfc.utils.base_model import BaseModel


class ComponentEvaluation(BaseModel):
    """
    Represents an individual evaluation score from component_evals.

    Each TrialItemResult may have multiple component evaluations,
    e.g., levenshtein_similarity, rouge_score, etc.
    This provides granular breakdowns of how each evaluation metric scored.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trial_item_result = models.ForeignKey(
        TrialItemResult,
        on_delete=models.CASCADE,
        related_name="component_evaluations",
    )
    eval_config = models.ForeignKey(
        SimulateEvalConfig,
        on_delete=models.CASCADE,
        related_name="component_evaluations",
    )
    score = models.FloatField()
    reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "component_evaluation"
        indexes = [
            models.Index(
                fields=["trial_item_result", "eval_config"],
                name="idx_comp_eval_trial_config",
            ),
            models.Index(
                fields=["eval_config"],
                name="idx_comp_eval_config",
            ),
        ]

    def __str__(self):
        return f"{self.trial_item_result.id} - {self.eval_config.name} (score: {self.score:.2f})"
