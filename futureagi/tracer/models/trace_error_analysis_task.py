import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project
from tracer.models.trace import Trace


class TraceErrorTaskStatus:
    """Status choices for trace error task"""

    RUNNING = "running"  # Currently processing traces
    WAITING = "waiting"  # Active but no new traces to process
    PAUSED = "paused"  # User paused it

    CHOICES = [
        (RUNNING, "Running"),
        (WAITING, "Waiting"),
        (PAUSED, "Paused"),
    ]


class TraceErrorAnalysisTask(BaseModel):
    """
    One task per project that continuously analyzes traces based on sampling rate
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # One task per project
    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name="trace_error_task"
    )

    # Configuration (what user sets from UI)
    sampling_rate = models.FloatField(
        default=0.1, help_text="Percentage of traces to analyze (0-1)"  # 10% by default
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        default=TraceErrorTaskStatus.WAITING,  # Start in waiting state
        choices=TraceErrorTaskStatus.CHOICES,
    )

    # Execution tracking
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_trace_analyzed = models.ForeignKey(
        Trace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_analyzed_by_task",
    )

    # Statistics
    total_traces_analyzed = models.IntegerField(default=0)
    total_errors_found = models.IntegerField(default=0)
    failed_analyses = models.IntegerField(default=0)

    # Proper relationship for failed traces
    failed_traces = models.ManyToManyField(
        Trace, blank=True, related_name="failed_error_analyses"
    )

    class Meta:
        db_table = "tracer_trace_error_analysis_task"
        unique_together = [["project", "deleted"]]  # Only one active task per project

    def __str__(self):
        return f"Error Analysis Task for {self.project.name} - {self.status}"
