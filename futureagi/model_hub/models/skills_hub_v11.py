"""Durable Skills jobs and evidence. No ambient managers or save orchestration.

Immutable payloads are accepted by services, never by model save hooks. Lifecycle
columns remain separate so retention can remove text without rewriting identity.
"""

import uuid

from django.conf import settings
from django.db import models


class ScopedRecord(models.Model):
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="hub_skill_%(class)s_records",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="hub_skill_%(class)s_records",
    )

    class Meta:
        abstract = True


class HubSkillExecutionQuota(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="hub_skill_execution_quota",
    )
    max_active_model_jobs = models.PositiveIntegerField(default=2)
    max_active_provider_calls = models.PositiveIntegerField(default=4)
    max_active_github_jobs = models.PositiveIntegerField(default=1)
    revision = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class HubSkillJob(ScopedRecord):
    class Kind(models.TextChoices):
        TEST = "test"
        ASSIST = "assist"
        GITHUB = "github"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        CANCELLING = "cancelling"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        TIMED_OUT = "timed_out"
        INTERRUPTED = "interrupted"

    class DispatchState(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        UNKNOWN = "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        null=True,
        related_name="hub_skill_jobs",
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_skill_jobs",
    )
    actor_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_jobs",
    )
    # Preserve the authentication class if the key row is subsequently removed.
    principal_kind = models.CharField(max_length=16, default="human")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    idempotency_key = models.UUIDField()
    request_digest = models.CharField(max_length=71)
    workflow_id = models.CharField(max_length=100, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    dispatch_state = models.CharField(
        max_length=8, choices=DispatchState.choices, default=DispatchState.PENDING
    )
    dispatch_attempts = models.PositiveIntegerField(default=0)
    cancel_requested_at = models.DateTimeField(null=True)
    claim_generation = models.PositiveBigIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    deadline_at = models.DateTimeField()
    reservation_released_at = models.DateTimeField(null=True)
    failure_code = models.CharField(max_length=64, null=True)
    failure_detail = models.JSONField(default=dict)
    max_total_tokens = models.PositiveBigIntegerField(null=True)
    max_estimated_cost_usd = models.DecimalField(
        max_digits=18, decimal_places=8, null=True
    )
    reserved_tokens = models.PositiveBigIntegerField(default=0)
    reserved_estimate_usd = models.DecimalField(
        max_digits=18, decimal_places=8, default=0
    )
    known_input_tokens = models.PositiveBigIntegerField(default=0)
    known_output_tokens = models.PositiveBigIntegerField(default=0)
    known_estimate_usd = models.DecimalField(max_digits=18, decimal_places=8, default=0)
    unknown_call_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "workspace",
                    "owner_user",
                    "kind",
                    "idempotency_key",
                ],
                name="uniq_hub_skill_job_request",
            ),
            models.CheckConstraint(
                condition=models.Q(skill__isnull=False) | models.Q(kind="github"),
                name="hub_skill_job_skill_required",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "status", "created_at"],
                name="hub_job_org_status_idx",
            ),
            models.Index(
                fields=["dispatch_state", "status", "created_at"],
                name="hub_job_dispatch_idx",
            ),
        ]


class HubSkillTestSuite(ScopedRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.OneToOneField(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_test_suite",
    )
    revision = models.PositiveBigIntegerField(default=1)
    cases = models.JSONField(default=list)
    digest = models.CharField(max_length=71)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_test_suites",
    )
    updated_by_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_test_suites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class HubSkillTestRun(models.Model):
    @property
    def id(self):
        return self.job_id

    class Visibility(models.TextChoices):
        PRIVATE = "private"
        WORKSPACE = "workspace"

    job = models.OneToOneField(
        HubSkillJob,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="hub_skill_test_run",
    )
    visibility = models.CharField(
        max_length=12, choices=Visibility.choices, default=Visibility.PRIVATE
    )
    shared_at = models.DateTimeField(null=True)
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_shared_runs",
    )
    profile = models.CharField(
        max_length=8, choices=[("quick", "quick"), ("repeated", "repeated")]
    )
    parent_run = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_child_runs",
    )
    suite = models.ForeignKey(
        HubSkillTestSuite,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_runs",
    )
    suite_revision = models.PositiveBigIntegerField(null=True)
    selected_case_ids = models.JSONField(default=list)
    full_suite = models.BooleanField(default=False)
    repetitions = models.PositiveSmallIntegerField(default=1)
    harness_version = models.CharField(max_length=64)
    adapter_version = models.CharField(max_length=64)
    evaluator_version = models.CharField(max_length=64)
    # Nonsecret and retained after snapshot purge (audit correction 10).
    execution_configuration = models.JSONField(default=dict)
    plan_fingerprint = models.CharField(max_length=71)
    execution_fingerprint = models.CharField(max_length=71)
    candidate_digest = models.CharField(max_length=71)
    candidate = models.JSONField(default=dict)
    production = models.JSONField(null=True)
    competitors = models.JSONField(default=list)
    baseline_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_baseline_runs",
    )
    baseline_event = models.ForeignKey(
        "model_hub.HubSkillLabelEvent",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_baseline_runs",
    )
    snapshot = models.JSONField(null=True)
    validity = models.CharField(
        max_length=12, choices=[("reviewed", "reviewed"), ("diagnostic", "diagnostic")]
    )
    validity_reasons = models.JSONField(default=list)
    counts = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    payload_expires_at = models.DateTimeField(null=True)
    payload_purged_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=["visibility", "job"], name="hub_run_visibility_idx")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(repetitions__in=[1, 3]),
                name="hub_run_repetitions_valid",
            )
        ]


