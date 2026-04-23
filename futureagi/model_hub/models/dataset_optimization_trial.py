import uuid

from django.db import models

from model_hub.models.optimize_dataset import OptimizeDataset
from tfc.utils.base_model import BaseModel


class DatasetOptimizationTrial(BaseModel):
    """
    Represents a single trial in a dataset optimization run.
    Follows the same pattern as simulate.models.PromptTrial.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    optimization_run = models.ForeignKey(
        OptimizeDataset,
        on_delete=models.CASCADE,
        related_name="trials",
    )
    trial_number = models.IntegerField()
    is_baseline = models.BooleanField(default=False)
    prompt = models.TextField(null=True, blank=True)
    average_score = models.FloatField()
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "dataset_optimization_trial"
        unique_together = ("optimization_run", "trial_number")
        ordering = ["trial_number"]

    def __str__(self):
        label = "Baseline" if self.is_baseline else f"Trial {self.trial_number}"
        return f"{self.optimization_run_id} - {label} (score: {self.average_score:.2f})"
