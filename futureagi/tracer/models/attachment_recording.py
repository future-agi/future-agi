"""Recording, completed-import and provider-recording control records.

None of these rows grants read access. A recording becomes playable only via
its published segment occurrences, which the occurrence/gateway checks govern.
Durable rows hold opaque references only: no URL, signed query, filename,
provider credential or source metadata is stored in plain text anywhere here.
"""

from datetime import timedelta

from django.db import models
from django.db.models.functions import Coalesce
from django.db.models.lookups import Exact, GreaterThan, LessThanOrEqual

# The recording timeline row is shared with streamed recordings and lives in
# tracer.models.attachment; imports set its state to ``importing`` explicitly.
from tracer.models.attachment import (  # noqa: F401
    AttachmentRecording,
    ScopedAttachmentRecord,
)

from .attachment_storage import (
    RESERVATION_REQUIRED_FIELDS,
    AttachmentReservationFields,
    all_or_none,
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
    storage_version = models.CharField(max_length=255, null=True)
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

    import_job = models.ForeignKey(
        "tracer.AttachmentImportJob", null=True, on_delete=models.PROTECT
    )
    storage_binding = models.ForeignKey(
        "tracer.AttachmentStorageBinding", null=True, on_delete=models.PROTECT
    )
    attachment = models.ForeignKey(
        "tracer.Attachment", null=True, on_delete=models.PROTECT
    )
    storage_object_id = models.UUIDField(null=True, unique=True)
    quarantine_key = models.CharField(max_length=255, null=True)
    allocation_epoch = models.PositiveBigIntegerField(default=0)
    write_attempts = models.PositiveSmallIntegerField(default=0)
    write_deadline_at = models.DateTimeField(null=True)
    cleanup_not_before = models.DateTimeField(null=True)
    cleanup_checked_at = models.DateTimeField(null=True)
    unowned_cleanup_due_at = models.DateTimeField(null=True)
    erased_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(write_attempts__lte=3),
                name="att_segment_write_attempts",
            ),
            models.UniqueConstraint(
                fields=["recording", "index"], name="attachment_segment_index_unique"
            ),
            models.UniqueConstraint(
                fields=["scope", "occurrence_client_id"],
                name="attachment_segment_occurrence_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["unowned_cleanup_due_at", "id"],
                condition=models.Q(unowned_cleanup_due_at__isnull=False),
                name="att_segment_unowned_due",
            ),
            # ``derivatives.pending``: open published segments only, in queue
            # order, so the queue never walks segments that are done.
            models.Index(
                Coalesce("derivative_checked_at", "created_at"),
                models.F("id"),
                condition=models.Q(state="published", derivatives_done_at__isnull=True),
                name="attachment_derivative_due_idx",
            ),
        ]


