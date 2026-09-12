import uuid

from django.db import models

from accounts.models import Organization, User
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


class TraceInvestigationUsageStatus(models.TextChoices):
    PENDING = "pending"
    EMITTED = "emitted"
    UNPRICED = "unpriced"
    SKIPPED = "skipped"


class TraceInvestigationFeedbackType(models.TextChoices):
    CONFIRM_FINDING = "confirm_finding"
    FALSE_POSITIVE = "false_positive"
    ATTRIBUTION_CORRECTION = "attribution_correction"
    GROUPING_MERGE = "grouping_merge"
    GROUPING_SPLIT = "grouping_split"


class TraceInvestigationMemoryStatus(models.TextChoices):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class TraceInvestigationMemoryAction(models.TextChoices):
    PROMOTE = "promote"
    ROLLBACK = "rollback"


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
                fields=["topic", "partition", "offset"],
                name="unique_trace_investigation_delivery",
            ),
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


class TraceInvestigationUsageReceipt(BaseModel):
    """Durable, tenant-pinned outbox for one immutable Omega report's cost."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.OneToOneField(
        TraceInvestigationReport,
        on_delete=models.CASCADE,
        related_name="usage_receipt",
    )
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    event_id = models.UUIDField(unique=True)
    raw_cost_usd = models.DecimalField(
        max_digits=30,
        decimal_places=18,
        null=True,
        blank=True,
    )
    credit_amount = models.DecimalField(
        max_digits=30,
        decimal_places=18,
        null=True,
        blank=True,
    )
    event_properties = models.JSONField(default=dict)
    event_payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=TraceInvestigationUsageStatus.choices,
        default=TraceInvestigationUsageStatus.PENDING,
    )
    status_reason = models.CharField(max_length=64, blank=True)
    delivery_attempts = models.PositiveIntegerField(default=0)
    emitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_trace_investigation_usage_receipt"
        indexes = [
            models.Index(
                fields=["status", "updated_at"],
                name="trace_inv_usage_pending_idx",
            ),
            models.Index(
                fields=["organization", "created_at"],
                name="trace_inv_usage_org_idx",
            ),
        ]


class TraceInvestigationFeedback(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    report = models.ForeignKey(
        TraceInvestigationReport,
        on_delete=models.CASCADE,
        related_name="feedback_events",
    )
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT)
    occurrence_id = models.UUIDField()
    finding_id = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=255)
    feedback_type = models.CharField(
        max_length=40,
        choices=TraceInvestigationFeedbackType.choices,
    )
    comment = models.TextField(blank=True)
    review_state = models.CharField(max_length=20, default="reviewed")
    learning_event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    content_digest = models.CharField(max_length=71)

    class Meta:
        db_table = "tracer_trace_investigation_feedback"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_inv_feedback_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["project", "created_at"],
                name="trace_inv_feedback_project_idx",
            )
        ]


class TraceInvestigationMemorySnapshot(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="omega_memory_snapshots",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="candidates",
    )
    idempotency_key = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=TraceInvestigationMemoryStatus.choices,
    )
    digest = models.CharField(max_length=71)
    entries = models.JSONField(default=list)
    source_feedback_ids = models.JSONField(default=list)

    class Meta:
        db_table = "tracer_trace_investigation_memory"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_inv_memory_key",
            ),
            models.UniqueConstraint(
                fields=["project", "digest"],
                name="unique_trace_inv_memory_digest",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status=TraceInvestigationMemoryStatus.ACTIVE),
                name="unique_trace_inv_active_memory",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "status", "created_at"],
                name="trace_inv_memory_project_idx",
            )
        ]


class TraceInvestigationMemoryEvaluation(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    candidate = models.ForeignKey(
        TraceInvestigationMemorySnapshot,
        on_delete=models.CASCADE,
        related_name="evaluations",
    )
    idempotency_key = models.CharField(max_length=255)
    candidate_digest = models.CharField(max_length=71)
    cohort_id = models.CharField(max_length=255)
    metrics = models.JSONField()
    passed = models.BooleanField()
    holdout_disjoint = models.BooleanField()
    content_digest = models.CharField(max_length=71)

    class Meta:
        db_table = "tracer_trace_investigation_memory_evaluation"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_inv_memory_eval_key",
            )
        ]


class TraceInvestigationMemoryPromotion(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    from_snapshot = models.ForeignKey(
        TraceInvestigationMemorySnapshot,
        on_delete=models.PROTECT,
        related_name="outgoing_promotions",
    )
    to_snapshot = models.ForeignKey(
        TraceInvestigationMemorySnapshot,
        on_delete=models.PROTECT,
        related_name="incoming_promotions",
    )
    evaluation = models.ForeignKey(
        TraceInvestigationMemoryEvaluation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    action = models.CharField(
        max_length=20,
        choices=TraceInvestigationMemoryAction.choices,
    )
    idempotency_key = models.CharField(max_length=255)
    content_digest = models.CharField(max_length=71)

    class Meta:
        db_table = "tracer_trace_investigation_memory_promotion"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_inv_memory_promo_key",
            )
        ]


class TraceInvestigationReconciliationCursor(BaseModel):
    """Crash-safe ClickHouse discovery cursor for one Omega project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, null=True, blank=True
    )
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="omega_reconciliation_cursor",
    )
    completed_through = models.DateTimeField(null=True, blank=True)
    window_lower = models.DateTimeField(null=True, blank=True)
    window_upper = models.DateTimeField(null=True, blank=True)
    after_created_at = models.DateTimeField(null=True, blank=True)
    after_trace_id = models.CharField(max_length=64, null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_trace_investigation_reconciliation_cursor"
        indexes = [
            models.Index(
                fields=["completed_through", "project"],
                name="trace_inv_reconcile_due_idx",
            )
        ]
