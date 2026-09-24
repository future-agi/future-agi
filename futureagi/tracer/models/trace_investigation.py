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


class TraceInvestigationSource(models.TextChoices):
    OMEGA = "omega"
    LEGACY_SCAN = "legacy_scan"


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
    current_report = models.ForeignKey(
        "tracer.TraceInvestigationReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_for_jobs",
    )

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
    trace_id = models.UUIDField()
    source = models.CharField(max_length=20, choices=TraceInvestigationSource.choices)
    source_record_id = models.UUIDField(null=True, blank=True)
    source_version = models.CharField(max_length=64, null=True, blank=True)
    recorded_at = models.DateTimeField()
    is_current = models.BooleanField(default=False)
    error_message = models.TextField(null=True, blank=True)
    turn_count = models.PositiveIntegerField(null=True, blank=True)
    has_issues = models.BooleanField(null=True, blank=True)
    job = models.ForeignKey(
        TraceInvestigationJob,
        on_delete=models.CASCADE,
        related_name="reports",
        null=True,
        blank=True,
    )
    attempt = models.OneToOneField(
        TraceInvestigationAttempt,
        on_delete=models.CASCADE,
        related_name="report",
        null=True,
        blank=True,
    )
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    result_digest = models.CharField(max_length=71, null=True, blank=True)
    contract_version = models.CharField(max_length=64, null=True, blank=True)
    evidence_digest = models.CharField(max_length=71, null=True, blank=True)
    execution_status = models.CharField(max_length=20)
    outcome = models.CharField(max_length=20, null=True, blank=True)
    coverage_scope = models.CharField(max_length=255, null=True, blank=True)
    observed_span_count = models.PositiveIntegerField(null=True, blank=True)
    read_complete = models.BooleanField(null=True, blank=True)
    future_arrivals_known = models.BooleanField(null=True, blank=True)
    model_calls = models.PositiveIntegerField(null=True, blank=True)
    input_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    output_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=20, decimal_places=9, null=True)
    cost_status = models.CharField(max_length=64, null=True, blank=True)
    grouping_status = models.CharField(
        max_length=20,
        choices=TraceInvestigationGroupingStatus.choices,
    )

    class Meta:
        db_table = "tracer_trace_investigation_report"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "source_record_id"],
                condition=models.Q(source_record_id__isnull=False),
                name="unique_inv_source_record",
            ),
            models.UniqueConstraint(
                fields=["project", "trace_id"],
                condition=models.Q(is_current=True, deleted=False),
                name="unique_current_trace_investigation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source=TraceInvestigationSource.LEGACY_SCAN,
                        source_record_id__isnull=False,
                        job__isnull=True,
                        attempt__isnull=True,
                    )
                    | models.Q(
                        source=TraceInvestigationSource.OMEGA,
                        source_record_id__isnull=True,
                        job__isnull=False,
                        attempt__isnull=False,
                        idempotency_key__isnull=False,
                        result_digest__isnull=False,
                        contract_version__isnull=False,
                        evidence_digest__isnull=False,
                        outcome__isnull=False,
                        coverage_scope__isnull=False,
                        observed_span_count__isnull=False,
                        read_complete__isnull=False,
                        future_arrivals_known__isnull=False,
                        model_calls__isnull=False,
                        input_tokens__isnull=False,
                        output_tokens__isnull=False,
                        cost_status__isnull=False,
                    )
                ),
                name="valid_inv_report_source_fields",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="unique_trace_investigation_report_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["project", "grouping_status", "created_at"],
                name="trace_inv_report_group_idx",
            )
        ]


class TraceInvestigationRequirementCheck(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport,
        on_delete=models.CASCADE,
        related_name="requirement_checks",
    )
    requirement_id = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField()
    requirement = models.TextField()
    status = models.CharField(max_length=64)

    class Meta:
        db_table = "tracer_trace_investigation_requirement_check"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "requirement_id"], name="unique_inv_requirement_id"
            )
        ]


class TraceInvestigationFinding(BaseModel):
    id = models.UUIDField(primary_key=True, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport, on_delete=models.CASCADE, related_name="findings"
    )
    finding_id = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField()
    kind = models.CharField(max_length=64, null=True, blank=True)
    statement = models.TextField()
    recovery = models.CharField(max_length=64, null=True, blank=True)
    source_finding_id = models.UUIDField(null=True, blank=True, unique=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    group_label = models.CharField(max_length=100, null=True, blank=True)
    fix_layer = models.CharField(max_length=50, null=True, blank=True)
    confidence = models.CharField(max_length=2, null=True, blank=True)
    requirement = models.ForeignKey(
        TraceInvestigationRequirementCheck,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="findings",
    )
    cluster = models.ForeignKey(
        "tracer.TraceErrorGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investigation_findings",
    )

    class Meta:
        db_table = "tracer_trace_investigation_finding"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "finding_id"], name="unique_inv_finding_id"
            )
        ]


