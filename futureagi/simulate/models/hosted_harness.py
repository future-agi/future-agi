from __future__ import annotations

import uuid

from django.db import models

from accounts.models import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class HostedHarnessJob(BaseModel):
    class State(models.TextChoices):
        RECEIVED = "received", "Received"
        QUEUED = "queued", "Queued"
        ADMITTED = "admitted", "Admitted"
        PROVISIONING = "provisioning", "Provisioning"
        RUNNING = "running", "Running"
        FINALIZING = "finalizing", "Finalizing"
        CLEANING_UP = "cleaning_up", "Cleaning up"
        RETRY_WAIT = "retry_wait", "Retry wait"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="hosted_harness_jobs"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="hosted_harness_jobs",
        null=True,
        blank=True,
    )
    run_id = models.UUIDField(unique=True)
    # The name a person gave this environment, which outranks every value
    # derived from the submitted request. Empty means nobody has renamed it, so
    # the derived name still applies.
    name = models.CharField(max_length=255, blank=True, default="")
    idempotency_key = models.CharField(max_length=255)
    request_digest = models.CharField(max_length=71)
    schema_version = models.CharField(max_length=64)
    payload = models.JSONField()
    state = models.CharField(
        max_length=32, choices=State.choices, default=State.RECEIVED
    )
    current_stage = models.CharField(max_length=64, default="queued")
    current_attempt_number = models.PositiveIntegerField(default=0)
    seed = models.BigIntegerField()
    scenario_count = models.PositiveSmallIntegerField()
    completed_count = models.PositiveSmallIntegerField(default=0)
    failed_count = models.PositiveSmallIntegerField(default=0)
    artifact_level = models.CharField(max_length=32)
    max_artifact_bytes = models.BigIntegerField()
    uploaded_artifact_bytes = models.BigIntegerField(default=0)
    deadline_at = models.DateTimeField()
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=32, null=True, blank=True)
    terminal_at = models.DateTimeField(null=True, blank=True)
    content_updated_at = models.DateTimeField(null=True, blank=True)
    failure = models.JSONField(null=True, blank=True)
    # Secret-safe presentation snapshots produced as each ALK authoring stage
    # completes.  Keep these separate from the submitted payload: the payload is
    # forwarded to the hosted guest, while these values exist only for the live UI.
    stage_outputs = models.JSONField(default=list, blank=True)
    bundle_digest = models.CharField(max_length=71, null=True, blank=True)
    run_test = models.ForeignKey(
        "simulate.RunTest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_harness_jobs",
    )
    test_execution = models.OneToOneField(
        "simulate.TestExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_harness_job",
    )

    class Meta:
        db_table = "simulate_hosted_harness_job"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                name="uniq_harness_job_org_idempotency",
            ),
            models.CheckConstraint(
                condition=models.Q(scenario_count__gte=1, scenario_count__lte=200),
                name="harness_job_scenario_count_1_200",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "state"], name="idx_hjob_org_state"),
            models.Index(
                fields=["organization", "workspace", "state"],
                name="idx_hjob_org_ws_state",
            ),
            models.Index(
                fields=["state", "deadline_at"], name="idx_hjob_state_deadline"
            ),
        ]


