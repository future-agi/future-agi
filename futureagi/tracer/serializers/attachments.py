"""OpenAPI documentation shapes for the F01 attachment routes.

Requests are validated against the vendored F01 JSON Schema in
``tracer.services.attachments.wire``; these serializers document the same
shapes for Swagger and the generated frontend contracts. Keep them in step
with ``tracer/services/attachments/f01/contract.schema.json``.
"""

from rest_framework import serializers

REASONS = (
    "queued",
    "disabled",
    "bootstrap_pending",
    "policy_denied",
    "credential_scope_unsupported",
    "source_unavailable",
    "unsampled",
    "too_large",
    "queue_full",
    "unsupported_version",
    "endpoint_unconfigured",
    "scope_unresolved",
    "correlation_unavailable",
    "redaction_unavailable",
    "recording_disabled",
    "binding_pending",
    "not_found",
    "conflict",
    "throttled",
    "validation_failed",
    "expired",
    "deleted",
    "incomplete",
    "ready",
)
STATUSES = (
    "pending_upload",
    "importing",
    "validating",
    "ready",
    "failed",
    "rejected",
    "expired",
    "deleted",
)
DIRECTIONS = ("input", "output", "mixed")
MODALITIES = ("audio", "image", "video", "document", "file")
MIME_TYPES = (
    "audio/wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "audio/flac",
    "audio/L16",
    "image/png",
    "image/jpeg",
    "image/webp",
    "video/mp4",
    "video/webm",
    "application/pdf",
    "application/octet-stream",
)
SOURCE_KINDS = (
    "Bytes",
    "Blob",
    "RemoteUrl",
    "LocalFile",
    "OwnedRecordingFile",
    "ProviderFile",
)
PURPOSES = (
    "conversation_recording",
    "turn_recording",
    "model_input",
    "model_output",
    "tool_result",
    "computer_screenshot",
    "diagnostic",
    "document",
)
REPRESENTATIONS = ("original", "playable", "channel", "peaks", "thumbnail")


class AttachmentEmptyRequestSerializer(serializers.Serializer):
    """The media-session exchange takes no fields; credentials carry everything."""


class AttachmentErrorSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=REASONS)
    request_id = serializers.UUIDField()


class AttachmentScopeSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    workspace_id = serializers.UUIDField()
    project_id = serializers.UUIDField()


class AttachmentParentSerializer(serializers.Serializer):
    trace_id = serializers.CharField(max_length=36)
    span_id = serializers.CharField(max_length=16)


class AttachmentAssociationSerializer(serializers.Serializer):
    capture_session_id = serializers.UUIDField()
    correlation_nonce = serializers.UUIDField()
    parent = AttachmentParentSerializer()


class AttachmentBootstrapRequestSerializer(serializers.Serializer):
    schema_versions = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=65535),
        min_length=1,
        max_length=8,
    )
    project_name = serializers.CharField(max_length=255)
    project_type = serializers.CharField(max_length=64)
    workspace_id = serializers.UUIDField(required=False)
    capture_session_id = serializers.UUIDField()
    correlation_nonce = serializers.UUIDField()


class AttachmentPolicySerializer(serializers.Serializer):
    version = serializers.CharField(max_length=128)
    enabled = serializers.BooleanField()
    allowed_sources = serializers.ListField(
        child=serializers.ChoiceField(choices=SOURCE_KINDS)
    )
    allowed_modalities = serializers.ListField(
        child=serializers.ChoiceField(choices=MODALITIES)
    )
    require_media_redaction = serializers.BooleanField()
    require_metadata_removal = serializers.BooleanField()
    allow_unredacted_media_with_text_pii = serializers.BooleanField()
    record_audio = serializers.BooleanField()
    record_video = serializers.BooleanField()


