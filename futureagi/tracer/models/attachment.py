"""Private attachment control plane. None of these records grants read access.

All public identifiers are unique only within AttachmentScope. Readers must also
check occurrence binding, source retention, current policy and deletion generation.
Do not use the ambient workspace manager for capability-owned requests.
"""

import uuid

from django.db import models
from django.db.models.functions import Coalesce


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
    # Creating NEW recordings is a separate opt-in from capturing attachments.
    record_audio = models.BooleanField(default=False)
    # Importing provider recordings is a third, separate opt-in. It sits in the
    # 0133 scope-fence policy tuple, so flipping it requires a policy_version bump.
    allow_import = models.BooleanField(default=False)
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
    # The grant outlives the person who made it: offboarding never blocks on it.
    granted_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL
    )
    enabled = models.BooleanField(default=False)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "api_key"], name="attachment_writer_scope_unique"
            )
        ]


class CaptureSession(ScopedAttachmentRecord):
    """One SDK runtime capture session, keyed by client-supplied identifiers.

    ``client_id`` is F01 ``capture_session_id`` and ``correlation_nonce`` is the
    non-secret span carrier. Both are chosen by the SDK and unique per scope, so a
    second writer cannot adopt another writer's session. There is no per-session
    parent: every upload names its own parent in its ``Association``.
    """

    client_id = models.UUIDField()
    writer_id = models.UUIDField()
    writer_kind = models.CharField(
        max_length=16, choices=[("api_key", "API key"), ("user", "User")]
    )
    correlation_nonce = models.UUIDField()
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
                fields=["scope", "client_id"],
                name="attachment_session_client_unique",
            ),
            models.UniqueConstraint(
                fields=["scope", "correlation_nonce"],
                name="attachment_session_nonce_unique",
            ),
        ]


class Attachment(ScopedAttachmentRecord):
    client_id = models.UUIDField()
    modality = models.CharField(max_length=16, default="audio")
    mime_type = models.CharField(max_length=64, default="audio/wav")
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
    # Last binding check that left the row unbound; orders fair claims only.
    bind_checked_at = models.DateTimeField(null=True)
    source_started_at = models.DateTimeField(null=True)
    source_expires_at = models.DateTimeField(null=True)
    source_generation = models.PositiveBigIntegerField(null=True)
    expires_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True)
    capture_complete = models.BooleanField(default=False)
    # Server-side projection of the F01 Reference fields; never trusted for authority.
    purpose = models.CharField(max_length=32, default="")
    # F01 Reference placement: where the attachment sits inside its parent span
    # (message/part position, speaker role, audio channel, conversation turn,
    # tool call). Persisted at write time because link, import and recording
    # occurrences have no other source for it. Immutable (0133 identity loop).
    message_index = models.PositiveIntegerField(null=True)
    part_index = models.PositiveIntegerField(null=True)
    role = models.CharField(max_length=16, null=True)
    channel = models.PositiveSmallIntegerField(null=True)
    conversation_id = models.CharField(max_length=128, null=True)
    turn_id = models.CharField(max_length=128, null=True)
    tool_call_id = models.CharField(max_length=128, null=True)
    # Last time the orphan sweep confirmed the bound parent span still exists
    # in ClickHouse; the sweep's cursor. Operational, not identity.
    source_checked_at = models.DateTimeField(null=True)
    # First sweep that found the parent span missing; cleared when it is seen
    # again. The sweep tombstones only after a second miss at least the orphan
    # grace later, never on one read. Operational, not identity.
    source_missing_since = models.DateTimeField(null=True)
    recording = models.ForeignKey(
        "tracer.AttachmentRecording", null=True, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "client_id"], name="attachment_occurrence_scope_unique"
            )
        ]
        indexes = [
            models.Index(
                fields=["scope", "trace_id", "span_id"], name="attachment_parent_idx"
            ),
            # Expiry sweeps scan live rows per scope by their two expiry clocks.
            models.Index(
                fields=["scope", "expires_at"],
                condition=models.Q(deleted_at__isnull=True),
                name="attachment_occ_expiry_idx",
            ),
            models.Index(
                fields=["scope", "source_expires_at"],
                condition=models.Q(deleted_at__isnull=True),
                name="attachment_occ_src_expiry_idx",
            ),
            # Orphan sweep: live bound rows, least recently checked first.
            models.Index(
                models.F("source_checked_at").asc(nulls_first=True),
                condition=models.Q(deleted_at__isnull=True, bound_at__isnull=False),
                name="attachment_occ_checked_idx",
            ),
            # The binding worker's claim: per scope, least recently checked
            # first. Each scope's window is one bounded scan of this index.
            models.Index(
                models.F("scope"),
                Coalesce("bind_checked_at", "created_at"),
                models.F("id"),
                condition=models.Q(bound_at__isnull=True, deleted_at__isnull=True),
                name="attachment_unbound_claim_idx",
            ),
        ]


