import uuid

from django.db import models

from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.evals_metric import UserEvalMetric
from tfc.utils.base_model import BaseModel


class DatasetOptimizationTrialItem(BaseModel):
    """
    Represents the result for a single dataset row within a DatasetOptimizationTrial.

    Each trial runs the prompt against multiple dataset rows.
    This model stores the result for each row, including
    the overall score, reason, and the input/output of the evaluation.

    Equivalent to simulate.models.TrialItemResult.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trial = models.ForeignKey(
        DatasetOptimizationTrial,
        on_delete=models.CASCADE,
        related_name="trial_items",
    )
    # Row identifier from the dataset (can be row index or UUID)
    row_id = models.CharField(max_length=255)
    score = models.FloatField()
    reason = models.TextField(null=True, blank=True)
    input_text = models.TextField(null=True, blank=True)
    output_text = models.TextField(null=True, blank=True)
    filled_prompt = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "dataset_optimization_trial_item"
        unique_together = ("trial", "row_id")
        indexes = [
            models.Index(
                fields=["trial"],
                name="idx_ds_opt_trial_item_trial",
            ),
        ]

    def __str__(self):
        return f"{self.trial_id} - row:{self.row_id} (score: {self.score:.2f})"


class DatasetOptimizationItemEvaluation(BaseModel):
    """
    Represents an individual evaluation score from a specific evaluator.

    Each TrialItem may have multiple evaluations (one per configured eval metric),
    e.g., is_helpful, accuracy, etc.
    This provides granular breakdowns of how each evaluation metric scored.

    Equivalent to simulate.models.ComponentEvaluation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trial_item = models.ForeignKey(
        DatasetOptimizationTrialItem,
        on_delete=models.CASCADE,
        related_name="evaluations",
    )
    # Reference to the UserEvalMetric (which contains the EvalTemplate)
    eval_metric = models.ForeignKey(
        UserEvalMetric,
        on_delete=models.CASCADE,
        related_name="dataset_optimization_evaluations",
    )
    score = models.FloatField()
    reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "dataset_optimization_item_evaluation"
        indexes = [
            models.Index(
                fields=["trial_item", "eval_metric"],
                name="idx_dsopt_eval_item_metric",
            ),
            models.Index(
                fields=["eval_metric"],
                name="idx_dsopt_eval_metric",
            ),
        ]

    def __str__(self):
        eval_name = (
            self.eval_metric.template.name if self.eval_metric.template else "unknown"
        )
        return f"{self.trial_item_id} - {eval_name} (score: {self.score:.2f})"
