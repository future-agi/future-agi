import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from tfc.utils.base_model import BaseModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


class RunType(models.TextChoices):
    CONTINUOUS = "continuous", _("Continuous")
    HISTORICAL = "historical", _("Historical")


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
