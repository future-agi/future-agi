"""Reference-only fi.attachments.v1 admission. This module never reads media.

Projection validity is not association authority. Capture-session carriers are
consumed by the authenticated binding boundary and never persisted as attributes.
"""

import json
import re
from dataclasses import dataclass
from uuid import UUID

PREFIX = "fi.attachment."
SCHEMA = PREFIX + "schema_version"
REFS = PREFIX + "refs"
SESSION = PREFIX + "capture_session_id"
NONCE = PREFIX + "correlation_nonce"
MAX_BYTES = 16384
MAX_REFS = 64
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_HEX_TRACE = re.compile(r"[0-9a-f]{32}\Z")
_SPAN = re.compile(r"[0-9a-f]{16}\Z")
MIMES = frozenset(
    (
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
)
PURPOSES = frozenset(
    (
        "conversation_recording",
        "turn_recording",
        "model_input",
        "model_output",
        "tool_result",
        "computer_screenshot",
        "diagnostic",
        "document",
    )
)
_REQUIRED = frozenset(
    ("attachment_id", "occurrence_id", "direction", "modality", "mime_type")
)
_OPTIONAL = frozenset(("purpose", "message_index", "part_index", "role", "channel"))


class InvalidReference(ValueError):
    def __init__(self):
        # Never echo untrusted values into ingestion logs or error responses.
        super().__init__("invalid_attachment_reference")


def canonical_uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value) or not UUID(value).int:
        raise InvalidReference()
    return value


def canonical_trace_id(value):
    if isinstance(value, UUID):
        value = str(value)
    if isinstance(value, str) and _HEX_TRACE.fullmatch(value) and int(value, 16):
        return str(UUID(hex=value))
    return canonical_uuid(value)


def canonical_span_id(value):
    if not isinstance(value, str) or not _SPAN.fullmatch(value) or not int(value, 16):
        raise InvalidReference()
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidReference()
        result[key] = value
    return result


def validate_reference(ref):
    if (
        not isinstance(ref, dict)
        or not _REQUIRED <= ref.keys()
        or ref.keys() - (_REQUIRED | _OPTIONAL)
    ):
        raise InvalidReference()
    canonical_uuid(ref["attachment_id"])
    canonical_uuid(ref["occurrence_id"])
    for key, choices in (
        ("direction", ("input", "output", "mixed")),
        ("modality", ("audio", "image", "video", "document", "file")),
        ("mime_type", MIMES),
        ("purpose", PURPOSES),
        ("role", ("user", "assistant", "tool", "system", "unknown")),
    ):
        if key in ref and (not isinstance(ref[key], str) or ref[key] not in choices):
            raise InvalidReference()
    for key, maximum in (
        ("message_index", 65535),
        ("part_index", 65535),
        ("channel", 31),
    ):
        if key in ref and (type(ref[key]) is not int or not 0 <= ref[key] <= maximum):
            raise InvalidReference()
    return dict(ref)


def parse_references(version, raw):
    if type(version) is not int or version != 1 or not isinstance(raw, str):
        raise InvalidReference()
    try:
        if len(raw) > MAX_BYTES or len(raw.encode("utf-8")) > MAX_BYTES:
            raise InvalidReference()
        refs = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(refs, list) or len(refs) > MAX_REFS:
            raise InvalidReference()
        validated = tuple(validate_reference(ref) for ref in refs)
        if len({ref["occurrence_id"] for ref in validated}) != len(validated):
            raise InvalidReference()
        return validated
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise InvalidReference() from None


@dataclass(frozen=True)
class Projection:
    refs: tuple
    capture_session_id: str | None = None
    correlation_nonce: str | None = None


def extract_projection(attributes):
    """Validate the closed namespace, including paired non-secret carriers."""
    if not any(str(key).startswith(PREFIX) for key in attributes):
        return None
    if any(
        str(key).startswith(PREFIX) and key not in (SCHEMA, REFS, SESSION, NONCE)
        for key in attributes
    ):
        raise InvalidReference()
    refs = parse_references(attributes.get(SCHEMA), attributes.get(REFS))
    session, nonce = attributes.get(SESSION), attributes.get(NONCE)
    if SESSION in attributes or NONCE in attributes:
        canonical_uuid(session)
        canonical_uuid(nonce)
    return Projection(refs, session, nonce)


def normalize_attributes(attributes):
    """Strip invalid projections and all client authority claims; keep other attrs.

    Valid refs remain a non-authoritative display hint. Lookup always uses the
    authorized occurrence store, never these attributes, URLs or a blob ID.
    """
    result = {
        key: value
        for key, value in attributes.items()
        if not str(key).startswith(PREFIX)
    }
    try:
        projection = extract_projection(attributes)
    except InvalidReference:
        return result
    if projection is not None:
        result[SCHEMA] = 1
        result[REFS] = json.dumps(
            projection.refs, separators=(",", ":"), sort_keys=True
        )
    return result


def is_attachment_media(value):
    """Reserve our scheme even when malformed: never fall back to legacy upload."""
    if isinstance(value, str):
        if value.lstrip().lower().startswith("fi-attachment:"):
            return True
        if len(value) <= MAX_BYTES and value.lstrip().startswith("{"):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                return False
            uri = parsed.get("uri") if isinstance(parsed, dict) else None
            return isinstance(uri, str) and uri.lstrip().lower().startswith(
                "fi-attachment:"
            )
        return False
    uri = value.get("uri") if isinstance(value, dict) else None
    return isinstance(uri, str) and uri.lstrip().lower().startswith("fi-attachment:")
