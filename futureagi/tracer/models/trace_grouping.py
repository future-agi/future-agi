"""Durable feature-preparation work; not Feed membership or issue identity."""

import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.constants.grouping_versions import GROUPING_POLICY_VERSION


class GroupingFeatureState(models.TextChoices):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TraceGroupingFeatureJob(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        "tracer.TraceInvestigationReport",
        on_delete=models.CASCADE,
        related_name="grouping_feature_jobs",
    )
    policy_version = models.CharField(max_length=64)
    publication_result_digest = models.CharField(max_length=71)
    state = models.CharField(
        max_length=20,
        choices=GroupingFeatureState.choices,
        default=GroupingFeatureState.PENDING,
    )
    # Set to enqueue time, not the later grouping/cohort debounce deadline.
    not_before = models.DateTimeField()
    worker_id = models.CharField(max_length=255, blank=True)
    lease_token_digest = models.CharField(max_length=64, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=0)
    model_revision = models.CharField(max_length=128, null=True, blank=True)
    serving_release = models.CharField(max_length=128, blank=True)
    feature_digest = models.CharField(max_length=71, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "tracer_trace_grouping_feature_job"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "policy_version"],
                name="unique_group_feature_report_policy",
            )
        ]
        indexes = [
            models.Index(fields=["state", "not_before"], name="group_feature_due_idx")
        ]


class TraceGroupingFeature(BaseModel):
    """PostgreSQL visibility receipt for a versioned ClickHouse vector+LSH row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(TraceGroupingFeatureJob, on_delete=models.CASCADE)
    finding = models.ForeignKey(
        "tracer.TraceInvestigationFinding", on_delete=models.CASCADE
    )
    view = models.CharField(max_length=16)
    source_digest = models.CharField(max_length=71)
    evidence_revision = models.CharField(max_length=71)
    text_digest = models.CharField(max_length=71)
    feature_digest = models.CharField(max_length=71)
    model = models.CharField(max_length=100)
    model_revision = models.CharField(max_length=128, null=True, blank=True)
    serving_release = models.CharField(max_length=128)
    dimension = models.PositiveIntegerField()
    bucket_keys = models.JSONField(default=list)

    class Meta:
        db_table = "tracer_trace_grouping_feature"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "finding", "view"], name="unique_group_feature_view"
            )
        ]
        indexes = [
            models.Index(fields=["finding", "view"], name="group_feature_lookup_idx")
        ]


class GroupingWorkState(models.TextChoices):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class GroupingAttemptState(models.TextChoices):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TraceGroupingScope(BaseModel):
    """One authoritative live F6 registry fence per project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField("tracer.Project", on_delete=models.CASCADE)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey(
        "accounts.Workspace", on_delete=models.CASCADE, null=True, blank=True
    )
    policy_version = models.CharField(max_length=64, default=GROUPING_POLICY_VERSION)
    pending_revision = models.PositiveBigIntegerField(default=0)
    registry_revision = models.PositiveBigIntegerField(default=0)
    lease_fence = models.PositiveBigIntegerField(default=0)
    spent_usd = models.DecimalField(max_digits=20, decimal_places=9, default=0)
    reserved_usd = models.DecimalField(max_digits=20, decimal_places=9, default=0)

    class Meta:
        db_table = "tracer_trace_grouping_scope"


class TraceGroupingWork(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    report = models.ForeignKey(
        "tracer.TraceInvestigationReport", on_delete=models.CASCADE
    )
    feature_job = models.OneToOneField(
        TraceGroupingFeatureJob, on_delete=models.CASCADE
    )
    state = models.CharField(
        max_length=20,
        choices=GroupingWorkState.choices,
        default=GroupingWorkState.PENDING,
    )
    input_revision = models.PositiveBigIntegerField()
    not_before = models.DateTimeField()
    attempt_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "tracer_trace_grouping_work"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "scope"], name="unique_group_work_report_scope"
            )
        ]
        indexes = [
            models.Index(fields=["state", "not_before"], name="group_work_due_idx"),
            models.Index(fields=["scope", "state"], name="group_work_scope_idx"),
        ]


