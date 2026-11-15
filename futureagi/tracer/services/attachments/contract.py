"""Content-free errors, canonical identifiers and deny-wins admission."""

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass


class AttachmentError(Exception):
    """Only a fixed reason code may leave this boundary, never a raw exception."""

    def __init__(self, code, status=400):
        self.code = code
        self.status = status
        super().__init__(code)


def trace_uuid(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        value,
    ):
        raise AttachmentError("invalid_trace_id")
    result = uuid.UUID(value)
    if not result.int:
        raise AttachmentError("invalid_trace_id")
    return result


def span_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-f]{16}", value)
        or int(value, 16) == 0
    ):
        raise AttachmentError("invalid_span_id")
    return value


# F01 Reference placement, persisted on AttachmentOccurrence and immutable there
# (0133 identity loop). ``role`` and ``channel`` are Reference fields that
# UploadRequest does not carry, so an upload never sets them.
PLACEMENT_FIELDS = (
    "message_index",
    "part_index",
    "role",
    "channel",
    "conversation_id",
    "turn_id",
    "tool_call_id",
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class CapturePolicy:
    enabled: bool = False
    capture_content: bool = True
    hide_inputs: bool = False
    hide_outputs: bool = False
    hide_input_text: bool = False
    hide_output_text: bool = False
    hide_error_details: bool = False
    pii_redaction: bool = False
    allow_unredacted_media_with_text_pii: bool = False
    # There is no qualified local media sanitizer in Wave A.
    record_audio: bool = False
    # Importing an already-completed recording is its own explicit permission;
    # record_audio (starting a recorder) never implies it, nor the reverse.
    allow_import: bool = False

    def validate(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise AttachmentError("invalid_policy")


# F01 SourceKind values the server accepts for an authenticated byte upload.
# RemoteUrl/LocalFile/ProviderFile never reach the upload route: the SDK
# resolves owned files itself and remote sources only arrive through imports.
UPLOAD_SOURCES = ("Bytes", "Blob", "OwnedRecordingFile")
# Server-internal kinds written by trusted server paths, never by a client body.
INTERNAL_SOURCES = ("imported_recording",)
# Import source kinds that a DEPLOYED route can actually create: "RemoteUrl"
# needs a mounted trusted resolver that seals envelopes (``envelopes.seal``);
# "ProviderFile" needs a mounted provider registration and webhook intake
# (``provider_recordings.register``/``verify_livekit_webhook``) and, in
# addition, a qualified output reader. The route that mounts such a path adds
# its kind here. Empty today: nothing deployed calls either, so advertising a
# kind would promise an import that no client can start.
IMPORT_SOURCE_PRODUCERS: set[str] = set()
# Only types whose bytes the validation worker can settle are accepted, and only
# their modalities are advertised. Accepting a type nothing can validate would
# strand uploads in validating. ``probe.py`` sniffs each of these; any other
# declared type (audio/L16, application/octet-stream) is refused at admission.
SERVER_MODALITIES = {
    "audio": (
        "audio/wav",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/webm",
        "audio/flac",
    ),
    "document": ("application/pdf",),
    "image": ("image/png", "image/jpeg", "image/webp"),
    "video": ("video/mp4", "video/webm"),
}
# Recording segments feed PCM timeline manifests; they stay PCM WAV.
SEGMENT_MIME_TYPES = ("audio/wav",)


def server_policy(scope):
    """The scope's capture policy. Clients never supply policy to the server.

    Client-side hide/PII flags are applied by the SDK before egress; the server
    enforces the scope switches and re-checks them on every later operation.
    """
    return CapturePolicy(
        enabled=bool(scope.enabled),
        allow_unredacted_media_with_text_pii=bool(
            scope.allow_unredacted_media_with_text_pii
        ),
        record_audio=bool(scope.record_audio),
        allow_import=bool(scope.allow_import),
    )


def allowed_sources(scope):
    """F01 SourceKinds this scope accepts: uploads, plus serviceable imports.

    An import kind is advertised only while ``allow_import`` is on AND a
    deployed route can create that source (``IMPORT_SOURCE_PRODUCERS``).
    ``ProviderFile`` also needs a qualified provider output reader, because
    without one every such import fails ``source_unavailable``.
    """
    sources = list(UPLOAD_SOURCES)
    if scope.allow_import:
        from tracer.services.attachments.provider_recordings import READERS

        if "RemoteUrl" in IMPORT_SOURCE_PRODUCERS:
            sources.append("RemoteUrl")
        if "ProviderFile" in IMPORT_SOURCE_PRODUCERS and READERS:
            sources.append("ProviderFile")
    return sources


def policy_document(scope):
    """F01 ``Policy``: the server's current, versioned decision for this scope."""
    enabled = bool(scope.enabled)
    return {
        "version": str(scope.policy_version),
        "enabled": enabled,
        "allowed_sources": allowed_sources(scope) if enabled else [],
        "allowed_modalities": sorted(SERVER_MODALITIES) if enabled else [],
        "require_media_redaction": bool(scope.require_media_redaction),
        "require_metadata_removal": False,
        "allow_unredacted_media_with_text_pii": bool(
            scope.allow_unredacted_media_with_text_pii
        ),
        "record_audio": bool(scope.record_audio),
        "record_video": False,
    }


def admit_audio(
    scope,
    policy,
    *,
    direction,
    source_kind,
    creates_recording=False,
    error_body=False,
    modality="audio",
):
    """Deny-wins admission for every modality (the name predates images/video).

    Hide and PII flags apply to all media: screenshots and documents carry text
    too. Only audio may form a recording; ``record_video`` is never offered.
    """
    policy.validate()
    if not scope.enabled or not policy.enabled:
        raise AttachmentError("disabled", 403)
    if not policy.capture_content:
        raise AttachmentError("policy_denied", 403)
    if direction not in {"input", "output", "mixed"}:
        raise AttachmentError("invalid_direction")
    if source_kind not in UPLOAD_SOURCES + INTERNAL_SOURCES:
        raise AttachmentError("source_unavailable", 403)
    if creates_recording and (modality != "audio" or not policy.record_audio):
        raise AttachmentError("recording_disabled", 403)
    if (
        (
            direction in {"input", "mixed"}
            and (policy.hide_inputs or policy.hide_input_text)
        )
        or (
            direction in {"output", "mixed"}
            and (policy.hide_outputs or policy.hide_output_text)
        )
        or (error_body and policy.hide_error_details)
    ):
        raise AttachmentError("policy_denied", 403)
    if scope.require_media_redaction or (
        policy.pii_redaction
        and not (
            scope.allow_unredacted_media_with_text_pii
            and policy.allow_unredacted_media_with_text_pii
        )
    ):
        raise AttachmentError("redaction_unavailable", 403)
