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


def admit_audio(
    scope, policy, *, direction, source_kind, creates_recording=False, error_body=False
):
    policy.validate()
    if not scope.enabled or not policy.enabled:
        raise AttachmentError("disabled", 403)
    if not policy.capture_content:
        raise AttachmentError("policy_denied", 403)
    if direction not in {"input", "output", "mixed"}:
        raise AttachmentError("invalid_direction")
    if source_kind not in {"bytes", "owned_recording_file"}:
        raise AttachmentError("source_unavailable", 403)
    if creates_recording and not policy.record_audio:
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