class HubSkillTestAttempt(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started"
        RUNNING = "running"
        COMPLETED = "completed"
        ERROR = "error"
        CANCELLED = "cancelled"
        INCOMPLETE = "incomplete"
        UNKNOWN = "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        HubSkillTestRun, on_delete=models.CASCADE, related_name="hub_skill_attempts"
    )
    case_id = models.UUIDField()
    variant = models.CharField(
        max_length=16,
        choices=[(v, v) for v in ("candidate", "production", "without_target")],
    )
    repeat_index = models.PositiveSmallIntegerField()
    schedule_index = models.PositiveIntegerField()
    claim_generation = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NOT_STARTED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    answer = models.TextField(null=True)
    finish_reason = models.CharField(max_length=64, null=True)
    target_requested = models.BooleanField(default=False)
    target_loaded = models.BooleanField(null=True)
    root_fully_delivered = models.BooleanField(null=True)
    routing_verdict = models.CharField(max_length=16, default="not_graded")
    delivery_verdict = models.CharField(max_length=16, default="not_graded")
    answer_verdict = models.CharField(max_length=16, default="not_graded")
    check_results = models.JSONField(default=list)
    receipts = models.JSONField(default=list)
    task_metrics = models.JSONField(null=True)
    judge_metrics = models.JSONField(null=True)
    error = models.JSONField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["run", "case_id", "variant", "repeat_index"],
                name="uniq_hub_skill_attempt",
            )
        ]
        indexes = [
            models.Index(
                fields=["run", "schedule_index"], name="hub_attempt_schedule_idx"
            )
        ]


class HubSkillModelCall(models.Model):
    class State(models.TextChoices):
        PREPARED = "prepared"
        IN_FLIGHT = "in_flight"
        COMPLETED = "completed"
        FAILED = "failed"
        UNKNOWN = "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HubSkillJob, on_delete=models.CASCADE, related_name="hub_skill_calls"
    )
    attempt = models.ForeignKey(
        HubSkillTestAttempt,
        on_delete=models.CASCADE,
        null=True,
        related_name="hub_skill_calls",
    )
    kind = models.CharField(
        max_length=8, choices=[(v, v) for v in ("task", "judge", "assist")]
    )
    sequence = models.PositiveIntegerField()
    claim_generation = models.PositiveBigIntegerField(default=0)
    state = models.CharField(
        max_length=12, choices=State.choices, default=State.PREPARED
    )
    request_digest = models.CharField(max_length=71)
    provider_request_id = models.CharField(max_length=255, null=True)
    connection_reference = models.CharField(max_length=255, blank=True)
    connection_revision = models.CharField(max_length=128, blank=True)
    requested_model = models.CharField(max_length=255, blank=True)
    returned_model = models.CharField(max_length=255, null=True)
    reserved_tokens = models.PositiveBigIntegerField(default=0)
    reserved_estimate_usd = models.DecimalField(
        max_digits=18, decimal_places=8, default=0
    )
    usage = models.JSONField(null=True)
    estimated_cost_usd = models.DecimalField(max_digits=18, decimal_places=8, null=True)
    pricing_source = models.CharField(max_length=255, blank=True)
    pricing_version = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    latency_ms = models.PositiveIntegerField(null=True)
    assistant_message = models.JSONField(null=True)
    tool_receipts = models.JSONField(default=list)
    finish_reason = models.CharField(max_length=64, null=True)
    error = models.JSONField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "sequence"], name="uniq_hub_skill_call_sequence"
            )
        ]
        indexes = [models.Index(fields=["job", "state"], name="hub_call_job_state_idx")]