class AttachmentUpload(ScopedAttachmentRecord):
    """Server-allocated upload job; the public ``upload_id`` is the primary key.

    Idempotency is keyed on the scoped session plus the client's
    ``Idempotency-Key``. A recording segment index is reusable only after the
    earlier upload for it reached a terminal state (BR-4).
    """

    capture_session = models.ForeignKey(CaptureSession, on_delete=models.PROTECT)
    attachment = models.OneToOneField(Attachment, on_delete=models.PROTECT)
    occurrence = models.OneToOneField(AttachmentOccurrence, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
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
    # Digest of the persisted quarantine snapshot, used only to recognise a
    # byte-identical content replay (BR-3). Never exposed.
    quarantine_sha256 = models.CharField(max_length=64, default="")
    recording = models.ForeignKey(
        "tracer.AttachmentRecording", null=True, on_delete=models.PROTECT
    )
    segment_index = models.PositiveIntegerField(null=True)
    expires_at = models.DateTimeField()
    transfer_started_at = models.DateTimeField(null=True)
    failure_code = models.CharField(max_length=40, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "capture_session", "idempotency_key"],
                name="attachment_upload_key_unique",
            ),
            models.UniqueConstraint(
                fields=["scope", "recording", "segment_index"],
                condition=models.Q(
                    recording__isnull=False,
                    state__in=["pending_upload", "validating", "ready"],
                ),
                name="attachment_upload_live_segment_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_bytes__gt=0),
                name="attachment_upload_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(received_bytes__lte=models.F("expected_bytes")),
                name="attachment_upload_bounded",
            ),
            models.CheckConstraint(
                condition=models.Q(recording__isnull=True, segment_index__isnull=True)
                | models.Q(recording__isnull=False, segment_index__isnull=False),
                name="attachment_upload_segment_pair",
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
            # expire_reservations: unsettled rows in expiry order. A plain
            # (settled_at, expires_at) index could not give that order for
            # IS NULL, so it seq-scanned a 100k-row backlog (decision 7).
            models.Index(
                fields=["expires_at"],
                condition=models.Q(settled_at__isnull=True),
                name="attachment_reservation_due_idx",
            )
        ]


class AttachmentBudget(models.Model):
    """Stable lock rows: ``global:<shard>``, ``org:<id>``, ``project:<id>``.

    Lock one global shard, then the organization, then the project row.
    """

    key = models.CharField(max_length=80, primary_key=True)
    reserved_bytes = models.PositiveBigIntegerField(default=0)
    active_transfers = models.PositiveIntegerField(default=0)
    validation_jobs = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    initialization_attempts = models.PositiveIntegerField(default=0)
    # Completed-recording imports: in-flight recording jobs, objects and links.
    active_recordings = models.PositiveIntegerField(default=0)
    object_count = models.PositiveIntegerField(default=0)
    link_count = models.PositiveIntegerField(default=0)


