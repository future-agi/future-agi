import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project
from tracer.models.trace import Trace


class TraceScanStatus(models.TextChoices):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanIssueConfidence(models.TextChoices):
    HIGH = "H"
    MEDIUM = "M"
    LOW = "L"


class TraceScanConfig(BaseModel):
    """Per-project scanning configuration."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="scan_config",
    )
    sampling_rate = models.FloatField(
        default=0.1, help_text="0.0-1.0, fraction of traces to scan"
    )
    enabled = models.BooleanField(default=True)
    scan_version = models.CharField(max_length=20, default="v7.2")

    class Meta:
        db_table = "tracer_trace_scan_config"

    def __str__(self):
        return f"ScanConfig({self.project.name}, rate={self.sampling_rate})"


class TraceScanResult(BaseModel):
    """One result per scanned trace — stores scanner output."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="scan_results",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="scan_results",
    )
    status = models.CharField(
        max_length=20,
        choices=TraceScanStatus.choices,
        default=TraceScanStatus.PENDING,
    )
    has_issues = models.BooleanField(default=False)
    key_moments = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"kevinified": "...", "verbatim": "..."}]',
    )
    meta = models.JSONField(
        default=dict,
        blank=True,
        help_text="{tools_called, tools_available, turn_count}",
    )
    scan_version = models.CharField(max_length=20, default="v7.2")
    error_message = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "tracer_trace_scan_result"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["trace"]),
            models.Index(fields=["status"]),
            models.Index(fields=["has_issues"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["trace"],
                condition=models.Q(deleted=False),
                name="unique_scan_result_per_trace",
            ),
        ]

    def __str__(self):
        return f"ScanResult({self.trace_id}, {self.status})"


class TraceScanIssue(BaseModel):
    """Individual issue found by the scanner — one-to-many from TraceScanResult."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scan_result = models.ForeignKey(
        TraceScanResult,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    category = models.CharField(
        max_length=100, help_text='Subcategory e.g. "Language-only"'
    )
    group = models.CharField(
        max_length=100, help_text='Scanner group e.g. "Tool Failures"'
    )
    fix_layer = models.CharField(
        max_length=50,
        help_text='"Tools" / "Prompt" / "Orchestration" / "Guardrails"',
    )
    confidence = models.CharField(
        max_length=2,
        choices=ScanIssueConfidence.choices,
        default=ScanIssueConfidence.MEDIUM,
    )
    brief = models.TextField(help_text="Short description of the issue")
    cluster = models.ForeignKey(
        "tracer.TraceErrorGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_issues",
        help_text="Assigned after clustering",
    )

    class Meta:
        db_table = "tracer_trace_scan_issue"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["scan_result"]),
            models.Index(fields=["group"]),
            models.Index(fields=["category"]),
            models.Index(fields=["fix_layer"]),
            models.Index(fields=["confidence"]),
        ]

    def __str__(self):
        return f"ScanIssue({self.category}, {self.confidence})"


class ProjectCapability(BaseModel):
    """Auto-discovered task types from root input clustering."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="capabilities",
    )
    title = models.CharField(
        max_length=255, help_text='Auto-generated: "Refund Processing"'
    )
    trace_count = models.IntegerField(default=0)
    success_rate = models.FloatField(
        default=0.0, help_text="0.0-1.0, traces without issues / total"
    )
    centroid_id = models.CharField(
        max_length=255,
        help_text="Reference to ClickHouse centroid for this capability cluster",
    )
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_project_capability"
        ordering = ["-trace_count"]
        indexes = [
            models.Index(fields=["project", "created_at"]),
            models.Index(fields=["project", "success_rate"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.project.name})"