class HostedHarnessAttempt(BaseModel):
    class State(models.TextChoices):
        REGISTERED = "registered", "Registered"
        PROVISIONING = "provisioning", "Provisioning"
        RUNNING = "running", "Running"
        FINALIZING = "finalizing", "Finalizing"
        CLEANING_UP = "cleaning_up", "Cleaning up"
        SUPERSEDED = "superseded", "Superseded"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HostedHarnessJob, on_delete=models.CASCADE, related_name="attempts"
    )
    attempt_number = models.PositiveIntegerField()
    token_hash = models.CharField(max_length=64)
    fence_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    state = models.CharField(
        max_length=32, choices=State.choices, default=State.REGISTERED
    )
    event_watermark = models.PositiveBigIntegerField(default=0)
    gap_started_at = models.DateTimeField(null=True, blank=True)
    released_event_gaps = models.JSONField(default=list)
    # Attempt-level parallelism degrade projection (C4 §6, decision D26). Written
    # only when an accepted ``parallelism_degraded`` event is stored for the first
    # time (min-monotone effective, append-if-absent reason). ``None`` effective
    # means "no degrade yet" and the serializer falls back to the requested value.
    # A new attempt row starts cleared, so attempt N never inherits attempt N-1's
    # degrade state.
    effective_parallelism = models.PositiveSmallIntegerField(null=True, blank=True)
    degrade_reasons = models.JSONField(default=list)
    terminal_stage = models.CharField(max_length=16, null=True, blank=True)
    terminal_reason = models.CharField(max_length=32, null=True, blank=True)
    terminal_failure = models.JSONField(null=True, blank=True)
    terminal_event_received = models.BooleanField(default=False)
    manifest_acked = models.BooleanField(default=False)
    provider_ref = models.CharField(max_length=255, null=True, blank=True)
    snapshot_name = models.CharField(max_length=255, null=True, blank=True)
    snapshot_digest = models.CharField(max_length=71, null=True, blank=True)
    source_digest = models.CharField(max_length=71, null=True, blank=True)
    bundle_digest = models.CharField(max_length=71, null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    diagnostics_object_key = models.CharField(max_length=1024, null=True, blank=True)
    diagnostics_sha256 = models.CharField(max_length=64, null=True, blank=True)
    diagnostics_size = models.BigIntegerField(null=True, blank=True)
    diagnostics_captured_at = models.DateTimeField(null=True, blank=True)
    diagnostics_final = models.BooleanField(null=True, blank=True)
    diagnostics_error = models.CharField(max_length=500, null=True, blank=True)
    cleanup_verified_at = models.DateTimeField(null=True, blank=True)
    usage_report = models.JSONField(null=True, blank=True)
    authoring_usage_report = models.JSONField(null=True, blank=True)
    sandbox_runtime = models.JSONField(default=dict, blank=True)
    receipt_history = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "simulate_hosted_harness_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "attempt_number"], name="uniq_harness_attempt_number"
            )
        ]
        indexes = [
            models.Index(fields=["job", "state"], name="idx_hattempt_job_state"),
            models.Index(fields=["state", "expires_at"], name="idx_hattempt_expiry"),
        ]


class HostedHarnessScenario(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HostedHarnessJob,
        on_delete=models.CASCADE,
        related_name="scenario_registrations",
    )
    scenario_key = models.CharField(max_length=255)
    scenario = models.ForeignKey(
        "simulate.Scenarios",
        on_delete=models.CASCADE,
        related_name="hosted_registrations",
    )
    dataset_row = models.ForeignKey(
        "model_hub.Row",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_registrations",
        help_text=(
            "The exact row represented by this hosted scenario key. Multiple "
            "registrations can share one dataset-backed scenario."
        ),
    )
    call_execution = models.OneToOneField(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_registration",
    )

    class Meta:
        db_table = "simulate_hosted_harness_scenario"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "scenario_key"], name="uniq_harness_scenario_key"
            )
        ]


class HostedHarnessEvent(BaseModel):
    event_id = models.CharField(primary_key=True, max_length=64)
    attempt = models.ForeignKey(
        HostedHarnessAttempt, on_delete=models.CASCADE, related_name="events"
    )
    sequence = models.PositiveBigIntegerField()
    stage = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(null=True, blank=True)
    digest = models.CharField(max_length=71)
    emitted_at = models.DateTimeField()
    accepted = models.BooleanField(default=True)
    rejection_code = models.CharField(max_length=64, null=True, blank=True)
    rejection_message = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "simulate_hosted_harness_event"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "sequence"], name="uniq_harness_event_sequence"
            )
        ]
        indexes = [
            models.Index(fields=["attempt", "sequence"], name="idx_hevent_attempt_seq")
        ]


class HostedHarnessArtifact(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HostedHarnessJob, on_delete=models.CASCADE, related_name="artifacts"
    )
    sha256 = models.CharField(max_length=64)
    kind = models.CharField(max_length=32)
    size = models.BigIntegerField()
    content_type = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024)
    scenario_key = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "simulate_hosted_harness_artifact"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "sha256"], name="uniq_harness_job_artifact"
            )
        ]


class HostedHarnessReceipt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HostedHarnessJob, on_delete=models.CASCADE, related_name="result_receipts"
    )
    attempt = models.ForeignKey(
        HostedHarnessAttempt, on_delete=models.CASCADE, related_name="result_receipts"
    )
    scenario = models.ForeignKey(
        HostedHarnessScenario, on_delete=models.CASCADE, related_name="receipts"
    )
    attempt_number = models.PositiveIntegerField()
    digest = models.CharField(max_length=71)
    status = models.CharField(max_length=16)
    body = models.JSONField()

    class Meta:
        db_table = "simulate_hosted_harness_receipt"
        constraints = [
            models.UniqueConstraint(
                fields=["job", "scenario"], name="uniq_harness_job_scenario_receipt"
            )
        ]


