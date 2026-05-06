import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.base_model import BaseModel


class ErrorLocalizerStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class ErrorLocalizerSource(models.TextChoices):
    DATASET = "dataset", "Dataset"
    OBSERVE = "observe", "Observe"
    PLAYGROUND = "playground", "Playground"
    SIMULATE = "simulate", "Simulate"
    STANDALONE = "standalone", "Standalone"


class ErrorLocalizerTask(BaseModel):
    """
    Model to track error localization tasks and their results.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate, on_delete=models.CASCADE, null=True, blank=True
    )
    source = models.CharField(
        max_length=255,
        choices=ErrorLocalizerSource.choices,
        default=ErrorLocalizerSource.DATASET,
    )
    source_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ErrorLocalizerStatus.choices,
        default=ErrorLocalizerStatus.PENDING,
    )

    input_data = models.JSONField(default=dict, blank=True)
    input_keys = models.JSONField(default=list, blank=True)
    input_types = models.JSONField(default=dict, blank=True)
    eval_result = models.JSONField(default=dict, blank=True)
    eval_explanation = models.JSONField(default=dict, blank=True)
    rule_prompt = models.TextField(null=True, blank=True)

    error_analysis = models.JSONField(default=dict, blank=True)
    selected_input_key = models.CharField(max_length=2000, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="error_localizer_tasks",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "error_localizer_task"
        indexes = [models.Index(fields=["source_id"]), models.Index(fields=["status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["source_id"],
                condition=models.Q(deleted=False),
                name="unique_source_id",
            ),
        ]

    def __str__(self):
        return f"ErrorLocalizerTask {self.id} - {self.status}"

    def mark_as_running(self):
        """Mark the task as running."""
        self.status = ErrorLocalizerStatus.RUNNING
        self.save(update_fields=["status"])

    def mark_as_completed(self, error_analysis, selected_input_key):
        """Mark the task as completed with results."""
        self.status = ErrorLocalizerStatus.COMPLETED
        self.error_analysis = error_analysis
        self.selected_input_key = selected_input_key
        self.save(update_fields=["status", "error_analysis", "selected_input_key"])

    def mark_as_failed(self, error_message):
        """Mark the task as failed with an error message."""
        self.status = ErrorLocalizerStatus.FAILED
        self.error_message = error_message
        self.save(update_fields=["status", "error_message"])

    def mark_as_skipped(self, reason):
        """Mark the task as skipped with a reason."""
        self.status = ErrorLocalizerStatus.SKIPPED
        self.error_message = reason
        self.save(update_fields=["status", "error_message"])
