"""F01 wire shapes: strict JSON, schema validation and public error codes.

Every HTTP shape check lives here so a contract revision touches one module.
The schema is the vendored F01 ``contract.schema.json`` (see ``f01/README.md``).
Only fixed reason codes leave this boundary; supplied values never do.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tracer.services.attachments.contract import AttachmentError

SCHEMA_PATH = Path(__file__).resolve().parent / "f01" / "contract.schema.json"
MAX_JSON_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024

# Internal service code -> (HTTP status, F01 Reason). One table for every route;
# the reason always stays inside the refinement set allowed for its HTTP class
# (SDK design 0.5, BR-6). Anything unknown is an opaque 503 ``incomplete``.
PUBLIC = {
    # 400: malformed or unsupported request shapes.
    "invalid_request": (400, "unsupported_version"),
    "invalid_trace_id": (400, "unsupported_version"),
    "invalid_span_id": (400, "unsupported_version"),
    "invalid_direction": (400, "unsupported_version"),
    "invalid_policy": (400, "unsupported_version"),
    "invalid_chunk": (400, "unsupported_version"),
    "length_mismatch": (400, "unsupported_version"),
    "unsupported_version": (400, "unsupported_version"),
    # A RecordingImportRequest the import admission rules reject (F01 fixtures).
    "validation_failed": (400, "unsupported_version"),
    "idempotency_key_required": (400, "unsupported_version"),
    # 401: credentials absent/ineligible, or an expired capability.
    "unauthenticated": (401, "credential_scope_unsupported"),
    "capability_expired": (401, "expired"),
    # 403: policy and eligibility denials.
    "disabled": (403, "policy_denied"),
    "policy_denied": (403, "policy_denied"),
    "unsupported_media": (403, "policy_denied"),
    "source_unavailable": (403, "policy_denied"),
    "redaction_unavailable": (403, "redaction_unavailable"),
    "recording_disabled": (403, "recording_disabled"),
    "correlation_unavailable": (403, "correlation_unavailable"),
    "credential_scope_unsupported": (403, "credential_scope_unsupported"),
    "permission_denied": (403, "credential_scope_unsupported"),
    # 404: unauthorized and nonexistent are indistinguishable.
    "not_found": (404, "not_found"),
    "scope_unresolved": (404, "scope_unresolved"),
    # 409: conflicts with existing state.
    "idempotency_conflict": (409, "conflict"),
    "transfer_conflict": (409, "conflict"),
    "incomplete_upload": (409, "conflict"),
    "not_validated": (409, "conflict"),
    "binding_fenced": (409, "conflict"),
    "segment_conflict": (409, "conflict"),
    "manifest_conflict": (409, "conflict"),
    # Completed-recording import (imports.py) codes.
    "conflict": (409, "conflict"),
    "deleted": (409, "deleted"),
    "expired": (409, "expired"),
    # 413 / 416 / 429.
    "too_large": (413, "too_large"),
    "range_unsatisfiable": (416, "not_found"),
    "throttled": (429, "throttled"),
    # Retryable server-side incompleteness.
    "transfer_timeout": (408, "incomplete"),
    "transfer_failed": (503, "incomplete"),
    "metadata_unavailable": (503, "incomplete"),
    "validation_unavailable": (503, "incomplete"),
}

# The public ``source_unavailable`` refusal for imports keeps its own F01 code:
# 403 refinements do not include it, so the import route maps it explicitly.
IMPORT_UNAVAILABLE = (403, "policy_denied")


def public_error(error):
    """Map an AttachmentError to (status, F01 Reason) without leaking details."""
    status, reason = PUBLIC.get(getattr(error, "code", ""), (503, "incomplete"))
    return status, reason


def strict_json(raw):
    """Duplicate members and non-JSON numbers must not hide a conflicting body."""
    if not isinstance(raw, bytes | bytearray) or len(raw) > MAX_MANIFEST_BYTES:
        raise AttachmentError("too_large", 413)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AttachmentError("invalid_request")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise AttachmentError("invalid_request")

    try:
        return json.loads(
            bytes(raw).decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except AttachmentError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AttachmentError("invalid_request") from None


@lru_cache(maxsize=1)
def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=64)
def _validator(name):
    from jsonschema import Draft202012Validator, FormatChecker

    schema = _schema()
    if name not in schema["$defs"]:
        raise KeyError(name)
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{name}",
        },
        format_checker=FormatChecker(),
    )


def is_valid(name, value):
    try:
        validate(name, value)
    except AttachmentError:
        return False
    return True


def validate(name, value):
    """F01 JSON Schema plus the cross-field rules of the contract's oracle."""
    if not _validator(name).is_valid(value):
        raise AttachmentError("invalid_request")
    try:
        _cross_field(name, value)
    except (KeyError, TypeError, ValueError, IndexError):
        raise AttachmentError("invalid_request") from None
    return value