class HostedHarnessManifest(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        HostedHarnessAttempt, on_delete=models.CASCADE, related_name="manifests"
    )
    digest = models.CharField(max_length=71)
    complete = models.BooleanField()
    body = models.JSONField()

    class Meta:
        db_table = "simulate_hosted_harness_manifest"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "digest"], name="uniq_harness_attempt_manifest"
            )
        ]


class HostedHarnessCleanupReceipt(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.OneToOneField(
        HostedHarnessAttempt, on_delete=models.CASCADE, related_name="cleanup_receipt"
    )
    provider_ref = models.CharField(max_length=255)
    verified_absent = models.BooleanField()
    details = models.JSONField(default=dict)

    class Meta:
        db_table = "simulate_hosted_harness_cleanup_receipt"


class HostedHarnessSecret(BaseModel):
    """Tenant-scoped encrypted value addressed by a platform-vault SecretRef."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="hosted_harness_secrets"
    )
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=255, default="1")
    encrypted_value = models.TextField()

    class Meta:
        db_table = "simulate_hosted_harness_secret"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                name="uniq_harness_secret_version",
            )
        ]

    def save(self, *args, **kwargs):
        from agentcc.services.credential_manager import encrypt_token

        if self.encrypted_value and not self.encrypted_value.startswith("enc::"):
            self.encrypted_value = encrypt_token(self.encrypted_value)
        super().save(*args, **kwargs)

    def get_value(self) -> str:
        from agentcc.services.credential_manager import decrypt_token

        return decrypt_token(self.encrypted_value)


class HostedHarnessStageOutput(BaseModel):
    """Persisted authoritative snapshot from a verified bundle.

    Created at job admission/launch from the pre-authored bundle so the read
    DTO always has contract/environment/scenarios data without parsing mutable
    files at read time.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        HostedHarnessJob,
        on_delete=models.CASCADE,
        related_name="normalized_stage_outputs",
    )
    title = models.CharField(max_length=255)
    summary = models.CharField(max_length=1024, default="")
    kind = models.CharField(max_length=64)
    data = models.JSONField()

    class Meta:
        db_table = "simulate_hosted_harness_stage_output"
        indexes = [
            models.Index(fields=["job", "kind"], name="idx_hstageout_job_kind"),
        ]


class HostedHarnessConversation(BaseModel):
    class State(models.TextChoices):
        COLD = "cold", "Cold"
        STARTING = "starting", "Starting"
        HYDRATING = "hydrating", "Hydrating"
        WARM_IDLE = "warm_idle", "Warm idle"
        RESPONDING = "responding", "Responding"
        WAITING_FOR_USER = "waiting_for_user", "Waiting for user"
        DEGRADED = "degraded", "Degraded"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(
        HostedHarnessJob,
        on_delete=models.CASCADE,
        related_name="conversation",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="hosted_harness_conversations",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="hosted_harness_conversations",
        null=True,
        blank=True,
    )
    state = models.CharField(max_length=32, choices=State.choices, default=State.COLD)
    current_stage = models.CharField(max_length=32, default="reception")
    next_message_sequence = models.PositiveBigIntegerField(default=1)
    next_event_sequence = models.PositiveBigIntegerField(default=1)
    command_acked_through = models.PositiveBigIntegerField(default=0)
    event_acked_through = models.PositiveBigIntegerField(default=0)
    active_invocation_id = models.CharField(max_length=255, null=True, blank=True)
    blocking_input = models.JSONField(null=True, blank=True)
    policy_hash = models.CharField(max_length=64, default="")
    latest_workspace_digest = models.CharField(max_length=71, null=True, blank=True)
    latest_scenario_count = models.PositiveIntegerField(null=True, blank=True)
    latest_workspace_object_key = models.CharField(
        max_length=1024, null=True, blank=True
    )
    last_activity_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "simulate_hosted_harness_conversation"
        indexes = [
            models.Index(fields=["organization", "state"], name="idx_hconv_org_state"),
            models.Index(
                fields=["state", "last_activity_at"], name="idx_hconv_state_seen"
            ),
        ]


