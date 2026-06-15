import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_session import TraceSession


class TraceErrorAnalysisStatus(models.TextChoices):
    """Status for trace error analysis processing"""

    PENDING = ("pending",)  # Not yet processed
    PROCESSING = ("processing",)  # Currently being analyzed
    COMPLETED = ("completed",)  # Successfully analyzed
    SKIPPED = ("skipped",)  # Skipped analysis
    FAILED = ("failed",)


class Trace(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="traces",
        blank=False,
        null=False,
    )
    project_version = models.ForeignKey(
        ProjectVersion,
        on_delete=models.CASCADE,
        related_name="traces",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=2000, blank=True, null=True)
    metadata = models.JSONField(null=True, blank=True)
    input = models.JSONField(null=True, blank=True)
    output = models.JSONField(null=True, blank=True)
    error = models.JSONField(null=True, blank=True)
    session = models.ForeignKey(
        TraceSession,
        on_delete=models.CASCADE,
        related_name="traces",
        blank=True,
        null=True,
        # CH scale: SCALE_ARCHITECTURE.md §9a. Decoupled (no DB FK) so a Trace can
        # carry a DETERMINISTIC trace_session_id (CH-derived-dimensions, DESIGN §3)
        # whose TraceSession PG row no longer exists post-P3b-flip — ingestion stamps
        # the bare id (`trace.session_id = deterministic_trace_session_id(...)`) and
        # there is no PG `trace_session` row to satisfy a constraint against. The
        # other telemetry FKs (span→end_user, span→trace, eval_logger→*) were
        # decoupled in migration 0080; this one was missed and is forced by the flip.
        db_constraint=False,
    )
    external_id = models.CharField(max_length=255, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    error_analysis_status = models.CharField(
        max_length=20,
        default=TraceErrorAnalysisStatus.PENDING,
        choices=TraceErrorAnalysisStatus.choices,
    )

    class Meta:
        db_table = "tracer_trace"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["project_version"]),
            models.Index(fields=["session"]),
            models.Index(fields=["external_id"]),
        ]
