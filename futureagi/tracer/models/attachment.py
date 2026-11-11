"""Private attachment control plane. None of these records grants read access.

All public identifiers are unique only within AttachmentScope. Readers must also
check occurrence binding, source retention, current policy and deletion generation.
Do not use the ambient workspace manager for capability-owned requests.
"""

import uuid

from django.db import models


class AttachmentState(models.TextChoices):
    PENDING = "pending_upload"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETED = "deleted"


class AttachmentScope(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.PROTECT)
    project = models.OneToOneField("tracer.Project", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    policy_version = models.PositiveBigIntegerField(default=1)
    deletion_generation = models.PositiveBigIntegerField(default=0)
    require_media_redaction = models.BooleanField(default=True)
    allow_unredacted_media_with_text_pii = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class ScopedAttachmentRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(AttachmentScope, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class AttachmentWriteGrant(ScopedAttachmentRecord):
    """Explicit owner-managed eligibility; never inferred from an ingest key."""

    api_key = models.ForeignKey("accounts.OrgApiKey", on_delete=models.PROTECT)
    granted_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "api_key"], name="attachment_writer_scope_unique"
            )
        ]


class CaptureSession(ScopedAttachmentRecord):
    client_id = models.UUIDField()
    writer_id = models.UUIDField()
    writer_kind = models.CharField(
        max_length=16, choices=[("api_key", "API key"), ("user", "User")]
    )
    # Safe, non-secret correlation carrier. Ingestion must authenticate writer_id.
    correlation_nonce = models.UUIDField(default=uuid.uuid4, unique=True)
    trace_id = models.UUIDField()
    span_id = models.CharField(max_length=16)
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    origin = models.CharField(max_length=255, default="")
    request_fingerprint = models.CharField(max_length=64)
    capture_policy = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "writer_kind", "writer_id", "client_id"],
                name="attachment_session_client_unique",
            )
        ]


class Attachment(ScopedAttachmentRecord):
    client_id = models.UUIDField()
    modality = models.CharField(max_length=16, default="audio")
    source_kind = models.CharField(max_length=32)
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=24, choices=AttachmentState.choices, default=AttachmentState.PENDING
    )
    completed_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "client_id"], name="attachment_client_scope_unique"
            )
        ]


class AttachmentBlob(ScopedAttachmentRecord):
    attachment = models.OneToOneField(Attachment, on_delete=models.PROTECT)
    storage_key = models.CharField(max_length=255)
    storage_version = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    byte_length = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=64)
    validated_at = models.DateTimeField()
    # These are server-probed facts, not application supplied metadata.
    sample_rate = models.PositiveIntegerField()
    channels = models.PositiveSmallIntegerField()
    frame_count = models.PositiveBigIntegerField()


class AttachmentOccurrence(ScopedAttachmentRecord):
    client_id = models.UUIDField()
    attachment = models.ForeignKey(Attachment, on_delete=models.PROTECT)
    capture_session = models.ForeignKey(CaptureSession, on_delete=models.PROTECT)
    trace_id = models.UUIDField()
    span_id = models.CharField(max_length=16)
    direction = models.CharField(
        max_length=8,
        choices=[("input", "Input"), ("output", "Output"), ("mixed", "Mixed")],
    )
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    bound_at = models.DateTimeField(null=True)
    source_started_at = models.DateTimeField(null=True)
    source_expires_at = models.DateTimeField(null=True)
    source_generation = models.PositiveBigIntegerField(null=True)
    expires_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True)
    capture_complete = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "client_id"], name="attachment_occurrence_scope_unique"
            )
        ]
        indexes = [
            models.Index(
                fields=["scope", "trace_id", "span_id"], name="attachment_parent_idx"
            )
        ]


class AttachmentUpload(ScopedAttachmentRecord):
    client_id = models.UUIDField()
    capture_session = models.ForeignKey(CaptureSession, on_delete=models.PROTECT)
    attachment = models.OneToOneField(Attachment, on_delete=models.PROTECT)
    occurrence = models.OneToOneField(AttachmentOccurrence, on_delete=models.PROTECT)
    request_fingerprint = models.CharField(max_length=64)
    expected_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=64)
    received_bytes = models.PositiveBigIntegerField(default=0)
    state = models.CharField(
        max_length=24, choices=AttachmentState.choices, default=AttachmentState.PENDING
    )
    deletion_generation = models.PositiveBigIntegerField()
    policy_version = models.PositiveBigIntegerField()
    quarantine_key = models.CharField(max_length=255, default="")
    quarantine_version = models.CharField(max_length=255, default="")
    expires_at = models.DateTimeField()
    transfer_started_at = models.DateTimeField(null=True)
    failure_code = models.CharField(max_length=40, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "client_id"], name="attachment_upload_scope_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(expected_bytes__gt=0),
                name="attachment_upload_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(received_bytes__lte=models.F("expected_bytes")),
                name="attachment_upload_bounded",
            ),
        ]


class UploadReservation(ScopedAttachmentRecord):
    upload = models.OneToOneField(AttachmentUpload, on_delete=models.PROTECT)
    reserved_bytes = models.PositiveBigIntegerField()
    decoded_bytes = models.PositiveBigIntegerField()
    object_count = models.PositiveIntegerField(default=2)
    part_count = models.PositiveIntegerField(default=1)
    settled_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(
                fields=["settled_at", "expires_at"],
                name="attachment_reservation_expiry",
            )
        ]


class AttachmentBudget(models.Model):
    """Stable lock rows. Lock global, organization, project in that order."""

    key = models.CharField(max_length=80, primary_key=True)
    reserved_bytes = models.PositiveBigIntegerField(default=0)
    active_transfers = models.PositiveIntegerField(default=0)
    validation_jobs = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    initialization_attempts = models.PositiveIntegerField(default=0)


class AttachmentOutbox(ScopedAttachmentRecord):
    upload = models.ForeignKey(AttachmentUpload, null=True, on_delete=models.PROTECT)
    event = models.CharField(
        max_length=32,
        choices=[
            ("validate", "Validate"),
            ("erase", "Erase"),
            ("admitted", "Admitted"),
            ("settled", "Settled"),
        ],
    )
    generation = models.PositiveBigIntegerField()
    # No payload/URL/filename/free-form exception field by design.
    processed_at = models.DateTimeField(null=True)
    available_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "upload", "event", "generation"],
                name="attachment_outbox_unique",
            )
        ]