class HostedHarnessConversationMessage(BaseModel):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    class Kind(models.TextChoices):
        MESSAGE = "message", "Message"
        QUESTION = "question", "Question"
        CONFIRMATION = "confirmation", "Confirmation"
        STATUS = "status", "Status"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        DELIVERED = "delivered", "Delivered"
        STREAMING = "streaming", "Streaming"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        HostedHarnessConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    client_request_id = models.CharField(max_length=128, null=True, blank=True)
    sequence = models.PositiveBigIntegerField()
    role = models.CharField(max_length=16, choices=Role.choices)
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.MESSAGE)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    stage = models.CharField(max_length=32, default="")
    content = models.TextField(default="")
    payload = models.JSONField(default=dict)
    invocation_id = models.CharField(max_length=255, null=True, blank=True)
    function_call_id = models.CharField(max_length=255, null=True, blank=True)
    reply_to = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "simulate_hosted_harness_conversation_message"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                name="uniq_hconv_message_sequence",
            ),
            models.UniqueConstraint(
                fields=["conversation", "client_request_id"],
                condition=models.Q(client_request_id__isnull=False),
                name="uniq_hconv_client_request",
            ),
        ]
        indexes = [
            models.Index(
                fields=["conversation", "sequence"], name="idx_hconv_msg_sequence"
            ),
            models.Index(fields=["conversation", "state"], name="idx_hconv_msg_state"),
        ]


class HostedHarnessConversationEvent(BaseModel):
    event_id = models.CharField(primary_key=True, max_length=128)
    conversation = models.ForeignKey(
        HostedHarnessConversation,
        on_delete=models.CASCADE,
        related_name="events",
    )
    sequence = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=64)
    message_id = models.UUIDField(null=True, blank=True)
    stage = models.CharField(max_length=32, default="")
    invocation_id = models.CharField(max_length=255, null=True, blank=True)
    function_call_id = models.CharField(max_length=255, null=True, blank=True)
    payload = models.JSONField(default=dict)
    digest = models.CharField(max_length=71)
    emitted_at = models.DateTimeField()

    class Meta:
        db_table = "simulate_hosted_harness_conversation_event"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                name="uniq_hconv_event_sequence",
            )
        ]
        indexes = [
            models.Index(
                fields=["conversation", "sequence"], name="idx_hconv_evt_sequence"
            )
        ]


class HostedHarnessConversationTranscript(BaseModel):
    conversation = models.ForeignKey(
        HostedHarnessConversation,
        on_delete=models.CASCADE,
        related_name="provider_transcripts",
    )
    project_key = models.CharField(max_length=255)
    provider_session_id = models.CharField(max_length=255)
    subpath = models.CharField(max_length=512, default="")
    entries = models.JSONField(default=list)

    class Meta:
        db_table = "simulate_hosted_harness_conversation_transcript"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "conversation",
                    "project_key",
                    "provider_session_id",
                    "subpath",
                ],
                name="uniq_hconv_transcript_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["conversation", "provider_session_id"],
                name="idx_hconv_transcript_session",
            )
        ]


class HostedHarnessConversationLease(BaseModel):
    class State(models.TextChoices):
        STARTING = "starting", "Starting"
        ACTIVE = "active", "Active"
        DRAINING = "draining", "Draining"
        EXPIRED = "expired", "Expired"
        RELEASED = "released", "Released"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        HostedHarnessConversation,
        on_delete=models.CASCADE,
        related_name="lease",
    )
    attempt = models.ForeignKey(
        HostedHarnessAttempt,
        on_delete=models.SET_NULL,
        related_name="conversation_leases",
        null=True,
        blank=True,
    )
    control_only = models.BooleanField(default=False)
    provider_ref = models.CharField(max_length=255)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.STARTING
    )
    token_hash = models.CharField(max_length=64)
    fence_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    command_watermark = models.PositiveBigIntegerField(default=0)
    event_watermark = models.PositiveBigIntegerField(default=0)
    snapshot_name = models.CharField(max_length=255, default="")
    snapshot_digest = models.CharField(max_length=71, default="")

    class Meta:
        db_table = "simulate_hosted_harness_conversation_lease"
        indexes = [
            models.Index(fields=["state", "expires_at"], name="idx_hconv_lease_exp")
        ]