class AttachmentMediaSession(models.Model):
    """Opaque cookie exchange; application-session validity is checked live.

    A media session belongs to one viewer and one application session, not to a
    project: the exchange carries no project context. Every playback grant
    carries its own scope and is re-authorized on each request.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Deleting a user deletes their media sessions (and, through them, their
    # playback grants); the audit trail keeps only the bare actor UUID.
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT)
    application_session_id = models.UUIDField()
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "application_session_id", "expires_at"],
                name="attachment_media_viewer_idx",
            )
        ]


class AttachmentPlaybackGrant(ScopedAttachmentRecord):
    media_session = models.ForeignKey(AttachmentMediaSession, on_delete=models.CASCADE)
    occurrence = models.ForeignKey(AttachmentOccurrence, on_delete=models.PROTECT)
    token_digest = models.CharField(max_length=64, unique=True)
    representation = models.CharField(max_length=16, default="original")
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    source_generation = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField()


class AttachmentRecording(ScopedAttachmentRecord):
    """A recording timeline addressed by the SDK-chosen ``recording_id``.

    Streamed recordings are created by their first segment upload; the manifest
    is written only with a segment write capability for this recording (BR-7).
    Field names match the completed-recording import lane so both share one
    ``(scope, recording_id)`` namespace.
    """

    client_id = models.UUIDField()
    capture_session = models.ForeignKey(CaptureSession, on_delete=models.PROTECT)
    trace_id = models.UUIDField()
    span_id = models.CharField(max_length=16)
    capture_session_client_id = models.UUIDField()
    correlation_nonce = models.UUIDField()
    direction = models.CharField(
        max_length=8,
        choices=[("input", "Input"), ("output", "Output"), ("mixed", "Mixed")],
    )
    state = models.CharField(
        max_length=16,
        choices=[
            ("pending_upload", "Pending upload"),
            ("importing", "Importing"),
            ("validating", "Validating"),
            ("ready", "Ready"),
            ("failed", "Failed"),
            ("rejected", "Rejected"),
            ("expired", "Expired"),
            ("deleted", "Deleted"),
        ],
        default="pending_upload",
    )
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    # Probed facts of an imported (server-segmented) recording; null otherwise.
    sample_rate = models.PositiveIntegerField(null=True)
    channels = models.PositiveSmallIntegerField(null=True)
    duration_ms = models.PositiveIntegerField(null=True)
    capture_complete = models.BooleanField(default=False)
    manifest = models.JSONField(null=True)
    manifest_revision = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "client_id"], name="attachment_recording_unique"
            )
        ]


class AttachmentManifestRevision(ScopedAttachmentRecord):
    """Accepted manifest bodies by idempotency key (BR-5); fingerprints only."""

    recording = models.ForeignKey(AttachmentRecording, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    revision = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "idempotency_key"],
                name="attachment_manifest_key_unique",
            )
        ]


class AttachmentUsageReceipt(ScopedAttachmentRecord):
    """One terminal receipt per upload, not an invoice or edition entitlement."""

    upload = models.OneToOneField(AttachmentUpload, on_delete=models.PROTECT)
    stored_bytes = models.PositiveBigIntegerField()
    outcome = models.CharField(max_length=24, choices=AttachmentState.choices)


class AttachmentAuditEvent(ScopedAttachmentRecord):
    """No arbitrary payload/labels: content and capability secrets do not fit."""

    event_id = models.UUIDField()
    actor_id = models.UUIDField(null=True)
    entity_id = models.UUIDField()
    event = models.CharField(max_length=24)
    reason = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "event_id"], name="attachment_audit_event_unique"
            )
        ]


class AttachmentOutbox(ScopedAttachmentRecord):
    """Durable work queue; every ``event`` kind has exactly one registered consumer
    (``tracer.services.attachments.lifecycle.CONSUMERS``)."""

    upload = models.ForeignKey(AttachmentUpload, null=True, on_delete=models.PROTECT)
    # Set at creation on every row that has one; immutable afterwards (0133). It
    # lets erase work be keyed on the attachment when there is no upload job.
    attachment = models.ForeignKey(Attachment, null=True, on_delete=models.PROTECT)
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
            ),
            # NULLs are distinct in the constraint above, so upload-less rows
            # (attachment-keyed erase work) need their own uniqueness.
            models.UniqueConstraint(
                fields=["scope", "attachment", "event", "generation"],
                condition=models.Q(upload__isnull=True, attachment__isnull=False),
                name="attachment_outbox_attachment_unique",
            ),
        ]
        indexes = [
            # Drains select due, unprocessed rows of one event kind.
            models.Index(
                fields=["event", "available_at"],
                condition=models.Q(processed_at__isnull=True),
                name="attachment_outbox_due_idx",
            )
        ]