class AttachmentRecordingDerivative(
    AttachmentReservationFields, ScopedAttachmentRecord
):
    """Asset-owned channel/peaks allocation; occurrences alone grant access."""

    segment = models.ForeignKey(
        AttachmentRecordingSegment, null=True, on_delete=models.PROTECT
    )
    representation = models.CharField(max_length=16)
    storage_key = models.CharField(max_length=255)
    storage_version = models.CharField(max_length=255, null=True)
    sha256 = models.CharField(max_length=64, null=True)
    byte_length = models.PositiveBigIntegerField(null=True)
    facts = models.JSONField(default=dict)
    policy_version = models.PositiveBigIntegerField()
    deletion_generation = models.PositiveBigIntegerField()
    published_at = models.DateTimeField(null=True)

    attachment = models.ForeignKey(
        "tracer.Attachment", null=True, on_delete=models.PROTECT
    )
    state = models.CharField(
        max_length=16,
        default="queued",
        choices=[
            (v, v)
            for v in (
                "queued",
                "recovering",
                "writing",
                "ready",
                "cleanup",
                "erased",
                "failed",
            )
        ],
    )
    storage_object_id = models.UUIDField(null=True, unique=True)
    quarantine_key = models.CharField(max_length=255, null=True)
    mime_type = models.CharField(max_length=64, null=True)
    sample_rate = models.PositiveIntegerField(null=True)
    channels = models.PositiveSmallIntegerField(null=True)
    frame_count = models.PositiveBigIntegerField(null=True)
    lease_token = models.UUIDField(null=True)
    lease_epoch = models.PositiveBigIntegerField(default=0)
    lease_until = models.DateTimeField(null=True)
    write_attempts = models.PositiveSmallIntegerField(default=0)
    write_deadline_at = models.DateTimeField(null=True)
    cleanup_not_before = models.DateTimeField(null=True)
    cleanup_checked_at = models.DateTimeField(null=True)
    unowned_cleanup_due_at = models.DateTimeField(null=True)
    available_at = models.DateTimeField(null=True)
    erased_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["available_at", "id"],
                condition=models.Q(
                    state__in=["queued", "recovering", "writing", "cleanup"]
                ),
                name="att_derivative_work_due",
            ),
            # Workers select production, cleanup and due ready reconciliation
            # in one ordered page; the production-only index omits two branches.
            models.Index(
                fields=["available_at", "id"],
                condition=models.Q(
                    state__in=["queued", "recovering", "writing", "cleanup", "failed"]
                )
                | models.Q(state="ready", unowned_cleanup_due_at__isnull=False),
                name="att_derivative_dispatch_due",
            ),
            models.Index(
                fields=["unowned_cleanup_due_at", "id"],
                condition=models.Q(unowned_cleanup_due_at__isnull=False),
                name="att_derivative_unowned_due",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=all_or_none(
                    "storage_version", "sha256", "byte_length", "mime_type"
                ),
                name="att_derivative_verified_group",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "queued",
                        "recovering",
                        "writing",
                        "ready",
                        "cleanup",
                        "erased",
                        "failed",
                    ]
                ),
                name="att_derivative_state",
            ),
            models.CheckConstraint(
                condition=all_or_none(*RESERVATION_REQUIRED_FIELDS),
                name="att_deriv_reservation_group",
            ),
            models.CheckConstraint(
                condition=models.Q(write_attempts__lte=3),
                name="att_derivative_write_attempts",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    representation__in=["channel-0", "channel-1", "peaks", "playable"]
                ),
                name="att_derivative_representation",
            ),
            models.UniqueConstraint(
                fields=["attachment", "representation"],
                name="attachment_derivative_asset_unique",
            ),
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

    generation_kind = models.CharField(max_length=24, null=True)
    generation = models.CharField(max_length=255, null=True)
    mime_type = models.CharField(max_length=64, null=True)
    duration_ms = models.PositiveIntegerField(null=True)
    source_expires_at = models.DateTimeField(null=True)
    authority_generation = models.PositiveBigIntegerField(default=0)
    trace_id = models.UUIDField(null=True)
    span_id = models.CharField(max_length=16, null=True)
    revoked_at = models.DateTimeField(null=True)


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

    authority_generation = models.PositiveBigIntegerField(default=0)

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

    source_generation_kind = models.CharField(max_length=24, null=True)
    source_generation = models.CharField(max_length=255, null=True)
    source_mime_type = models.CharField(max_length=64, null=True)
    source_expires_at = models.DateTimeField(null=True)
    source_authority_generation = models.PositiveBigIntegerField(null=True)
    # Bare UUID deliberately outlives deletion of short-lived sealed material.
    output_envelope_id = models.UUIDField(null=True)
    dispatch_mode = models.CharField(
        max_length=12,
        choices=[("auto", "auto"), ("sdk_explicit", "sdk_explicit")],
        default="auto",
    )
    dispatch_due_at = models.DateTimeField(null=True)
    dispatch_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["dispatch_due_at", "id"],
                condition=models.Q(
                    dispatch_mode="auto",
                    provider_state="complete",
                    import_job_id__isnull=True,
                    dispatch_due_at__isnull=False,
                ),
                name="att_provider_dispatch_due",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(dispatch_mode__in=["auto", "sdk_explicit"]),
                name="att_provider_dispatch_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(dispatch_mode="auto")
                | models.Q(dispatch_due_at__isnull=True),
                name="att_provider_explicit_idle",
            ),
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
    pending_egress_id = models.CharField(max_length=64, null=True)
    pending_expires_at = models.DateTimeField(null=True)
    pending_due_at = models.DateTimeField(null=True)
    pending_key_id = models.CharField(max_length=16, null=True)
    pending_nonce = models.BinaryField(max_length=12, null=True)
    pending_ciphertext = models.BinaryField(max_length=65552, null=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["connection", "pending_egress_id", "id"],
                condition=models.Q(pending_ciphertext__isnull=False),
                name="att_provider_pending_conn",
            ),
            models.Index(
                fields=["pending_due_at", "id"],
                condition=models.Q(pending_ciphertext__isnull=False),
                name="att_provider_pending_due",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=all_or_none(
                    "pending_egress_id",
                    "pending_expires_at",
                    "pending_due_at",
                    "pending_key_id",
                    "pending_nonce",
                    "pending_ciphertext",
                ),
                name="att_provider_pending_group",
            ),
            models.CheckConstraint(
                condition=models.Q(pending_ciphertext__isnull=True)
                | (
                    Exact(
                        models.Func(
                            models.F("pending_nonce"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        12,
                    )
                    & GreaterThan(
                        models.Func(
                            models.F("pending_ciphertext"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        16,
                    )
                    & LessThanOrEqual(
                        models.Func(
                            models.F("pending_ciphertext"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        65552,
                    )
                ),
                name="att_provider_pending_lengths",
            ),
            models.CheckConstraint(
                condition=models.Q(pending_expires_at__isnull=True)
                | (
                    models.Q(pending_expires_at__gt=models.F("created_at"))
                    & models.Q(
                        pending_expires_at__lte=models.F("created_at")
                        + timedelta(seconds=600)
                    )
                ),
                name="att_provider_pending_lifetime",
            ),
            models.CheckConstraint(
                condition=models.Q(pending_due_at__isnull=True)
                | models.Q(pending_due_at__lte=models.F("pending_expires_at")),
                name="att_provider_pending_due_cap",
            ),
            models.UniqueConstraint(
                fields=["connection", "event_id"], name="attachment_provider_event"
            ),
        ]


class AttachmentImportJob(AttachmentReservationFields, ScopedAttachmentRecord):
    """Durable job. Only opaque references; reasons are F01 Reason codes."""

    recording = models.OneToOneField(
        AttachmentRecording, null=True, on_delete=models.PROTECT
    )
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
    finished_at = models.DateTimeField(null=True)
    # Single-worker lease (imports._claim); the token fences late writers.
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)

    upload = models.OneToOneField(
        "tracer.AttachmentUpload", null=True, on_delete=models.PROTECT
    )
    storage_binding = models.ForeignKey(
        "tracer.AttachmentStorageBinding", null=True, on_delete=models.PROTECT
    )
    capture_session = models.ForeignKey(
        "tracer.CaptureSession", null=True, on_delete=models.PROTECT
    )
    lease_epoch = models.PositiveBigIntegerField(default=0)
    lease_phase = models.CharField(
        max_length=16,
        default="idle",
        choices=[
            (v, v) for v in ("idle", "recovering", "writing", "publishing", "terminal")
        ],
    )
    cleanup_not_before = models.DateTimeField(null=True)
    cleanup_checked_at = models.DateTimeField(null=True)
    scratch_id = models.UUIDField(null=True)
    source_generation_kind = models.CharField(max_length=24, null=True)
    source_generation = models.CharField(max_length=255, null=True)
    source_byte_length = models.PositiveBigIntegerField(null=True)
    source_mime_type = models.CharField(max_length=64, null=True)
    source_duration_ms = models.PositiveIntegerField(null=True)
    source_expires_at = models.DateTimeField(null=True)
    source_authority_generation = models.PositiveBigIntegerField(null=True)
    source_creator_kind = models.CharField(max_length=16, null=True)
    source_creator_id = models.UUIDField(null=True)
    source_trace_id = models.UUIDField(null=True)
    source_span_id = models.CharField(max_length=16, null=True)
    source_direction = models.CharField(max_length=8, null=True, choices=DIRECTIONS)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    lease_phase__in=[
                        "idle",
                        "recovering",
                        "writing",
                        "publishing",
                        "terminal",
                    ]
                ),
                name="att_import_lease_phase",
            ),
            models.CheckConstraint(
                condition=all_or_none(*RESERVATION_REQUIRED_FIELDS),
                name="att_import_reservation_group",
            ),
            models.CheckConstraint(
                condition=models.Q(recording__isnull=False, upload__isnull=True)
                | models.Q(recording__isnull=True, upload__isnull=False),
                name="att_import_target_xor",
            ),
            models.CheckConstraint(
                condition=models.Q(attempts__lte=3), name="attachment_import_attempts"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source_kind="RemoteUrlEnvelope",
                        envelope_id__isnull=False,
                        provider_recording__isnull=True,
                    )
                    | models.Q(
                        source_kind="ProviderFile",
                        provider_recording__isnull=False,
                        envelope_id__isnull=True,
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


class AttachmentImportSchedule(models.Model):
    """Scheduler cursor only. Never grant or extend a writer lease."""

    job = models.OneToOneField(
        AttachmentImportJob,
        primary_key=True,
        related_name="schedule",
        on_delete=models.CASCADE,
    )
    retry_at = models.DateTimeField()