def _modality_matches(value):
    modality, mime = value["modality"], value["mime_type"]
    allowed = {
        "audio": mime.startswith("audio/"),
        "image": mime.startswith("image/"),
        "video": mime.startswith("video/"),
        "document": mime == "application/pdf",
        "file": mime == "application/octet-stream",
    }
    if not allowed[modality]:
        raise ValueError("modality/MIME mismatch")


def _cross_field(name, value):
    if name in {"Reference", "UploadRequest"}:
        _modality_matches(value)
    if name == "UploadRequest":
        if value["modality"] == "image" and value["declared_bytes"] > 20971520:
            raise ValueError("image byte limit")
        if "recording_id" in value and value["declared_bytes"] > 8388608:
            raise ValueError("recording segment byte limit")
    if name == "Occurrence":
        _cross_field("Reference", value["reference"])
    if name == "OccurrencePage":
        for occurrence in value["items"]:
            _cross_field("Occurrence", occurrence)
    if name == "ResolveResponse":
        for item in value["items"]:
            if (
                item["status"] == "ready"
                and item["gateway_path"].split("/")[4] != item["occurrence_id"]
            ):
                raise ValueError("gateway occurrence mismatch")
    if name in {
        "ImportRequest",
        "SingleAttachmentImportRequest",
        "RecordingImportRequest",
    }:
        recording = value.get("mode") == "completed_recording"
        descriptor = value["recording" if recording else "upload"]
        if not recording:
            _cross_field("UploadRequest", descriptor)
        expected = (
            "RemoteUrl"
            if value["source"]["kind"] == "RemoteUrlEnvelope"
            else "ProviderFile"
        )
        if descriptor["source_kind"] != expected:
            raise ValueError("import source mismatch")
    if name == "RecordingManifest":
        _manifest_rules(value)


def _manifest_rules(value):
    if (
        len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        > MAX_MANIFEST_BYTES
    ):
        raise ValueError("manifest byte limit")
    if value["capture_complete"] and value["gaps"]:
        raise ValueError("complete recording has capture gaps")
    track_ids = [track["track_id"] for track in value["tracks"]]
    if len(track_ids) != len(set(track_ids)):
        raise ValueError("duplicate track")
    channels = [channel for track in value["tracks"] for channel in track["channels"]]
    if sorted(channels) != list(range(value["channels"])):
        raise ValueError("channel mapping must cover each channel exactly once")
    if value["layout"] == "mixed" and any(
        track["direction"] != "mixed" for track in value["tracks"]
    ):
        raise ValueError("mixed recording cannot claim isolated directions")
    if value["layout"] == "stereo" and value["channels"] != 2:
        raise ValueError("stereo needs two channels")
    seen = set()
    previous_start = -1
    for index, segment in enumerate(value["segments"]):
        if (
            segment["index"] != index
            or segment["start_ms"] < previous_start
            or segment["occurrence_id"] in seen
        ):
            raise ValueError("unordered or duplicate segment")
        seen.add(segment["occurrence_id"])
        previous_start = segment["start_ms"]
    separate = value["layout"] == "separate_tracks"
    timelines: dict[str | None, list[dict[str, Any]]] = (
        {track: [] for track in track_ids} if separate else {None: []}
    )
    for interval in value["segments"] + value["gaps"]:
        track = interval.get("track_id")
        if track not in timelines:
            raise ValueError("missing or unrelated track identity")
        timelines[track].append(interval)
    for intervals in timelines.values():
        end = 0
        for interval in sorted(intervals, key=lambda item: item["start_ms"]):
            if interval["start_ms"] != end:
                raise ValueError("overlap or unmarked capture gap")
            end += interval["duration_ms"]
        if end != value["duration_ms"]:
            raise ValueError("missing recording coverage")
    for interval in value["segments"] + value["gaps"] + value["playout"]:
        if interval["start_ms"] + interval["duration_ms"] > value["duration_ms"]:
            raise ValueError("interval exceeds recording")


def idempotency_key(value):
    """F01 ``Token``: the scoped replay key the SDK derives per route (0.6)."""
    if not isinstance(value, str) or not _validator("Token").is_valid(value):
        raise AttachmentError("idempotency_key_required")
    return value
