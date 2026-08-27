from __future__ import annotations

import uuid

from django.db import models

from accounts.models import Organization
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
    run_id = models.UUIDField(unique=True)
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
    failure = models.JSONField(null=True, blank=True)
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
                condition=models.Q(scenario_count__gte=1, scenario_count__lte=10),
                name="harness_job_scenario_count_1_10",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "state"], name="idx_hjob_org_state"),
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
    cleanup_verified_at = models.DateTimeField(null=True, blank=True)

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
        HostedHarnessJob, on_delete=models.CASCADE, related_name="stage_outputs"
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