class AttachmentLimitsSerializer(serializers.Serializer):
    max_upload_bytes = serializers.IntegerField()
    segment_bytes = serializers.IntegerField()
    segment_duration_ms = serializers.IntegerField()
    recording_bytes = serializers.IntegerField()
    recording_duration_ms = serializers.IntegerField()
    active_transfers = serializers.IntegerField()
    active_recordings = serializers.IntegerField()
    reserved_bytes = serializers.IntegerField()
    validation_jobs = serializers.IntegerField()
    initializations_per_minute = serializers.IntegerField()
    links_per_minute = serializers.IntegerField()
    upload_bytes_per_second = serializers.IntegerField()
    playback_bytes_per_second = serializers.IntegerField()
    transfer_timeout_seconds = serializers.IntegerField()
    grant_ttl_seconds = serializers.IntegerField()
    revocation_check_seconds = serializers.IntegerField()


class AttachmentBootstrapResponseSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    scope = AttachmentScopeSerializer()
    capture_session_id = serializers.UUIDField()
    policy = AttachmentPolicySerializer()
    limits = AttachmentLimitsSerializer()
    expires_at = serializers.DateTimeField()
    transfer_modes = serializers.ListField(child=serializers.CharField())


class AttachmentUploadRequestSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    attachment_id = serializers.UUIDField()
    occurrence_id = serializers.UUIDField()
    association = AttachmentAssociationSerializer()
    policy_version = serializers.CharField(max_length=128)
    deletion_generation = serializers.IntegerField(min_value=0)
    mime_type = serializers.ChoiceField(choices=MIME_TYPES)
    modality = serializers.ChoiceField(choices=MODALITIES)
    direction = serializers.ChoiceField(choices=DIRECTIONS)
    source_kind = serializers.ChoiceField(choices=SOURCE_KINDS)
    purpose = serializers.ChoiceField(choices=PURPOSES, required=False)
    declared_bytes = serializers.IntegerField(min_value=1, max_value=52428800)
    message_index = serializers.IntegerField(required=False)
    part_index = serializers.IntegerField(required=False)
    recording_id = serializers.UUIDField(required=False)
    segment_index = serializers.IntegerField(required=False)
    conversation_id = serializers.CharField(max_length=128, required=False)
    turn_id = serializers.CharField(max_length=128, required=False)
    tool_call_id = serializers.CharField(max_length=128, required=False)


class AttachmentUploadResponseSerializer(serializers.Serializer):
    upload_id = serializers.UUIDField()
    attachment_id = serializers.UUIDField()
    occurrence_id = serializers.UUIDField()
    state = serializers.CharField()
    reserved_bytes = serializers.IntegerField()
    expires_at = serializers.DateTimeField()
    transfer_mode = serializers.CharField()
    write_capability = serializers.CharField(max_length=4096)


class AttachmentCompleteRequestSerializer(serializers.Serializer):
    policy_version = serializers.CharField(max_length=128)
    deletion_generation = serializers.IntegerField(min_value=0)


class AttachmentStatusResponseSerializer(serializers.Serializer):
    upload_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=STATUSES)
    reason = serializers.ChoiceField(choices=REASONS)


class AttachmentImportRequestSerializer(serializers.Serializer):
    mode = serializers.CharField(required=False)
    recording = serializers.DictField(required=False)
    upload = serializers.DictField(required=False)
    source = serializers.DictField()


class AttachmentImportResponseSerializer(serializers.Serializer):
    mode = serializers.CharField()
    job_id = serializers.UUIDField()
    recording_id = serializers.UUIDField()
    state = serializers.CharField()


class AttachmentImportStatusResponseSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=STATUSES)
    reason = serializers.ChoiceField(choices=REASONS)


class AttachmentReferenceSerializer(serializers.Serializer):
    attachment_id = serializers.UUIDField()
    occurrence_id = serializers.UUIDField()
    direction = serializers.ChoiceField(choices=DIRECTIONS)
    modality = serializers.ChoiceField(choices=MODALITIES)
    mime_type = serializers.ChoiceField(choices=MIME_TYPES)
    purpose = serializers.ChoiceField(choices=PURPOSES, required=False)
    message_index = serializers.IntegerField(
        min_value=0, max_value=65535, required=False
    )
    part_index = serializers.IntegerField(min_value=0, max_value=65535, required=False)
    role = serializers.ChoiceField(
        choices=("user", "assistant", "tool", "system", "unknown"), required=False
    )
    channel = serializers.IntegerField(min_value=0, max_value=31, required=False)
    conversation_id = serializers.CharField(max_length=128, required=False)
    turn_id = serializers.CharField(max_length=128, required=False)
    tool_call_id = serializers.CharField(max_length=128, required=False)