class HubSkillTestEvidence(ScopedRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_test_evidence",
    )
    run = models.ForeignKey(
        HubSkillTestRun, on_delete=models.PROTECT, related_name="hub_skill_evidence"
    )
    version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_test_evidence",
    )
    label_event = models.ForeignKey(
        "model_hub.HubSkillLabelEvent",
        on_delete=models.PROTECT,
        null=True,
        related_name="hub_skill_test_evidence",
    )
    candidate_digest = models.CharField(max_length=71)
    execution_fingerprint = models.CharField(max_length=71)
    attached_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_evidence_attached",
    )
    attached_by_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_evidence_attached",
    )
    currentness_at_action = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["version", "run"],
                condition=models.Q(label_event__isnull=True),
                name="uniq_hub_evidence_version_run",
            ),
            models.UniqueConstraint(
                fields=["label_event", "run"],
                condition=models.Q(label_event__isnull=False),
                name="uniq_hub_evidence_event_run",
            ),
        ]


class HubSkillAssistProposal(models.Model):
    @property
    def id(self):
        return self.job_id

    job = models.OneToOneField(
        HubSkillJob,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="hub_skill_assist_proposal",
    )
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_assist_proposals",
    )
    kind = models.CharField(
        max_length=16, choices=[(v, v) for v in ("draft", "description", "summary")]
    )
    base_revision = models.PositiveBigIntegerField(null=True)
    base_digest = models.CharField(max_length=71)
    input = models.JSONField(null=True)
    input_fingerprint = models.CharField(max_length=71)
    prompt_version = models.CharField(max_length=64)
    model_config = models.JSONField(default=dict)
    source_run = models.ForeignKey(
        HubSkillTestRun,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_assist_proposals",
    )
    selected_case_ids = models.JSONField(default=list)
    exposed_source_group_ids = models.JSONField(default=list)
    proposal = models.JSONField(null=True)
    missing_information = models.JSONField(default=list)
    checks = models.JSONField(null=True)
    application_receipt = models.JSONField(null=True)
    payload_expires_at = models.DateTimeField(null=True)
    payload_purged_at = models.DateTimeField(null=True)


class HubSkillGitHubPreview(models.Model):
    @property
    def id(self):
        return self.job_id

    job = models.OneToOneField(
        HubSkillJob,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="hub_skill_github_preview",
    )
    parent_preview = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_continuations",
    )
    operation = models.CharField(
        max_length=8, choices=[("import", "import"), ("update", "update")]
    )
    repo = models.CharField(max_length=255, blank=True)
    repository_id = models.PositiveBigIntegerField(null=True)
    path = models.CharField(max_length=1024, blank=True)
    ref = models.CharField(max_length=255, blank=True)
    ref_kind = models.CharField(max_length=8, blank=True)
    commit_sha = models.CharField(max_length=40, null=True)
    source_revision = models.PositiveBigIntegerField(null=True)
    expected_latest_version = models.PositiveIntegerField(null=True)
    expected_latest_digest = models.CharField(max_length=71, null=True)
    roots = models.JSONField(default=list)
    bundle = models.JSONField(null=True)
    license = models.JSONField(default=dict)
    diff = models.JSONField(null=True)
    local_diverged = models.BooleanField(default=False)
    outcome = models.CharField(max_length=24, default="pending")
    expires_at = models.DateTimeField(null=True)
    consumed_skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_consumed_previews",
    )
    consumed_draft_revision = models.PositiveBigIntegerField(null=True)
    rights_review = models.JSONField(null=True)


class HubSkillGitHubSource(ScopedRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.OneToOneField(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_github_source",
    )
    revision = models.PositiveBigIntegerField(default=1)
    repo = models.CharField(max_length=255)
    repository_id = models.PositiveBigIntegerField()
    path = models.CharField(max_length=1024, blank=True)
    ref = models.CharField(max_length=255)
    ref_kind = models.CharField(
        max_length=8, choices=[(v, v) for v in ("branch", "tag", "commit", "default")]
    )
    imported_sha = models.CharField(max_length=40)
    upstream_digest = models.CharField(max_length=71)
    associated_version = models.ForeignKey(
        "model_hub.HubSkillVersion",
        on_delete=models.PROTECT,
        related_name="hub_skill_github_sources",
    )
    checked_sha = models.CharField(max_length=40, null=True)
    checked_at = models.DateTimeField(null=True)
    check_status = models.CharField(max_length=32, null=True)
    etag = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class HubSkillLabelPolicyEvent(ScopedRecord):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    skill = models.ForeignKey(
        "model_hub.HubSkill",
        on_delete=models.CASCADE,
        related_name="hub_skill_label_policy_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_label_policy_events",
    )
    actor_api_key = models.ForeignKey(
        "accounts.OrgApiKey",
        on_delete=models.SET_NULL,
        null=True,
        related_name="hub_skill_label_policy_events",
    )
    old_names = models.JSONField(default=list)
    new_names = models.JSONField(default=list)
    from_revision = models.PositiveBigIntegerField()
    to_revision = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["skill", "-created_at"], name="hub_policy_event_time_idx"
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "to_revision"], name="uniq_hub_policy_revision"
            )
        ]
