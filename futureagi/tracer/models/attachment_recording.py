"""Recording, completed-import and provider-recording control records.

None of these rows grants read access. A recording becomes playable only via
its published segment occurrences, which the occurrence/gateway checks govern.
Durable rows hold opaque references only: no URL, signed query, filename,
provider credential or source metadata is stored in plain text anywhere here.
"""

from django.db import models
from django.db.models.functions import Coalesce

# The recording timeline row is shared with streamed recordings and lives in
# tracer.models.attachment; imports set its state to ``importing`` explicitly.
from tracer.models.attachment import (  # noqa: F401
    AttachmentRecording,
    ScopedAttachmentRecord,
)

DIRECTIONS = [("input", "Input"), ("output", "Output"), ("mixed", "Mixed")]


class RecordingState(models.TextChoices):
    IMPORTING = "importing"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETED = "deleted"


TERMINAL_RECORDING_STATES = {
    RecordingState.READY,
    RecordingState.FAILED,
    RecordingState.REJECTED,
    RecordingState.EXPIRED,
    RecordingState.DELETED,
}


class RecordingSegmentState(models.TextChoices):
    STAGED = "staged"
    PUBLISHED = "published"
    ERASED = "erased"


class AttachmentRecordingSegment(ScopedAttachmentRecord):
    """Server-produced, sandbox-validated, privately staged playable part."""

    recording = models.ForeignKey(AttachmentRecording, on_delete=models.PROTECT)
    index = models.PositiveIntegerField()
    # Server-allocated public identities, published only with the manifest.
    attachment_client_id = models.UUIDField()
    occurrence_client_id = models.UUIDField()
    start_frame = models.PositiveBigIntegerField()
    frame_count = models.PositiveBigIntegerField()
    start_ms = models.PositiveIntegerField()
    duration_ms = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=255)
    storage_version = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    byte_length = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=16,
        choices=RecordingSegmentState.choices,
        default=RecordingSegmentState.STAGED,
    )
    # Set when a derivative attempt fails; the derivative queue goes oldest
    # stamp first, so segments that keep failing cannot starve newer ones.
    derivative_checked_at = models.DateTimeField(null=True)
    # Set once the segment can never need derivative work again (its peaks are
    # published, or its occurrence is deleted or expired): it leaves the
    # derivative queue's partial index for good (``derivatives.purge``).
    derivatives_done_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recording", "index"], name="attachment_segment_index_unique"
            ),
            models.UniqueConstraint(
                fields=["scope", "occurrence_client_id"],
                name="attachment_segment_occurrence_unique",
            ),
        ]
        indexes = [
            # ``derivatives.pending``: open published segments only, in queue
            # order, so the queue never walks segments that are done.
            models.Index(
                Coalesce("derivative_checked_at", "created_at"),
                models.F("id"),
                condition=models.Q(state="published", derivatives_done_at__isnull=True),
                name="attachment_derivative_due_idx",
            )
        ]


class AttachmentRecordingDerivative(ScopedAttachmentRecord):
    """Immutable channel/peak derivative; inherits its segment's authority."""

    segment = models.ForeignKey(AttachmentRecordingSegment, on_delete=models.PROTECT)
    representation = models.CharField(max_length=16)
    storage_key = models.CharField(max_length=255)
    storage_version = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    byte_length = models.PositiveBigIntegerField()
    facts = models.JSONField(default=dict)
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    published_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["segment", "representation"],
                name="attachment_derivative_unique",
            )
        ]


class AttachmentSourceEnvelope(ScopedAttachmentRecord):
    """AES-GCM sealed retrieval URL, at most 10 minutes, bound to its scope."""

    creator_kind = models.CharField(max_length=16)
    creator_id = models.UUIDField()
    direction = models.CharField(max_length=8, choices=DIRECTIONS)
    # Exact encoded length asserted by the trusted resolver that sealed it.
    source_bytes = models.PositiveBigIntegerField()
    key_id = models.CharField(max_length=16)
    nonce = models.BinaryField(max_length=12)
    ciphertext = models.BinaryField(max_length=4200)
    expires_at = models.DateTimeField()


class AttachmentProviderConnection(ScopedAttachmentRecord):
    """Explicitly configured provider connection. The secret is sealed."""

    provider = models.CharField(max_length=16, choices=[("livekit", "LiveKit")])
    # The provider's public API key identifier, used as the webhook issuer.
    api_key_id = models.CharField(max_length=64)
    key_id = models.CharField(max_length=16)
    secret_nonce = models.BinaryField(max_length=12)
    secret_ciphertext = models.BinaryField(max_length=512)
    # Audit only. Deleting the user keeps the project's connection (SET_NULL);
    # nothing authorizes through this column.
    created_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL
    )
    enabled = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "api_key_id"],
                name="attachment_provider_issuer_unique",
            )
        ]