class AttachmentOccurrenceSerializer(serializers.Serializer):
    reference = AttachmentReferenceSerializer()
    scope = AttachmentScopeSerializer()
    parent = AttachmentParentSerializer()
    state = serializers.ChoiceField(choices=STATUSES)
    binding = serializers.ChoiceField(choices=("pending", "bound"))
    capture_complete = serializers.BooleanField()
    policy_version = serializers.CharField(max_length=128)
    deletion_generation = serializers.IntegerField()
    expires_at = serializers.DateTimeField()
    recording_id = serializers.UUIDField(required=False)


class AttachmentOccurrencePageSerializer(serializers.Serializer):
    items = AttachmentOccurrenceSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True, max_length=128)


class AttachmentOccurrenceQuerySerializer(serializers.Serializer):
    trace_id = serializers.CharField(max_length=36)
    span_id = serializers.CharField(max_length=16, required=False)
    cursor = serializers.CharField(max_length=128, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False)


class AttachmentParentQuerySerializer(serializers.Serializer):
    trace_id = serializers.CharField(max_length=36)
    span_id = serializers.CharField(max_length=16)


class AttachmentLinkRequestSerializer(serializers.Serializer):
    source_occurrence_id = serializers.UUIDField()
    source_parent = AttachmentParentSerializer()
    target_parent = AttachmentParentSerializer()
    occurrence_id = serializers.UUIDField()
    policy_version = serializers.CharField(max_length=128)
    deletion_generation = serializers.IntegerField(min_value=0)


class AttachmentResolveItemSerializer(serializers.Serializer):
    occurrence_id = serializers.UUIDField()
    parent = AttachmentParentSerializer()
    representation = serializers.ChoiceField(choices=REPRESENTATIONS)
    channel = serializers.IntegerField(min_value=0, max_value=31, required=False)


class AttachmentResolveRequestSerializer(serializers.Serializer):
    items = AttachmentResolveItemSerializer(many=True)


class AttachmentResolveResultSerializer(serializers.Serializer):
    occurrence_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=("ready", "not_found", "pending", "failed", "expired", "deleted")
    )
    gateway_path = serializers.CharField(max_length=194, required=False)
    expires_at = serializers.DateTimeField(required=False)


class AttachmentResolveResponseSerializer(serializers.Serializer):
    items = AttachmentResolveResultSerializer(many=True)


class AttachmentTrackSerializer(serializers.Serializer):
    track_id = serializers.UUIDField()
    channels = serializers.ListField(child=serializers.IntegerField())
    direction = serializers.ChoiceField(choices=DIRECTIONS)
    role = serializers.ChoiceField(choices=("user", "assistant", "mixed", "unknown"))


class AttachmentSegmentSerializer(serializers.Serializer):
    occurrence_id = serializers.UUIDField()
    index = serializers.IntegerField()
    start_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    track_id = serializers.UUIDField(required=False)


class AttachmentGapSerializer(serializers.Serializer):
    start_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    reason = serializers.CharField()
    track_id = serializers.UUIDField(required=False)


class AttachmentPlayoutSerializer(serializers.Serializer):
    start_ms = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    evidence = serializers.ChoiceField(
        choices=("generated", "played", "unknown", "truncated")
    )


class AttachmentRecordingManifestSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    recording_id = serializers.UUIDField()
    timebase = serializers.CharField()
    sample_format = serializers.CharField()
    sample_rate_hz = serializers.IntegerField()
    channels = serializers.IntegerField()
    codec = serializers.CharField()
    container = serializers.CharField()
    layout = serializers.ChoiceField(choices=("mixed", "stereo", "separate_tracks"))
    tracks = AttachmentTrackSerializer(many=True)
    segments = AttachmentSegmentSerializer(many=True)
    gaps = AttachmentGapSerializer(many=True)
    playout = AttachmentPlayoutSerializer(many=True)
    capture_complete = serializers.BooleanField()
    duration_ms = serializers.IntegerField()
