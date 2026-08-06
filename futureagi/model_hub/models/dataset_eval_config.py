from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

from tfc.utils.base_model import BaseModel


class DatasetEvalConfig(BaseModel):
    """
    Links a Dataset to one or more EvalTemplates so that new rows
    automatically trigger evaluation on insertion.

    Design rationale (issue #74):
    - One config per (dataset, eval_template) pair; multiple configs per dataset
      allow multiple evals per row.
    - debounce_seconds coalesces rapid bulk_create calls into a single
      Temporal batch workflow, preventing N workflows for an N-row import.
    - column_mapping is user-controlled (not guessed) so the Row→eval-input
      bridge is explicit and auditable.
    - max_concurrent propagates directly to RunEvaluationBatchWorkflow's
      concurrency cap, re-using existing Temporal controls.
    - source_config_id is stamped on every Evaluation row produced by
      auto-eval for provenance and disabling traceability.
    """

    dataset = models.ForeignKey(
        "model_hub.Dataset",
        on_delete=models.CASCADE,
        related_name="eval_configs",
    )
    eval_template = models.ForeignKey(
        "model_hub.EvalTemplate",
        on_delete=models.CASCADE,
        related_name="dataset_auto_configs",
    )
    enabled = models.BooleanField(default=True)

    # Coalesce bulk inserts: wait this many seconds after the last insert
    # before starting the Temporal workflow.
    debounce_seconds = models.PositiveIntegerField(default=30)

    # Max parallel eval activities inside the Temporal batch workflow.
    max_concurrent = models.PositiveIntegerField(default=5)

    # Explicit column→input field mapping.
    # Keys: dataset column names; values: eval template input field names.
    # Example: {"user_input": "input", "response": "output"}
    column_mapping = models.JSONField(default=dict)

    # Optional: only auto-eval rows whose metadata matches these tags.
    filter_tags = ArrayField(
        models.CharField(max_length=255),
        default=list,
        blank=True,
    )

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="dataset_eval_configs",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dataset_eval_configs",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_dataset_eval_configs",
    )

    class Meta:
        db_table = "model_hub_dataset_eval_config"
        constraints = [
            models.UniqueConstraint(
                condition=Q(deleted=False),
                fields=("dataset", "eval_template"),
                name="unique_active_dataset_eval_config",
            ),
        ]
        indexes = [
            models.Index(
                fields=["dataset", "enabled"],
                name="dataset_eval_config_dataset_enabled_idx",
            ),
            models.Index(
                fields=["organization"],
                name="dataset_eval_config_org_idx",
            ),
        ]

    def __str__(self):
        return f"DatasetEvalConfig({self.dataset_id} → {self.eval_template_id})"