class ProviderRecordingState(models.TextChoices):
    REGISTERED = "registered"
    ACTIVE = "active"
    ENDING = "ending"
    COMPLETE = "complete"
    FAILED = "failed"
    ABORTED = "aborted"
    LIMIT_REACHED = "limit_reached"


TERMINAL_PROVIDER_STATES = {
    ProviderRecordingState.COMPLETE,
    ProviderRecordingState.FAILED,
    ProviderRecordingState.ABORTED,
    ProviderRecordingState.LIMIT_REACHED,
}


class AttachmentProviderRecording(ScopedAttachmentRecord):
    """An authorized, fixed-ID registration of an EXISTING provider recording.

    Registration never starts Egress. Only this row's exact provider job, room
    and connection can complete it.
    """

    connection = models.ForeignKey(
        AttachmentProviderConnection, on_delete=models.PROTECT
    )
    registered_output_id = models.UUIDField()
    egress_id = models.CharField(max_length=64)
    room_digest = models.CharField(max_length=64)
    registrar_kind = models.CharField(max_length=16)
    registrar_id = models.UUIDField()
    recording_client_id = models.UUIDField()
    trace_id = models.UUIDField()
    span_id = models.CharField(max_length=16)
    capture_session_client_id = models.UUIDField()
    correlation_nonce = models.UUIDField()
    direction = models.CharField(max_length=8, choices=DIRECTIONS)
    provider_state = models.CharField(
        max_length=16,
        choices=ProviderRecordingState.choices,
        default=ProviderRecordingState.REGISTERED,
    )
    provider_updated_at = models.BigIntegerField(default=0)
    output_bytes = models.PositiveBigIntegerField(null=True)
    output_duration_ms = models.PositiveIntegerField(null=True)
    import_job_id = models.UUIDField(null=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "registered_output_id"],
                name="attachment_provider_output_unique",
            ),
            models.UniqueConstraint(
                fields=["connection", "egress_id"],
                name="attachment_provider_egress_unique",
            ),
        ]


class AttachmentProviderEvent(ScopedAttachmentRecord):
    """Webhook replay fence: one row per verified provider event id."""

    connection = models.ForeignKey(
        AttachmentProviderConnection, on_delete=models.PROTECT
    )
    event_id = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "event_id"], name="attachment_provider_event"
            )
        ]


class AttachmentImportJob(ScopedAttachmentRecord):
    """Durable job. Only opaque references; reasons are F01 Reason codes."""

    recording = models.OneToOneField(AttachmentRecording, on_delete=models.PROTECT)
    importer_kind = models.CharField(max_length=16)
    importer_id = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    source_kind = models.CharField(
        max_length=24,
        choices=[("RemoteUrlEnvelope", "Envelope"), ("ProviderFile", "Provider")],
    )
    envelope_id = models.UUIDField(null=True)
    provider_recording = models.ForeignKey(
        AttachmentProviderRecording, null=True, on_delete=models.PROTECT
    )
    mime_type = models.CharField(max_length=32)
    declared_bytes = models.PositiveBigIntegerField()
    declared_duration_ms = models.PositiveIntegerField()
    window_bytes = models.PositiveBigIntegerField()
    max_segment_ms = models.PositiveIntegerField()
    recording_bytes = models.PositiveBigIntegerField()
    state = models.CharField(
        max_length=16,
        choices=RecordingState.choices,
        default=RecordingState.IMPORTING,
    )
    reason = models.CharField(max_length=32, default="queued")
    attempts = models.PositiveSmallIntegerField(default=0)
    deadline = models.DateTimeField()
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    # Live in-flight reservation (source + window + staged segments).
    reserved_bytes = models.PositiveBigIntegerField()
    # Staged segments charged one object and one link each (``_reserve_segment``),
    # released with the rest of the reservation by ``_release``.
    reserved_segments = models.PositiveIntegerField(default=0)
    released_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    # Single-worker lease (imports._claim); the token fences late writers.
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(attempts__lte=3), name="attachment_import_attempts"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_kind="RemoteUrlEnvelope", envelope_id__isnull=False)
                    | models.Q(
                        source_kind="ProviderFile", provider_recording__isnull=False
                    )
                ),
                name="attachment_import_source_ref",
            ),
        ]
        indexes = [
            models.Index(
                fields=["state", "deadline"], name="attachment_import_sweep_idx"
            )
        ]
