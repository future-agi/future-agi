import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


class RunType(models.TextChoices):
    CONTINUOUS = "continuous", _("Continuous")
    HISTORICAL = "historical", _("Historical")


class RowType(models.TextChoices):
    """The unit of evaluation a task runs on.

    Drives the dispatcher branch in ``process_eval_task`` (PR4) and the
    ``EvalLogger.target_type`` written by the corresponding evaluator
    (PR3/PR4). ``voice_calls`` is reserved for the existing voice pipeline,
    which has its own evaluation flow upstream of ``EvalTask``; the value
    is persisted here so the UI's voice-project tab round-trips on edit.
    """

    SPANS = "spans", _("Spans")
    TRACES = "traces", _("Traces")
    SESSIONS = "sessions", _("Sessions")
    VOICE_CALLS = "voiceCalls", _("Voice Calls")


class EvalTaskStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    FAILED = (
        "failed",
        _("Failed"),
    )
    PAUSED = "paused", _("Paused")
    DELETED = "deleted", _("Deleted")


class EvalTask(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="eval_tasks",
        blank=False,
        null=False,
    )
    name = models.CharField(max_length=255, blank=True, null=True)
    filters = models.JSONField(default=dict, blank=True, null=True)
    sampling_rate = models.FloatField(default=100.0, blank=True, null=True)
    last_run = models.DateTimeField(blank=True, null=True)
    spans_limit = models.IntegerField(default=1000, blank=True, null=True)
    run_type = models.CharField(
        max_length=255, choices=RunType.choices, blank=True, null=True
    )
    status = models.CharField(
        max_length=255, choices=EvalTaskStatus.choices, blank=True, null=True
    )
    start_time = models.DateTimeField(blank=True, null=True)
    end_time = models.DateTimeField(blank=True, null=True)
    evals_details = models.JSONField(default=list, blank=True, null=True)
    evals = models.ManyToManyField(
        CustomEvalConfig, related_name="eval_tasks", blank=True, null=True
    )
    failed_spans = models.JSONField(default=list, blank=True, null=True)
    # Unit of evaluation. ``spans`` is the historical default and matches
    # current dispatcher behaviour; ``traces`` and ``sessions`` are wired
    # up in PR4. Stored on every task so the UI's row-type tab survives
    # a round-trip through edit, and so PR4's dispatcher can branch on it.
    row_type = models.CharField(
        max_length=32,
        choices=RowType.choices,
        default=RowType.SPANS,
        db_index=True,
    )

    def __str__(self):
        return f"Eval Task {self.id}"

    class Meta:
        db_table = "tracer_eval_task"
        ordering = ["-created_at"]


class EvalTaskLogger(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_task = models.ForeignKey(
        EvalTask,
        on_delete=models.CASCADE,
        related_name="eval_task_loggers",
        blank=False,
        null=False,
    )
    offset = models.IntegerField(default=0, blank=True, null=True)
    errors = models.JSONField(default=list, blank=True, null=True)
    spanids_processed = models.JSONField(default=list, blank=True, null=True)
    status = models.CharField(
        max_length=255, choices=EvalTaskStatus.choices, blank=True, null=True
    )

    def __str__(self):
        return f"Eval Task Logger {self.id}"

    class Meta:
        db_table = "tracer_eval_task_logger"
        ordering = ["-created_at"]


MAX_EVAL_RUNS_IN_TASK = 50
EVAL_TASK_LOGGER_LIMIT = 10
