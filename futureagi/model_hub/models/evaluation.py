import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.user import User
from model_hub.models.error_localizer_model import ErrorLocalizerTask
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.base_model import BaseModel


class StatusChoices(models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Evaluation(BaseModel):
    """
    Model to store evaluation configuration, execution data, and results.
    This model handles both the input data required to run an evaluation
    AND stores the complete results from the evaluation process.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="evaluations",
        null=True,
        blank=True,
    )
    eval_template = models.ForeignKey(EvalTemplate, on_delete=models.CASCADE)

    model_name = models.CharField(
        max_length=2000, null=True, blank=True, help_text="Model used for evaluation"
    )
    input_data = models.JSONField(
        null=True, blank=True, help_text="Input data for the evaluation"
    )
    eval_config = models.JSONField(
        null=True, blank=True, help_text="Configuration parameters for evaluation"
    )

    # --- EVALUATION RESULTS ---
    data = models.JSONField(
        null=True, blank=True, help_text="Raw data from evaluation result"
    )
    reason = models.TextField(null=True, blank=True)
    runtime = models.FloatField(null=True, blank=True, help_text="Runtime in seconds")
    model = models.CharField(max_length=2000, null=True, blank=True)
    metrics = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    output_type = models.CharField(max_length=50, null=True, blank=True)
    value = models.TextField(null=True, blank=True, help_text="Formatted output value")

    # --- STATUS AND ERROR TRACKING ---
    status = models.CharField(
        max_length=50, choices=StatusChoices.choices, default=StatusChoices.PENDING
    )
    error_message = models.TextField(null=True, blank=True)

    # --- OUTPUT TYPE SPECIFIC FIELDS ---
    output_bool = models.BooleanField(null=True, blank=True)
    output_float = models.FloatField(null=True, blank=True)
    output_str = models.TextField(null=True, blank=True)
    output_str_list = models.JSONField(null=True, blank=True)

    # --- Tracing ---
    trace_data = models.JSONField(null=True, blank=True)

    # --- Error Localizer ---
    error_localizer_enabled = models.BooleanField(default=False)
    error_localizer = models.ForeignKey(
        ErrorLocalizerTask, on_delete=models.CASCADE, null=True, blank=True
    )

    # --- Composite Eval (Phase 7 wiring) ---
    parent_evaluation = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="child_evaluations",
        help_text=(
            "When this row is a child result inside a composite eval run, "
            "points to the aggregate (parent) Evaluation row. Null for single "
            "evals and for composites with aggregation_enabled=False."
        ),
    )

    class Meta:
        db_table = "model_hub_evaluation"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["organization"]),
            models.Index(fields=["eval_template"]),
            models.Index(fields=["status"]),
            models.Index(fields=["error_localizer_enabled"]),
            models.Index(fields=["error_localizer"]),
            models.Index(fields=["parent_evaluation"]),
        ]

    def __str__(self):
        template_name = self.eval_template.name if self.eval_template else "Unknown"
        return f"Evaluation {self.id} - {template_name} ({self.status})"

    def save(self, *args, **kwargs):
        """Populate typed output columns via the shared eval-engine dispatch.
        Additive only: never overwrites existing non-None columns."""
        if self.value is not None:
            try:
                from evaluations.engine.normalize import resolve_eval_axes

                template_config: dict = {}
                multi_choice = False
                try:
                    if self.eval_template_id is not None:
                        template_config = self.eval_template.config or {}
                        multi_choice = bool(self.eval_template.multi_choice)
                except (AttributeError, EvalTemplate.DoesNotExist):
                    pass

                config_output = (
                    template_config.get("output") or self.output_type or "score"
                )
                axes = resolve_eval_axes(self.value, config_output, multi_choice)

                if self.output_bool is None and axes["output_pass"] is not None:
                    self.output_bool = axes["output_pass"]
                if self.output_float is None and axes["output_score"] is not None:
                    self.output_float = axes["output_score"]
                if self.output_str_list in (None, []) and axes["output_choices"] is not None:
                    self.output_str_list = axes["output_choices"]

                if (
                    self.output_bool is None
                    and self.output_float is None
                    and self.output_str_list in (None, [])
                    and self.output_str is None
                ):
                    self.output_str = str(self.value)

            except Exception:
                if self.output_str is None:
                    self.output_str = str(self.value)

        super().save(*args, **kwargs)

    def update_with_result(self, eval_result):
        """
        Update the evaluation instance with results from the evaluation execution.
        """
        self.data = eval_result.get("data")
        self.reason = eval_result.get("reason", "")
        self.runtime = eval_result.get("runtime", 0)
        self.model = eval_result.get("model")
        self.metrics = eval_result.get("metrics")
        self.metadata = eval_result.get("metadata")
        self.output_type = eval_result.get("output")
        self.value = eval_result.get("value")

        if eval_result.get("failure"):
            self.status = StatusChoices.FAILED
            self.error_message = eval_result.get("failure")
        else:
            self.status = StatusChoices.COMPLETED

        self.save()

    def mark_as_processing(self):
        """Mark evaluation as currently processing."""
        self.status = StatusChoices.PROCESSING
        self.save(update_fields=["status", "updated_at"])

    def mark_as_failed(self, error_message):
        """Mark evaluation as failed with error message."""
        self.status = StatusChoices.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message", "updated_at"])
