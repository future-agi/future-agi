import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class TraceInvestigationJobState(models.TextChoices):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TraceInvestigationAttemptStatus(models.TextChoices):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TraceInvestigationGroupingStatus(models.TextChoices):
    PENDING = "pending"
    NOT_REQUIRED = "not_required"
    STALE = "stale"
    COMPLETED = "completed"
    FAILED = "failed"


class TraceInvestigationDelivery(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    topic = models.CharField(max_length=255)
    partition = models.PositiveIntegerField()
    offset = models.PositiveBigIntegerField()
    event_id = models.UUIDField()
    payload_digest = models.CharField(max_length=71)

    class Meta:
        db_table = "tracer_trace_investigation_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "event_id"],
                name="unique_trace_investigation_event",
            ),
        ]


class TraceInvestigationJob(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    trace_id = models.UUIDField()
    root_span_id = models.CharField(max_length=64)
    root_end_time = models.DateTimeField()
    generation = models.PositiveBigIntegerField(default=1)
    state = models.CharField(
        max_length=20,
        choices=TraceInvestigationJobState.choices,
        default=TraceInvestigationJobState.WAITING,
    )
    not_before = models.DateTimeField()

    class Meta:
        db_table = "tracer_trace_investigation_job"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "trace_id"],
                name="unique_trace_investigation_job",
            )
        ]
        indexes = [
            models.Index(
                fields=["state", "not_before"],
                name="trace_inv_job_due_idx",
            ),
            models.Index(
                fields=["project", "state"],
                name="trace_inv_job_project_idx",
            ),
        ]


class TraceInvestigationAttempt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        TraceInvestigationJob,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    generation = models.PositiveBigIntegerField()
    worker_id = models.CharField(max_length=255)
    engine_version = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20,
        choices=TraceInvestigationAttemptStatus.choices,
        default=TraceInvestigationAttemptStatus.CLAIMED,
    )
    lease_token_digest = models.CharField(max_length=64)
    lease_expires_at = models.DateTimeField()
    read_cutoff = models.DateTimeField()
    memory_snapshot_id = models.CharField(max_length=128)
    memory_digest = models.CharField(max_length=71)
    memory = models.JSONField(default=list)
    limits = models.JSONField(default=dict)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        db_table = "tracer_trace_investigation_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "generation"],
                name="unique_trace_investigation_attempt",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "lease_expires_at"],
                name="trace_inv_attempt_lease_idx",
            ),
            models.Index(
                fields=["job", "status"],
                name="trace_inv_attempt_job_idx",
            ),
        ]


class TraceInvestigationReport(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    job = models.ForeignKey(
        TraceInvestigationJob,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    attempt = models.OneToOneField(
        TraceInvestigationAttempt,
        on_delete=models.CASCADE,
        related_name="report",
    )
    idempotency_key = models.CharField(max_length=255)
    result_digest = models.CharField(max_length=71)
    result = models.JSONField()
    occurrences = models.JSONField(default=list)
    grouping_status = models.CharField(
        max_length=20,
        choices=TraceInvestigationGroupingStatus.choices,
    )
    active_projection_updated = models.BooleanField(default=False)

    class Meta:
        db_table = "tracer_trace_investigation_report"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_investigation_report_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["project", "grouping_status", "created_at"],
                name="trace_inv_report_group_idx",
            )
        ]
