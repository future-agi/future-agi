"""OpenAPI documentation shapes for the F01 attachment routes.

Requests are validated against the vendored F01 JSON Schema in
``tracer.services.attachments.wire``; these serializers document the same
shapes for Swagger and the generated frontend contracts. Keep them in step
with ``tracer/services/attachments/f01/contract.schema.json``.
"""

from rest_framework import serializers

from tracer.services.attachments import wire as _attachment_wire

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
    gateway_path = serializers.CharField(max_length=230, required=False)
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


# Product control serializers run after writer authorization or raw-byte
# webhook verification. The wire and provider checks still enforce provenance.
_CONTROL_F01 = _attachment_wire._schema()["$defs"]


class AttachmentControlUUIDField(serializers.UUIDField):
    class Meta:
        swagger_schema_fields = _CONTROL_F01["UUID"]


class AttachmentControlParentSerializer(AttachmentParentSerializer):
    class Meta:
        swagger_schema_fields = {
            "additionalProperties": False,
            "properties": {
                "trace_id": {
                    "type": "string",
                    "maxLength": _CONTROL_F01["UUID"]["maxLength"],
                    "pattern": "|".join(
                        f"(?:{variant['pattern']})"
                        for variant in (
                            _CONTROL_F01["TraceInput"]["oneOf"][0],
                            _CONTROL_F01["UUID"],
                        )
                    ),
                    # Preserve the canonical alternatives for extension-aware
                    # tools; the plain Swagger pattern also accepts both forms.
                    "x-oneOf": [
                        {"type": "string", **_CONTROL_F01["TraceInput"]["oneOf"][0]},
                        _CONTROL_F01["UUID"],
                    ],
                },
                "span_id": _CONTROL_F01["SpanID"],
            },
        }


class AttachmentControlAssociationSerializer(AttachmentAssociationSerializer):
    capture_session_id = AttachmentControlUUIDField()
    correlation_nonce = AttachmentControlUUIDField()
    parent = AttachmentControlParentSerializer()

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentProviderRegistrationRequestSerializer(serializers.Serializer):
    """Register an existing egress; never start one or accept a source URL."""

    connection_id = serializers.UUIDField()
    egress_id = serializers.RegexField(r"^EG_[A-Za-z0-9]{1,61}$")
    room_name = serializers.CharField(
        min_length=1, max_length=256, trim_whitespace=False
    )
    recording_id = AttachmentControlUUIDField()
    association = AttachmentControlAssociationSerializer()
    direction = serializers.ChoiceField(choices=DIRECTIONS)
    dispatch_mode = serializers.ChoiceField(
        choices=("auto", "sdk_explicit"), default="auto", required=False
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentProviderFileSerializer(serializers.Serializer):
    """The opaque ProviderFile branch of F01 ImportSource, not a locator."""

    kind = serializers.ChoiceField(choices=("ProviderFile",))
    connection_id = AttachmentControlUUIDField()
    registered_output_id = AttachmentControlUUIDField()

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


_SWAGGER2_ALTERNATIVES = {"oneOf": "x-oneOf", "anyOf": "x-anyOf"}


def _swagger2(value):
    """Spell JSON Schema alternatives in a form Swagger 2 accepts.

    Swagger 2 has no oneOf, anyOf or const. Keep alternatives as the vendor
    extensions the rest of this contract already uses and write const as a
    one-value enum, which accepts exactly the same documents.
    """
    if isinstance(value, list):
        return [_swagger2(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted = {}
    for key, item in value.items():
        if key == "const":
            if "enum" in value:
                raise ValueError("attachment schema has both const and enum")
            converted["enum"] = [item]
        else:
            converted[_SWAGGER2_ALTERNATIVES.get(key, key)] = _swagger2(item)
    return converted


def _control_f01_schema(name):
    """Inline only local vendored refs; never fetch a schema URL.

    The result documents Swagger 2 only; request validation uses the vendored
    JSON Schema itself.
    """

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if not reference.startswith("#/$defs/"):
                    raise ValueError("nonlocal attachment schema reference")
                return expand(_CONTROL_F01[reference.removeprefix("#/$defs/")])
            return {key: expand(item) for key, item in value.items()}
        return value

    return _swagger2(expand(_CONTROL_F01[name]))


class AttachmentControlImportSourceField(serializers.JSONField):
    class Meta:
        swagger_schema_fields = {
            "type": "object",
            "x-oneOf": _control_f01_schema("ImportSource")["x-oneOf"],
        }


class AttachmentCompletedRecordingImportSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField(min_value=1, max_value=1)
    association = AttachmentControlAssociationSerializer()
    policy_version = serializers.CharField(max_length=128)
    deletion_generation = serializers.IntegerField(min_value=0)
    mime_type = serializers.ChoiceField(
        choices=_CONTROL_F01["CompletedRecordingImport"]["properties"]["mime_type"][
            "enum"
        ]
    )
    direction = serializers.ChoiceField(choices=DIRECTIONS)
    source_kind = serializers.ChoiceField(choices=("ProviderFile", "RemoteUrl"))
    recording_id = AttachmentControlUUIDField()
    modality = serializers.ChoiceField(choices=("audio",))
    declared_bytes = serializers.IntegerField(min_value=1, max_value=1073741824)
    declared_duration_ms = serializers.IntegerField(min_value=1, max_value=7200000)


class AttachmentImportRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("completed_recording",), required=False)
    recording = AttachmentCompletedRecordingImportSerializer(required=False)
    upload = AttachmentUploadRequestSerializer(required=False)

    def get_fields(self):
        fields = super().get_fields()
        fields["source"] = AttachmentControlImportSourceField()
        return fields

    class Meta:
        swagger_schema_fields = {
            "additionalProperties": False,
            "x-oneOf": _control_f01_schema("ImportRequest")["x-oneOf"],
        }


class AttachmentImportResponseSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("completed_recording",), required=False)
    job_id = AttachmentControlUUIDField()
    upload_id = AttachmentControlUUIDField(required=False)
    recording_id = AttachmentControlUUIDField(required=False)
    state = serializers.ChoiceField(choices=("importing",))

    class Meta:
        swagger_schema_fields = {
            "additionalProperties": False,
            "x-oneOf": _control_f01_schema("ImportResponse")["x-oneOf"],
        }


class AttachmentControlRecordingImportField(serializers.JSONField):
    class Meta:
        swagger_schema_fields = _control_f01_schema("RecordingImportRequest")


class AttachmentProviderBindingSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=("livekit",))
    egress_id = serializers.RegexField(r"^EG_[A-Za-z0-9]{1,61}$")
    room_digest = serializers.RegexField(r"^[0-9a-f]{64}$")

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentProviderRegistrationResponseSerializer(serializers.Serializer):
    state = serializers.ChoiceField(
        choices=(
            "registered",
            "active",
            "ending",
            "complete",
            "failed",
            "aborted",
            "limit_reached",
        )
    )
    dispatch_mode = serializers.ChoiceField(choices=("auto", "sdk_explicit"))
    binding = AttachmentProviderBindingSerializer()
    import_request = AttachmentControlRecordingImportField(required=False)
    job_id = AttachmentControlUUIDField(required=False)

    def get_fields(self):
        fields = super().get_fields()
        fields["source"] = AttachmentProviderFileSerializer()
        return fields

    class Meta:
        swagger_schema_fields = {
            "additionalProperties": False,
            "x-allOf": _swagger2(
                [
                    {
                        "if": {"properties": {"state": {"const": "complete"}}},
                        "then": {"required": ["import_request"]},
                        "else": {
                            "not": {
                                "anyOf": [
                                    {"required": ["import_request"]},
                                    {"required": ["job_id"]},
                                ]
                            }
                        },
                    }
                ]
            ),
        }


class AttachmentOpaqueEnvelopeSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("RemoteUrlEnvelope",))
    envelope_id = AttachmentControlUUIDField()

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentSourceEnvelopeResponseSerializer(serializers.Serializer):
    registration = AttachmentProviderRegistrationResponseSerializer(
        required=False, allow_null=True
    )

    def get_fields(self):
        fields = super().get_fields()
        fields["source"] = AttachmentOpaqueEnvelopeSerializer()
        return fields

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentSourceEnvelopeRequestSerializer(serializers.Serializer):
    """Resolve a provider reference or inspect an opaque envelope; never a URL."""

    def get_fields(self):
        # As in AttachmentImportRequestSerializer, do not shadow Field.source.
        return {"source": AttachmentControlImportSourceField()}

    class Meta:
        swagger_schema_fields = {"additionalProperties": False}


class AttachmentLiveKitIntegerField(serializers.JSONField):
    """Documentation of protobuf JSON integers consumed by the provider reader."""

    class Meta:
        swagger_schema_fields = {
            "type": "integer",
            "x-anyOf": [
                {"type": "integer"},
                {"type": "string", "pattern": "^[0-9]+$", "maxLength": 20},
            ],
        }


class AttachmentLiveKitStatusField(serializers.JSONField):
    class Meta:
        swagger_schema_fields = {
            "type": "string",
            "x-anyOf": [{"type": "string"}, {"type": "integer"}],
            "description": "LiveKit EGRESS_* enum name or numeric enum value (0 through 6); unknown states do not advance a registration.",
        }


class AttachmentLiveKitFileResultSerializer(serializers.Serializer):
    location = serializers.CharField(
        required=False,
        help_text="Only a signed completion can select this locator; host approval and metadata/revision checks still apply.",
    )
    size = AttachmentLiveKitIntegerField(
        required=False, help_text="Bytes; completion requires 1 through 1073741824."
    )
    duration = AttachmentLiveKitIntegerField(
        required=False,
        help_text="Nanoseconds; completion requires 1 through 7200000000000.",
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}


class AttachmentLiveKitEgressInfoSerializer(serializers.Serializer):
    egressId = serializers.CharField(required=False)
    roomName = serializers.CharField(required=False)
    status = AttachmentLiveKitStatusField(required=False)
    updatedAt = AttachmentLiveKitIntegerField(required=False)
    endedAt = AttachmentLiveKitIntegerField(required=False)
    fileResults = AttachmentLiveKitFileResultSerializer(
        many=True,
        required=False,
        help_text="Completion requires exactly one result; other shapes do not publish an output.",
    )

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}


class AttachmentLiveKitEventRequestSerializer(serializers.Serializer):
    """Signed provider JSON, verified as original bytes before strict JSON decode.

    Additional provider fields are permitted, not trusted for authorization.
    Non-egress events and incomplete event shapes are acknowledged without
    processing; id/event/egressInfo are therefore not globally required.
    """

    id = serializers.CharField(
        required=False,
        help_text="Processing requires a nonempty event ID of at most 64 characters.",
    )
    event = serializers.CharField(
        required=False,
        help_text="Only egress_started, egress_updated and egress_ended are processed; other signed events are acknowledged.",
    )
    egressInfo = AttachmentLiveKitEgressInfoSerializer(required=False)

    class Meta:
        swagger_schema_fields = {"additionalProperties": True}