class TraceInvestigationKeyMoment(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport, on_delete=models.CASCADE, related_name="key_moments"
    )
    ordinal = models.PositiveIntegerField()
    kevinified = models.TextField(blank=True)
    verbatim = models.TextField(blank=True)
    role = models.CharField(max_length=64, blank=True)
    span_id = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=64, blank=True)
    is_failure = models.BooleanField(default=False)

    class Meta:
        db_table = "tracer_trace_investigation_key_moment"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "ordinal"], name="unique_inv_key_moment_ordinal"
            )
        ]


class TraceInvestigationTool(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport, on_delete=models.CASCADE, related_name="tools"
    )
    ordinal = models.PositiveIntegerField()
    role = models.CharField(max_length=16)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "tracer_trace_investigation_tool"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "role", "ordinal"], name="unique_inv_tool_ordinal"
            )
        ]


class TraceInvestigationEvidenceReceipt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport,
        on_delete=models.CASCADE,
        related_name="evidence_receipts",
    )
    evidence_id = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField()
    span_id = models.CharField(max_length=64)
    parent_span_id = models.CharField(max_length=64, null=True, blank=True)
    excerpt = models.TextField()
    end_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_trace_investigation_evidence_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "evidence_id"], name="unique_inv_evidence_id"
            )
        ]


class TraceInvestigationAttribution(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    finding = models.ForeignKey(
        TraceInvestigationFinding, on_delete=models.CASCADE, related_name="attributions"
    )
    role = models.CharField(max_length=16)
    status = models.CharField(max_length=16)
    span_id = models.CharField(max_length=64, null=True, blank=True)
    explanation = models.CharField(max_length=600, blank=True, default="")

    class Meta:
        db_table = "tracer_trace_investigation_attribution"
        constraints = [
            models.UniqueConstraint(
                fields=["finding", "role"], name="unique_inv_finding_role"
            )
        ]


class TraceInvestigationFindingEvidence(BaseModel):
    finding = models.ForeignKey(TraceInvestigationFinding, on_delete=models.CASCADE)
    evidence = models.ForeignKey(
        TraceInvestigationEvidenceReceipt, on_delete=models.CASCADE
    )

    class Meta:
        db_table = "tracer_trace_investigation_finding_evidence"
        constraints = [
            models.UniqueConstraint(
                fields=["finding", "evidence"], name="unique_inv_finding_evidence"
            )
        ]


class TraceInvestigationAttributionEvidence(BaseModel):
    attribution = models.ForeignKey(
        TraceInvestigationAttribution, on_delete=models.CASCADE
    )
    evidence = models.ForeignKey(
        TraceInvestigationEvidenceReceipt, on_delete=models.CASCADE
    )

    class Meta:
        db_table = "tracer_trace_investigation_attribution_evidence"
        constraints = [
            models.UniqueConstraint(
                fields=["attribution", "evidence"],
                name="unique_inv_attribution_evidence",
            )
        ]


class TraceInvestigationRequirementEvidence(BaseModel):
    requirement = models.ForeignKey(
        TraceInvestigationRequirementCheck, on_delete=models.CASCADE
    )
    evidence = models.ForeignKey(
        TraceInvestigationEvidenceReceipt, on_delete=models.CASCADE
    )

    class Meta:
        db_table = "tracer_trace_investigation_requirement_evidence"
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "evidence"],
                name="unique_inv_requirement_evidence",
            )
        ]


class TraceInvestigationVerificationReceipt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport,
        on_delete=models.CASCADE,
        related_name="verification_receipts",
    )
    receipt_id = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField()
    executed = models.BooleanField()

    class Meta:
        db_table = "tracer_trace_investigation_verification_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "receipt_id"], name="unique_inv_verification_id"
            )
        ]


class TraceInvestigationGatewayCall(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        TraceInvestigationReport, on_delete=models.CASCADE, related_name="gateway_calls"
    )
    ordinal = models.PositiveIntegerField()
    request_id = models.CharField(max_length=255, null=True, blank=True)
    model_used = models.CharField(max_length=255)
    cost_usd = models.DecimalField(max_digits=20, decimal_places=9, null=True)
    input_tokens = models.PositiveBigIntegerField(null=True)
    output_tokens = models.PositiveBigIntegerField(null=True)
    total_tokens = models.PositiveBigIntegerField(null=True)
    cached_input_tokens = models.PositiveBigIntegerField(null=True)
    reasoning_output_tokens = models.PositiveBigIntegerField(null=True)
    cache_status = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(max_length=64, null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True)
    retry_of = models.PositiveIntegerField(null=True)
    retry_delay_ms = models.PositiveIntegerField(null=True)

    class Meta:
        db_table = "tracer_trace_investigation_gateway_call"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "ordinal"], name="unique_inv_gateway_ordinal"
            )
        ]