class TraceGroupingAttempt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work = models.ForeignKey(
        TraceGroupingWork, on_delete=models.CASCADE, related_name="attempts"
    )
    attempt_number = models.PositiveIntegerField()
    worker_id = models.CharField(max_length=255)
    lease_token_digest = models.CharField(max_length=64)
    lease_expires_at = models.DateTimeField()
    fence = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=20,
        choices=GroupingAttemptState.choices,
        default=GroupingAttemptState.CLAIMED,
    )
    snapshot_digest = models.CharField(max_length=71)
    registry_revision = models.PositiveBigIntegerField()
    claimed_work_ids = models.JSONField(default=list)
    offered_issue_ids = models.JSONField(default=list)
    omitted_candidate_ids = models.JSONField(default=list)
    omitted_candidates = models.JSONField(default=list)
    candidate_digest = models.CharField(max_length=71, blank=True)
    checkpoint = models.JSONField(default=dict)
    checkpoint_revision = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "tracer_trace_grouping_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["work", "attempt_number"], name="unique_group_attempt_number"
            )
        ]
        indexes = [
            models.Index(
                fields=["state", "lease_expires_at"], name="group_attempt_lease_idx"
            )
        ]


class TraceGroupingIssueState(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    cluster = models.OneToOneField(
        "tracer.TraceErrorGroup", on_delete=models.CASCADE, related_name="issue_state"
    )
    revision = models.PositiveBigIntegerField(default=1)
    membership_revision = models.PositiveBigIntegerField(default=1)
    mechanism = models.JSONField(default=dict)
    prototype_occurrence_ids = models.JSONField(default=list)
    protected = models.BooleanField(default=False)
    dirty = models.BooleanField(default=False)
    retired = models.BooleanField(default=False)

    class Meta:
        db_table = "tracer_trace_grouping_issue_state"
        indexes = [
            models.Index(fields=["scope", "retired"], name="group_issue_live_idx")
        ]


class TraceGroupingFindingState(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    finding = models.OneToOneField(
        "tracer.TraceInvestigationFinding", on_delete=models.CASCADE
    )
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    disposition = models.CharField(max_length=20)
    reason = models.CharField(max_length=255, blank=True)
    source_digest = models.CharField(max_length=71)

    class Meta:
        db_table = "tracer_trace_grouping_finding_state"
        indexes = [
            models.Index(
                fields=["scope", "disposition"], name="group_finding_state_idx"
            )
        ]


class TraceGroupingConstraint(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)  # cannot_link or exclude_issue
    first_finding = models.ForeignKey(
        "tracer.TraceInvestigationFinding", on_delete=models.CASCADE
    )
    second_finding = models.ForeignKey(
        "tracer.TraceInvestigationFinding",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="grouping_second_constraints",
    )
    issue = models.ForeignKey(
        "tracer.TraceErrorGroup", on_delete=models.CASCADE, null=True, blank=True
    )
    reason = models.CharField(max_length=255)

    class Meta:
        db_table = "tracer_trace_grouping_constraint"
        indexes = [
            models.Index(fields=["scope", "kind"], name="group_constraint_scope_idx")
        ]


class TraceGroupingDecision(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    attempt = models.ForeignKey(TraceGroupingAttempt, on_delete=models.CASCADE)
    idempotency_key = models.CharField(max_length=255)
    proposal_digest = models.CharField(max_length=71)
    snapshot_digest = models.CharField(max_length=71)
    commands = models.JSONField(default=list)
    receipt_ids = models.JSONField(default=list)
    registry_revision = models.PositiveBigIntegerField()
    result = models.JSONField(default=dict)

    class Meta:
        db_table = "tracer_trace_grouping_decision"
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "idempotency_key"], name="unique_group_decision_key"
            )
        ]


class TraceGroupingCall(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    work = models.ForeignKey(TraceGroupingWork, on_delete=models.CASCADE)
    attempt = models.ForeignKey(TraceGroupingAttempt, on_delete=models.CASCADE)
    request_key = models.CharField(max_length=255)
    request_digest = models.CharField(max_length=71)
    repair_intent = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, default="reserved")
    max_cost_usd = models.DecimalField(max_digits=20, decimal_places=9)
    cost_usd = models.DecimalField(max_digits=20, decimal_places=9, null=True)
    result = models.JSONField(null=True, blank=True)
    model_used = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveBigIntegerField(null=True)
    output_tokens = models.PositiveBigIntegerField(null=True)
    failure_code = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "tracer_trace_grouping_call"
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "request_key"], name="unique_group_call_request"
            )
        ]


class TraceGroupingOutbox(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(TraceGroupingScope, on_delete=models.CASCADE)
    event_kind = models.CharField(max_length=64)
    source_id = models.UUIDField()
    revision = models.PositiveBigIntegerField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "tracer_trace_grouping_outbox"
        constraints = [
            models.UniqueConstraint(
                fields=["event_kind", "source_id", "revision"],
                name="unique_group_outbox_event",
            )
        ]
        indexes = [
            models.Index(
                fields=["delivered_at", "created_at"], name="group_outbox_due_idx"
            )
        ]
