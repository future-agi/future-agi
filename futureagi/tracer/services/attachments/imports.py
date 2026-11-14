"""F01 completed-recording import: admission, bounded transfer and publication.

Flow (README "Completed-recording import"):

1. ``submit`` authenticates nothing itself: it receives an already
   authenticated, explicitly eligible ORIGINAL identity (never a write-only
   upload capability), resolves the trusted source facts, applies deny-wins
   policy, and atomically reserves the whole declared source PLUS one output
   window under global, organization and project budgets before any fetch.
2. ``run`` (worker) rechecks fences, reads the exact immutable source version
   through sequential bounded ranges into private worker scratch, probes it in
   the seccomp sandbox, produces independently valid bounded segments in the
   sandbox, validates each segment again, stages it privately and finally
   publishes every segment occurrence and the manifest atomically.
3. Failure, expiry, deletion or tightening fences late work, erases staged
   segments and releases reservations exactly once, and only after every
   staged segment object is erased. A segment row is allocated before its
   object is written, so an erase never misses an object; an erase that fails
   leaves the row ``staged`` and ``sweep`` retries it before releasing.

Durable rows keep only opaque references and F01 Reason codes.
"""

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
    CaptureSession,
)
from tracer.models.attachment_recording import (
    TERMINAL_RECORDING_STATES,
    AttachmentImportJob,
    AttachmentRecording,
    AttachmentRecordingSegment,
    RecordingSegmentState,
    RecordingState,
)
from tracer.services.attachments import envelopes, recordings, sandbox
from tracer.services.attachments.bootstrap import check_session, session_policy
from tracer.services.attachments.contract import (
    AttachmentError,
    admit_audio,
    fingerprint,
    span_id,
    trace_uuid,
)
from tracer.services.attachments.uploads import BUDGETS as UPLOAD_BUDGETS
from tracer.services.attachments.uploads import _lock_budgets

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 3
# One worker owns a job at a time. The lease is renewed at every staged segment
# and checked again under the job row lock before publication, so a worker that
# lost its lease (crash, pause, overlapping tick) can never publish or finish.
LEASE_SECONDS = 15 * 60
IMPORTED_SOURCE_KIND = "imported_recording"
JOB_HOURS = 3
IMPORT_MIME_TYPES = {
    "audio/wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "audio/flac",
    "audio/L16",
}
# Only canonical PCM WAV has a qualified sandboxed segmenter today.
SEGMENTABLE_MIME_TYPES = {"audio/wav"}
# Finite in-flight bounds per (deployment, organization, project); not
# editions. The deployment column is the reference the ``global:imports`` row
# takes its configured share of (``import_budgets``): the upload deployment
# caps where uploads have the same resource (bytes, transfers, validation
# jobs), and an import-only bound for recordings, objects and links.
_UPLOAD_TRANSFERS, _UPLOAD_BYTES, _UPLOAD_VALIDATION_JOBS, _ = UPLOAD_BUDGETS[0]
IMPORT_BUDGETS = {
    "bytes": (_UPLOAD_BYTES, 2 * 1024**3, 1024**3),
    "transfers": (_UPLOAD_TRANSFERS, 32, 16),
    "recordings": (64, 8, 4),
    "validation_jobs": (_UPLOAD_VALIDATION_JOBS, 2048, 1024),
    "objects": (65536, 16384, 8192),
    "links": (65536, 16384, 8192),
}
_ROW_FIELDS = {
    "bytes": "reserved_bytes",
    "transfers": "active_transfers",
    "recordings": "active_recordings",
    "validation_jobs": "validation_jobs",
    "objects": "object_count",
    "links": "link_count",
}
# Each job holds one transfer, one recording, one validation slot, two objects
# (staged source snapshot + one output window) and one link from admission on.
# On top of that floor every staged segment adds its own object and link
# (``SEGMENT_UNITS``, counted in ``AttachmentImportJob.reserved_segments``), so
# a long recording's staged parts are bounded by the object and link budgets.
JOB_UNITS = {
    "transfers": 1,
    "recordings": 1,
    "validation_jobs": 1,
    "objects": 2,
    "links": 1,
}

# Internal codes -> F01 Reason. The HTTP layer must reuse this one table.
PUBLIC_REASONS = {
    "too_large": "too_large",
    "throttled": "throttled",
    "conflict": "conflict",
    "idempotency_conflict": "conflict",
    "not_found": "not_found",
    "disabled": "disabled",
    "policy_denied": "policy_denied",
    "redaction_unavailable": "redaction_unavailable",
    "recording_disabled": "recording_disabled",
    "source_unavailable": "source_unavailable",
    "credential_scope_unsupported": "credential_scope_unsupported",
    "scope_unresolved": "scope_unresolved",
    "expired": "expired",
    "deleted": "deleted",
    "incomplete": "incomplete",
    "validation_failed": "validation_failed",
    "validation_rejected": "validation_failed",
    "validation_unavailable": "validation_failed",
    "validation_fenced": "conflict",
    "unsupported_media": "validation_failed",
    "storage_unverified": "source_unavailable",
    "snapshot_changed": "conflict",
    "length_mismatch": "validation_failed",
    "invalid_trace_id": "correlation_unavailable",
    "invalid_span_id": "correlation_unavailable",
    "invalid_direction": "validation_failed",
    "invalid_policy": "policy_denied",
    "capability_expired": "expired",
    "permission_denied": "credential_scope_unsupported",
}


# F01 ``Reason`` values a stored job may carry as-is ("queued" while waiting).
STATUS_REASONS = frozenset(PUBLIC_REASONS.values()) | {"queued", "ready"}


SEGMENT_UNITS = {"objects": 1, "links": 1}
# ``uploads._budget_keys`` names it ``global:imports``; see ``_lock_import_budgets``.
IMPORT_SHARD = "imports"


def public_reason(code):
    return PUBLIC_REASONS.get(code, "validation_failed")


def import_budgets():
    """Caps per resource for (``global:imports``, organization, project).

    Read at call time. The organization and project caps are the per-scope
    caps. The ``global:imports`` row is kept apart from the upload shards, so
    a long import never throttles the uploads hashed to a shard, and it holds
    only ``FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT`` (default 50) of the
    deployment column. A value outside 1-100 fails closed: every import is
    throttled and nothing is reserved.
    """
    percent = getattr(settings, "FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT", 50)
    if type(percent) is not int or not 1 <= percent <= 100:
        raise AttachmentError("throttled", 429)
    return {
        resource: (max(1, deployment * percent // 100), organization, project)
        for resource, (deployment, organization, project) in IMPORT_BUDGETS.items()
    }


def lifetime_need(*, declared_bytes, window, segment_bytes, segments):
    """What one import holds at its peak, right before publication.

    The source and one window from admission, plus the bytes, one object and
    one link of every staged segment (``JOB_UNITS`` + ``SEGMENT_UNITS``).
    """
    return {
        "bytes": declared_bytes + window + segment_bytes,
        "objects": JOB_UNITS["objects"] + segments * SEGMENT_UNITS["objects"],
        "links": JOB_UNITS["links"] + segments * SEGMENT_UNITS["links"],
    }


def check_import_share(need):
    """Refuse ``too_large`` an import that could never finish, whatever the load.

    The share is the smallest cap of the three rows it is charged to. An
    import whose peak exceeds it would otherwise be admitted, then throttled
    at a staged segment on every attempt and refetch the whole source each
    time. A shortfall of HEADROOM (other jobs in flight) stays ``throttled``.
    """
    caps = import_budgets()
    for resource, amount in need.items():
        if amount > min(caps[resource]):
            raise AttachmentError("too_large", 413)


def server_limits():
    """Server-owned negotiated limits. Never taken from the request."""
    value = getattr(settings, "FI_ATTACHMENTS_RECORDING_LIMITS", None) or {}
    return recordings.Limits(
        max_upload_bytes=value.get("max_upload_bytes", 8 * 1024 * 1024),
        segment_bytes=value.get("segment_bytes", 8 * 1024 * 1024),
        segment_duration_ms=value.get("segment_duration_ms", 30000),
        recording_bytes=value.get("recording_bytes", 1024**3),
        recording_duration_ms=value.get("recording_duration_ms", 7_200_000),
        reserved_bytes=value.get("reserved_bytes", 1024**3),
    )


def policy_token(scope):
    """The F01 ``Policy.version`` token bootstrap issues for this scope."""
    return str(scope.policy_version)


@dataclass(frozen=True)
class Importer:
    """An already authenticated ORIGINAL identity (user or eligible API key)."""

    kind: str
    id: uuid.UUID


@dataclass(frozen=True)
class SourceFacts:
    """Trusted resolver observations; never client assertions."""

    terminal_completed: bool
    immutable: bool
    bounded_ranges: bool
    total_bytes: int
    direction: str


# --------------------------------------------------------------------------
# Pure admission/completion rules shared with the F01 conformance fixtures.
# --------------------------------------------------------------------------


def initial_reservation(declared_bytes, limits):
    return declared_bytes + limits.window


def admission_check(*, recording, limits, headroom, source):
    """Raise the F01 reason for the first failing admission rule."""
    if (
        recording["declared_bytes"] > limits.recording_bytes
        or recording["declared_duration_ms"] > limits.recording_duration_ms
    ):
        raise AttachmentError("too_large", 413)
    if not (source.terminal_completed and source.immutable and source.bounded_ranges):
        raise AttachmentError("source_unavailable", 403)
    if source.total_bytes != recording["declared_bytes"]:
        raise AttachmentError("validation_failed", 422)
    if recording["direction"] != "mixed" and source.direction != recording["direction"]:
        # A client label can never understate trusted source ancestry.
        raise AttachmentError("policy_denied", 403)
    reservation = initial_reservation(recording["declared_bytes"], limits)
    if reservation > limits.reserved_bytes:
        raise AttachmentError("too_large", 413)
    required = {"bytes": reservation, **JOB_UNITS}
    if set(headroom) != {"project", "organization", "global"}:
        raise AttachmentError("throttled", 429)
    for available in headroom.values():
        if set(available) != set(required):
            raise AttachmentError("throttled", 429)
        for resource, minimum in required.items():
            if type(available[resource]) is not int or available[resource] < minimum:
                raise AttachmentError("throttled", 429)
    return reservation


def completion_check(*, recording, limits, observation):
    """Verify worker observations before anything is published."""
    if (
        observation["source_bytes"] != recording["declared_bytes"]
        or observation["source_bytes"] > limits.recording_bytes
        or observation["source_duration_ms"] != recording["declared_duration_ms"]
        or observation["source_duration_ms"] > limits.recording_duration_ms
        or observation["generation_matches"] is not True
    ):
        raise AttachmentError("validation_failed", 422)
    end = 0
    for part in observation["reads"]:
        if (
            part["offset"] != end
            or type(part["bytes"]) is not int
            or not 0 < part["bytes"] <= limits.window
            or part["total_bytes"] != recording["declared_bytes"]
        ):
            raise AttachmentError("validation_failed", 422)
        end += part["bytes"]
    if end != recording["declared_bytes"]:
        raise AttachmentError("validation_failed", 422)
    manifest = recordings.validate_manifest(observation["manifest"])
    if (
        manifest["recording_id"] != recording["recording_id"]
        or manifest["duration_ms"] != recording["declared_duration_ms"]
        or not manifest["capture_complete"]
        or not manifest["segments"]
    ):
        raise AttachmentError("validation_failed", 422)
    if recording["direction"] != "mixed" and any(
        track["direction"] != recording["direction"] for track in manifest["tracks"]
    ):
        raise AttachmentError("policy_denied", 403)
    sizes = observation["segment_bytes"]
    if len(sizes) != len(manifest["segments"]) or any(
        type(size) is not int or not 0 < size <= limits.window for size in sizes
    ):
        raise AttachmentError("validation_failed", 422)
    if sum(sizes) > limits.recording_bytes:
        raise AttachmentError("too_large", 413)
    if any(
        segment["duration_ms"] > limits.segment_duration_ms
        for segment in manifest["segments"]
    ):
        raise AttachmentError("validation_failed", 422)
    return manifest


# --------------------------------------------------------------------------
# Request parsing (strict, no unknown members), source resolution, admission.
# --------------------------------------------------------------------------

_RECORDING_KEYS = {
    "schema_version",
    "association",
    "policy_version",
    "deletion_generation",
    "mime_type",
    "direction",
    "source_kind",
    "recording_id",
    "modality",
    "declared_bytes",
    "declared_duration_ms",
}


def _canonical_uuid(value):
    if not isinstance(value, str):
        raise ValueError
    return uuid.UUID(value)


def parse_request(body):
    """Normalize a RecordingImportRequest. Raises validation_failed only."""
    try:
        if set(body) != {"mode", "recording", "source"} or body["mode"] != (
            "completed_recording"
        ):
            raise ValueError
        recording, source = body["recording"], body["source"]
        if set(recording) != _RECORDING_KEYS or recording["schema_version"] != 1:
            raise ValueError
        association = recording["association"]
        if set(association) != {"capture_session_id", "correlation_nonce", "parent"}:
            raise ValueError
        parent = association["parent"]
        if set(parent) != {"trace_id", "span_id"}:
            raise ValueError
        if (
            recording["modality"] != "audio"
            or recording["mime_type"] not in IMPORT_MIME_TYPES
            or recording["direction"] not in {"input", "output", "mixed"}
            or type(recording["declared_bytes"]) is not int
            or not 1 <= recording["declared_bytes"] <= 1024**3
            or type(recording["declared_duration_ms"]) is not int
            or not 1 <= recording["declared_duration_ms"] <= 7_200_000
            or type(recording["deletion_generation"]) is not int
            or recording["deletion_generation"] < 0
            or not isinstance(recording["policy_version"], str)
        ):
            raise ValueError
        if source.get("kind") == "RemoteUrlEnvelope":
            if set(source) != {"kind", "envelope_id"}:
                raise ValueError
            expected_kind = "RemoteUrl"
            reference = {"envelope_id": _canonical_uuid(source["envelope_id"])}
        elif source.get("kind") == "ProviderFile":
            if set(source) != {"kind", "connection_id", "registered_output_id"}:
                raise ValueError
            expected_kind = "ProviderFile"
            reference = {
                "connection_id": _canonical_uuid(source["connection_id"]),
                "registered_output_id": _canonical_uuid(source["registered_output_id"]),
            }
        else:
            raise ValueError
        if recording["source_kind"] != expected_kind:
            raise ValueError
        normalized = {
            **{k: recording[k] for k in _RECORDING_KEYS - {"association"}},
            "recording_id": str(_canonical_uuid(recording["recording_id"])),
            "capture_session_id": _canonical_uuid(association["capture_session_id"]),
            "correlation_nonce": _canonical_uuid(association["correlation_nonce"]),
            "trace_id": trace_uuid(parent["trace_id"]),
            "span_id": span_id(parent["span_id"]),
        }
        return normalized, {"kind": source["kind"], **reference}
    except AttachmentError:
        raise AttachmentError("validation_failed", 422) from None
    except (TypeError, ValueError, KeyError, AttributeError):
        raise AttachmentError("validation_failed", 422) from None


def _resolve_source(importer, scope, source):
    """Return (SourceFacts, envelope_id, provider_recording)."""
    if source["kind"] == "RemoteUrlEnvelope":
        row = envelopes.describe(scope_id=scope.id, envelope_id=source["envelope_id"])
        if (row.creator_kind, row.creator_id) != (importer.kind, importer.id):
            raise AttachmentError("source_unavailable", 403)
        facts = SourceFacts(
            terminal_completed=True,
            immutable=True,
            bounded_ranges=True,
            total_bytes=row.source_bytes,
            direction=row.direction,
        )
        return facts, row.id, None
    from tracer.services.attachments import provider_recordings

    registration = provider_recordings.completed_output(
        importer,
        scope,
        connection_id=source["connection_id"],
        registered_output_id=source["registered_output_id"],
    )
    facts = SourceFacts(
        terminal_completed=True,
        immutable=True,
        bounded_ranges=provider_recordings.reader_qualified(registration),
        total_bytes=registration.output_bytes,
        direction=registration.direction,
    )
    return facts, None, registration


def _lock_import_budgets(scope):
    """Lock the deployment import row, then the organization and project rows.

    Imports do not use the upload shards (``global:0..15``). A shard holds
    1/16 of the deployment caps (512 MiB), less than one valid import (up to
    1 GiB + one window, then its staged segments), and an import parked on a
    shard for hours would also starve every upload hashed to it. Imports are
    rare, so one ``global:imports`` row does not contend; it holds a
    configurable share of the deployment caps (``import_budgets``, default
    half). Lock order is unchanged: a ``global:*`` row, organization,
    project, scope; retention takes ``global:0..15`` and never this row.
    """
    return _lock_budgets(scope, IMPORT_SHARD)


def _headroom(rows):
    """Headroom of (deployment import row, organization, project), in order."""
    caps = import_budgets()
    result = {}
    for index, (name, row) in enumerate(
        zip(("global", "organization", "project"), rows, strict=True)
    ):
        result[name] = {
            resource: caps[resource][index] - getattr(row, field)
            for resource, field in _ROW_FIELDS.items()
        }
    return result


def _apply(rows, amounts, sign):
    for row in rows:
        for resource, amount in amounts.items():
            field = _ROW_FIELDS[resource]
            setattr(row, field, getattr(row, field) + sign * amount)
            if getattr(row, field) < 0:
                raise AttachmentError("conflict", 409)
        row.save(update_fields=[_ROW_FIELDS[name] for name in amounts])


def _session(scope, importer, recording):
    """The importer's own capture session named by the request ``association``.

    A capture session has no parent of its own: the recording's parent comes
    from the request, exactly as every upload names its own parent.
    """
    session = CaptureSession.objects.filter(
        scope=scope,
        client_id=recording["capture_session_id"],
        correlation_nonce=recording["correlation_nonce"],
        writer_kind=importer.kind,
        writer_id=importer.id,
    ).first()
    if session is None:
        raise AttachmentError("not_found", 404)
    check_session(session)
    return session


def _admit_policy(scope, session, direction):
    policy = session_policy(session)
    # Both the session's snapshot and the LIVE scope switch must allow it, so
    # turning ``allow_import`` off takes effect at once (deny wins).
    if not scope.allow_import or not getattr(policy, "allow_import", False):
        # Import needs its own explicit source permission; recording opt-in
        # (record_audio) is a different switch and never implies it.
        raise AttachmentError("source_unavailable", 403)
    admit_audio(scope, policy, direction=direction, source_kind=IMPORTED_SOURCE_KIND)


def submit(importer, *, scope_id, body):
    """Admit one completed recording import; returns RecordingImportResponse."""
    recording, source = parse_request(body)
    scope = AttachmentScope.objects.get(pk=scope_id)
    if not scope.allow_import:
        # Uniform refusal before any session, envelope or provider lookup.
        raise AttachmentError("source_unavailable", 403)
    session = _session(scope, importer, recording)
    digest = fingerprint(
        {"importer": [importer.kind, importer.id], "recording": recording, **source}
    )
    # Identical scoped replay returns the same job, even after its one-shot
    # source envelope was consumed; a changed request conflicts.
    replay = _replay(scope, recording, digest)
    if replay is not None:
        return _response(replay), replay
    limits = server_limits()
    facts, envelope_id, registration = _resolve_source(importer, scope, source)
    direction = (
        "mixed"
        if "mixed" in {facts.direction, recording["direction"]}
        else (facts.direction)
    )
    # Deny-wins privacy on the trusted ancestry, before any byte is read.
    _admit_policy(scope, session, direction)
    if recording["policy_version"] != policy_token(scope) or (
        recording["deletion_generation"] != scope.deletion_generation
    ):
        raise AttachmentError("conflict", 409)
    if recording["mime_type"] not in SEGMENTABLE_MIME_TYPES:
        raise AttachmentError("validation_failed", 422)
    # Canonical WAV only (44-byte header): its segments carry all of the
    # source's samples plus a 44-byte header each, so they total at least the
    # declared size. Refuse before any reservation or fetch an import whose
    # peak cannot fit; the worker repeats this exactly once it has the plan.
    check_import_share(
        lifetime_need(
            declared_bytes=recording["declared_bytes"],
            window=limits.window,
            segment_bytes=recording["declared_bytes"],
            segments=1,
        )
    )
    with transaction.atomic():
        rows = _lock_import_budgets(scope)
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        replay = _replay(scope, recording, digest)
        if replay is not None:
            return _response(replay), replay
        reservation = admission_check(
            recording=recording,
            limits=limits,
            headroom=_headroom(rows),
            source=facts,
        )
        _apply(rows, {"bytes": reservation, **JOB_UNITS}, +1)
        record = AttachmentRecording.objects.create(
            scope=scope,
            client_id=recording["recording_id"],
            capture_session=session,
            trace_id=recording["trace_id"],
            span_id=recording["span_id"],
            capture_session_client_id=recording["capture_session_id"],
            correlation_nonce=recording["correlation_nonce"],
            direction=direction,
            # The shared recording row defaults to a streamed upload's state.
            state=RecordingState.IMPORTING,
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
        )
        deadline = timezone.now() + timedelta(hours=JOB_HOURS)
        if registration is not None:
            deadline = min(deadline, registration.expires_at)
        job = AttachmentImportJob.objects.create(
            scope=scope,
            recording=record,
            importer_kind=importer.kind,
            importer_id=importer.id,
            request_fingerprint=digest,
            source_kind=source["kind"],
            envelope_id=envelope_id,
            provider_recording=registration,
            mime_type=recording["mime_type"],
            declared_bytes=recording["declared_bytes"],
            declared_duration_ms=recording["declared_duration_ms"],
            window_bytes=limits.window,
            max_segment_ms=limits.segment_duration_ms,
            recording_bytes=limits.recording_bytes,
            deadline=deadline,
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
            reserved_bytes=reservation,
        )
    return _response(job), job


def _replay(scope, recording, digest):
    existing = AttachmentRecording.objects.filter(
        scope=scope, client_id=recording["recording_id"]
    ).first()
    if existing is None:
        return None
    job = AttachmentImportJob.objects.filter(scope=scope, recording=existing).first()
    if job is None or job.request_fingerprint != digest:
        raise AttachmentError("conflict", 409)
    return job


def _response(job):
    return {
        "mode": "completed_recording",
        "job_id": str(job.id),
        "recording_id": str(job.recording.client_id),
        "state": "importing",
    }


def status(importer, *, scope_id, job_id):
    """Content-free status, only for the original scoped importer."""
    job = AttachmentImportJob.objects.filter(
        pk=job_id,
        scope_id=scope_id,
        importer_kind=importer.kind,
        importer_id=importer.id,
    ).first()
    if job is None:
        raise AttachmentError("not_found", 404)
    reason = job.reason if job.reason in STATUS_REASONS else public_reason(job.reason)
    return {"job_id": str(job.id), "state": job.state, "reason": reason}


# --------------------------------------------------------------------------
# Worker.
# --------------------------------------------------------------------------


class _Retry(Exception):
    def __init__(self, code):
        self.code = code


class _LeaseLost(Exception):
    """Another worker claimed this job; stop without touching shared state."""


def _claim(job):
    """Take the job's lease. Returns (token, None) or (None, why)."""
    with transaction.atomic():
        locked = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        now = timezone.now()
        if locked.state in TERMINAL_RECORDING_STATES:
            return None, "terminal"
        if locked.lease_until is not None and locked.lease_until > now:
            return None, "busy"
        if locked.attempts >= MAX_ATTEMPTS:
            return None, "exhausted"
        token = uuid.uuid4()
        locked.attempts += 1
        locked.lease_token = token
        locked.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        locked.save(update_fields=["attempts", "lease_token", "lease_until"])
        return token, None


def _hold(locked, token):
    """Under the job row lock: fail unless ``token`` still owns the lease."""
    if token is not None and locked.lease_token != token:
        raise _LeaseLost()
    locked.lease_until = timezone.now() + timedelta(seconds=LEASE_SECONDS)


def _fence(job):
    """Fail closed on deletion, expiry, policy change or a revoked session."""
    scope = AttachmentScope.objects.get(pk=job.scope_id)
    recording = AttachmentRecording.objects.get(pk=job.recording_id)
    if recording.deleted_at or job.deletion_generation != scope.deletion_generation:
        raise AttachmentError("deleted", 410)
    if timezone.now() >= job.deadline:
        raise AttachmentError("expired", 410)
    if job.policy_version != scope.policy_version:
        raise AttachmentError("policy_denied", 403)
    session = CaptureSession.objects.get(pk=recording.capture_session_id)
    check_session(session)
    _admit_policy(scope, session, recording.direction)
    return scope, recording


def _reserve_segment(job, size, token=None):
    """Cover a staged segment before producing it: its bytes, object and link."""
    with transaction.atomic():
        scope = AttachmentScope.objects.get(pk=job.scope_id)
        rows = _lock_import_budgets(scope)
        locked = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        _hold(locked, token)
        if locked.state in TERMINAL_RECORDING_STATES or locked.released_at is not None:
            raise AttachmentError("conflict", 409)
        amounts = {"bytes": size, **SEGMENT_UNITS}
        headroom = _headroom(rows)
        if any(
            level[resource] < amount
            for level in headroom.values()
            for resource, amount in amounts.items()
        ):
            raise _Retry("throttled")
        _apply(rows, amounts, +1)
        locked.reserved_bytes += size
        locked.reserved_segments += 1
        locked.save(
            update_fields=["reserved_bytes", "reserved_segments", "lease_until"]
        )
        job.reserved_bytes = locked.reserved_bytes
        job.reserved_segments = locked.reserved_segments


def _release(job):
    """Release the job's whole live reservation exactly once."""
    with transaction.atomic():
        scope = AttachmentScope.objects.get(pk=job.scope_id)
        rows = _lock_import_budgets(scope)
        locked = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        if locked.released_at is not None:
            return False
        units = {
            resource: JOB_UNITS.get(resource, 0)
            + SEGMENT_UNITS.get(resource, 0) * locked.reserved_segments
            for resource in {*JOB_UNITS, *SEGMENT_UNITS}
        }
        _apply(rows, {"bytes": locked.reserved_bytes, **units}, -1)
        locked.released_at = timezone.now()
        locked.save(update_fields=["released_at"])
        return True


def _erase_staged(job, storage):
    """Erase every staged segment object of a finished job; True when none is left.

    A row without a ``storage_version`` was allocated but its object may still
    be in flight. While the job's lease is live its writer may still be
    uploading it, so the row is kept (and the reservation with it) until the
    writer stops or its lease runs out. Erasing an id that has no object is a
    no-op. A failed erase leaves the row ``staged`` for the next sweep. The
    row is marked ``erased`` only if it is unchanged since it was read, so an
    object version recorded in between is never lost.
    """
    now = timezone.now()
    live = job.lease_until is not None and job.lease_until > now
    left = 0
    for segment in AttachmentRecordingSegment.objects.filter(
        recording_id=job.recording_id, state=RecordingSegmentState.STAGED
    ).order_by("index"):
        if live and not segment.storage_version:
            left += 1
            continue
        try:
            storage.erase_upload(segment.attachment_client_id)
        except AttachmentError as error:
            logger.warning(
                "attachment_import_erase_deferred",
                job_id=str(job.pk),
                reason=error.code,
            )
            left += 1
            continue
        erased = AttachmentRecordingSegment.objects.filter(
            pk=segment.pk,
            state=RecordingSegmentState.STAGED,
            storage_version=segment.storage_version,
        ).update(state=RecordingSegmentState.ERASED)
        left += not erased
    return left == 0


def _cleanup(job, storage):
    """Erase a finished job's staged objects, then release its reservation once.

    The reservation is never released while a staged object may remain. Only
    erase failures are absorbed here (the row stays ``staged``); ``sweep``
    retries both steps. Returns True when this call released the reservation.
    """
    locked = AttachmentImportJob.objects.get(pk=job.pk)
    if locked.state not in TERMINAL_RECORDING_STATES:
        return False
    if locked.state != RecordingState.READY and not _erase_staged(locked, storage):
        return False
    return _release(job)


def _drop_lease(job, token):
    """The lease owner stops writing: its unpromoted rows become erasable."""
    if token is not None:
        AttachmentImportJob.objects.filter(pk=job.pk, lease_token=token).update(
            lease_until=None
        )


def _finish(job, *, state, reason, storage, token=None):
    """Terminal transition. A worker passes its lease token; the sweep doesn't.

    Only the lease owner clears the lease. When the sweep expires a job, a live
    lease is the signal that a worker may still be writing a segment object,
    so ``_erase_staged`` keeps that row (and the reservation) until it stops.
    """
    with transaction.atomic():
        locked = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        already = locked.state in TERMINAL_RECORDING_STATES
        if not already:
            if token is not None and locked.lease_token != token:
                return locked
            locked.state, locked.reason = state, reason
            locked.finished_at = timezone.now()
            fields = ["state", "reason", "finished_at"]
            if token is not None:
                locked.lease_until = None
                fields.append("lease_until")
            locked.save(update_fields=fields)
            if state != RecordingState.READY:
                AttachmentRecording.objects.filter(pk=job.recording_id).exclude(
                    state__in=TERMINAL_RECORDING_STATES
                ).update(state=state)
    if already:
        # Finished elsewhere (published, swept): erase, then release.
        _drop_lease(job, token)
        _cleanup(job, storage)
        return locked
    _cleanup(job, storage)
    if job.envelope_id:
        envelopes.destroy(scope_id=job.scope_id, envelope_id=job.envelope_id)
    return locked


def _open_source(job, limits):
    from tracer.services.attachments import provider_recordings, safe_fetch

    if job.source_kind == "RemoteUrlEnvelope":
        url = envelopes.open_url(scope_id=job.scope_id, envelope_id=job.envelope_id)
        destination = safe_fetch.parse_destination(
            url,
            approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ()),
        )
        del url
        return safe_fetch.RangeSource(
            destination=destination,
            total_bytes=job.declared_bytes,
            window=limits.window,
            timeout_seconds=getattr(settings, "FI_ATTACHMENTS_TRANSFER_SECONDS", 120),
            bytes_per_second=getattr(
                settings, "FI_ATTACHMENTS_IMPORT_BYTES_PER_SECOND", 8 * 1024 * 1024
            ),
        )
    return provider_recordings.open_reader(job.provider_recording, limits)


def _work_dir():
    path = getattr(settings, "FI_ATTACHMENTS_WORK_DIR", "") or tempfile.gettempdir()
    return path


def _fetch(job, source, snapshot, limits):
    reads, offset = [], 0
    while offset < job.declared_bytes:
        length = min(limits.window, job.declared_bytes - offset)
        source.read_range(offset, length, snapshot)
        reads.append(
            {"offset": offset, "bytes": length, "total_bytes": job.declared_bytes}
        )
        offset += length
    snapshot.flush()
    if os.fstat(snapshot.fileno()).st_size != job.declared_bytes:
        raise AttachmentError("validation_failed", 422)
    return reads


def _segments(job, recording, snapshot_path, facts, limits, storage, token=None):
    plans = recordings.plan_segments(
        frame_count=facts["frame_count"],
        rate=facts["sample_rate"],
        channels=facts["channels"],
        limits=limits,
    )
    # The exact peak, before the first segment is reserved: a plan that can
    # never fit is rejected ``too_large`` now, not throttled on every attempt.
    check_import_share(
        lifetime_need(
            declared_bytes=job.declared_bytes,
            window=job.window_bytes,
            segment_bytes=sum(plan.byte_length for plan in plans),
            segments=len(plans),
        )
    )
    staged = {
        row.index: row
        for row in AttachmentRecordingSegment.objects.filter(recording=recording)
    }
    for plan in plans:
        existing = staged.get(plan.index)
        if existing is not None and existing.state != RecordingSegmentState.STAGED:
            raise AttachmentError("conflict", 409)
        if existing is not None and existing.storage_version:
            continue  # Retries reuse allocations; no new occurrence identity.
        _fence(job)
        if existing is None:
            _reserve_segment(job, plan.byte_length, token)
        else:
            # An earlier attempt allocated this part (its reservation still
            # stands) but died before recording the object's version. Erase
            # whatever it may have written, then write it again under the
            # same identity (``promote`` never overwrites an existing key).
            storage.erase_upload(existing.attachment_client_id)
        with tempfile.NamedTemporaryFile(dir=_work_dir()) as part:
            result = sandbox.run(
                "segment",
                snapshot_path,
                plan.start_frame,
                plan.frame_count,
                output=part,
                output_limit=plan.byte_length,
            )
            part.flush()
            if (
                result.get("byte_length") != plan.byte_length
                or os.fstat(part.fileno()).st_size != plan.byte_length
            ):
                raise AttachmentError("validation_failed", 422)
            # Independent validation of the produced bytes, not the producer.
            probed = sandbox.probe(part.name)
            if (
                probed["sample_rate"] != facts["sample_rate"]
                or probed["channels"] != facts["channels"]
                or probed["frame_count"] != plan.frame_count
            ):
                raise AttachmentError("validation_failed", 422)
            part.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: part.read(256 * 1024), b""):
                digest.update(chunk)
            row = _allocate(job, recording, plan, digest.hexdigest(), existing, token)
            identity = storage.promote(
                part,
                upload_id=row.attachment_client_id,
                expected_length=plan.byte_length,
                expected_sha256=digest.hexdigest(),
            )
        staged[plan.index] = _record_object(job, row, identity, storage)
    return plans, [staged[plan.index] for plan in plans]


def _allocate(job, recording, plan, sha256, existing, token):
    """Name a segment's object in a durable ``staged`` row BEFORE writing it.

    Under the job row lock, only while this worker holds the lease and the job
    is still importing, so no row (and no object) can appear after the job
    finished. The row has no ``storage_version`` until ``_record_object``.
    """
    with transaction.atomic():
        locked = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        _hold(locked, token)
        if locked.state in TERMINAL_RECORDING_STATES or locked.released_at is not None:
            raise AttachmentError("conflict", 409)
        locked.save(update_fields=["lease_until"])
        if existing is not None:
            existing.sha256 = sha256
            existing.save(update_fields=["sha256"])
            return existing
        return AttachmentRecordingSegment.objects.create(
            scope_id=job.scope_id,
            recording=recording,
            index=plan.index,
            attachment_client_id=uuid.uuid4(),
            occurrence_client_id=uuid.uuid4(),
            start_frame=plan.start_frame,
            frame_count=plan.frame_count,
            start_ms=plan.start_ms,
            duration_ms=plan.duration_ms,
            storage_key="",
            storage_version="",
            sha256=sha256,
            byte_length=plan.byte_length,
        )


def _record_object(job, row, identity, storage):
    """Record the written object's exact version on its still-staged row.

    If cleanup already erased the row (this worker lost its lease and the job
    finished), the object it just wrote has no owner: erase it here. If that
    erase fails, put the row back to ``staged`` with the version so the sweep
    erases it (it looks for staged rows of finished jobs, released or not).
    """
    recorded = AttachmentRecordingSegment.objects.filter(
        pk=row.pk, state=RecordingSegmentState.STAGED, storage_version=""
    ).update(storage_key=identity.key, storage_version=identity.version)
    if not recorded:
        try:
            storage.erase_upload(row.attachment_client_id)
        except AttachmentError:
            AttachmentRecordingSegment.objects.filter(pk=row.pk).update(
                state=RecordingSegmentState.STAGED,
                storage_key=identity.key,
                storage_version=identity.version,
            )
        raise _LeaseLost()
    row.storage_key, row.storage_version = identity.key, identity.version
    return row


def _publish(job, recording, facts, manifest, rows, token=None):
    """Atomically create segment occurrences and the manifest under fences."""
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=job.scope_id)
        # Lock order everywhere: scope, job, recording (``_finish``: job, recording).
        held = AttachmentImportJob.objects.select_for_update().get(pk=job.pk)
        _hold(held, token)
        locked = AttachmentRecording.objects.select_for_update().get(pk=recording.pk)
        if locked.state in TERMINAL_RECORDING_STATES:
            raise AttachmentError("conflict", 409)
        job.refresh_from_db()
        _fence(job)
        now = timezone.now()
        common = {
            "scope": scope,
            "policy_version": scope.policy_version,
            "deletion_generation": scope.deletion_generation,
        }
        session = CaptureSession.objects.get(pk=locked.capture_session_id)
        for row in rows:
            attachment = Attachment.objects.create(
                **common,
                client_id=row.attachment_client_id,
                # The read path re-admits with this value (occurrences.py).
                source_kind=IMPORTED_SOURCE_KIND,
            )
            Attachment.objects.filter(pk=attachment.pk).update(
                state=AttachmentState.VALIDATING
            )
            AttachmentBlob.objects.create(
                scope=scope,
                attachment=attachment,
                storage_key=row.storage_key,
                storage_version=row.storage_version,
                sha256=row.sha256,
                byte_length=row.byte_length,
                mime_type="audio/wav",
                validated_at=now,
                sample_rate=facts["sample_rate"],
                channels=facts["channels"],
                frame_count=row.frame_count,
            )
            Attachment.objects.filter(pk=attachment.pk).update(
                state=AttachmentState.READY, completed_at=now
            )
            AttachmentOccurrence.objects.create(
                **common,
                client_id=row.occurrence_client_id,
                attachment=attachment,
                capture_session=session,
                trace_id=locked.trace_id,
                span_id=locked.span_id,
                direction=locked.direction,
                recording=locked,
                purpose="conversation_recording",
                # Unbound deadline: ingestion must observe the parent within
                # 24 h; bind_retention then replaces it with the source's expiry.
                expires_at=now + timedelta(hours=24),
                capture_complete=True,
            )
        AttachmentRecordingSegment.objects.filter(
            pk__in=[row.pk for row in rows]
        ).update(state=RecordingSegmentState.PUBLISHED)
        locked.state = RecordingState.READY
        locked.manifest = manifest
        locked.sample_rate = facts["sample_rate"]
        locked.channels = facts["channels"]
        locked.duration_ms = manifest["duration_ms"]
        locked.capture_complete = manifest["capture_complete"]
        locked.published_at = now
        locked.save(
            update_fields=[
                "state",
                "manifest",
                "sample_rate",
                "channels",
                "duration_ms",
                "capture_complete",
                "published_at",
            ]
        )
        AttachmentImportJob.objects.filter(pk=job.pk).update(
            state=RecordingState.READY,
            reason="ready",
            finished_at=now,
            lease_until=None,
        )


def run(job_id, *, scope_id, storage, source_factory=None):
    """Advance one job. Safe to call repeatedly; terminal jobs are no-ops."""
    job = AttachmentImportJob.objects.select_related("recording").get(
        pk=job_id, scope_id=scope_id
    )
    if job.state in TERMINAL_RECORDING_STATES:
        return job.state
    limits = recordings.Limits(
        max_upload_bytes=job.window_bytes,
        segment_bytes=job.window_bytes,
        segment_duration_ms=job.max_segment_ms,
        recording_bytes=job.recording_bytes,
        recording_duration_ms=7_200_000,
        reserved_bytes=job.reserved_bytes,
    )
    token, why = _claim(job)
    if why == "terminal":
        job.refresh_from_db()
        return job.state
    if why == "busy":
        return RecordingState.IMPORTING
    if why == "exhausted":
        return _finish(
            job,
            state=RecordingState.FAILED,
            reason="source_unavailable",
            storage=storage,
        ).state
    try:
        _, recording = _fence(job)
        source = (source_factory or _open_source)(job, limits)
        with tempfile.NamedTemporaryFile(dir=_work_dir()) as snapshot:
            reads = _fetch(job, source, snapshot, limits)
            facts = sandbox.run("source", snapshot.name)
            if (
                facts.get("mime_type") != "audio/wav"
                or facts.get("duration_ms") != job.declared_duration_ms
            ):
                # Reject a duration mismatch before producing any segment.
                raise AttachmentError("validation_failed", 422)
            plans, rows = _segments(
                job, recording, snapshot.name, facts, limits, storage, token
            )
        track_ids = [
            uuid.uuid5(recording.client_id, f"track:{index}")
            for index in range(facts["channels"])
        ]
        manifest = recordings.build_manifest(
            recording_id=recording.client_id,
            rate=facts["sample_rate"],
            channels=facts["channels"],
            direction=recording.direction,
            track_ids=track_ids,
            segments=[
                (row.occurrence_client_id, plan)
                for row, plan in zip(rows, plans, strict=True)
            ],
            duration_ms=facts["duration_ms"],
            capture_complete=True,
        )
        completion_check(
            recording={
                "recording_id": str(recording.client_id),
                "declared_bytes": job.declared_bytes,
                "declared_duration_ms": job.declared_duration_ms,
                "direction": recording.direction,
            },
            limits=limits,
            observation={
                "source_bytes": sum(part["bytes"] for part in reads),
                "source_duration_ms": facts["duration_ms"],
                "generation_matches": True,
                "reads": reads,
                "manifest": manifest,
                "segment_bytes": [row.byte_length for row in rows],
            },
        )
        _publish(job, recording, facts, manifest, rows, token)
        _release(job)
        if job.envelope_id:
            envelopes.destroy(scope_id=job.scope_id, envelope_id=job.envelope_id)
        return RecordingState.READY
    except _LeaseLost:
        return RecordingState.IMPORTING
    except _Retry as retry:
        AttachmentImportJob.objects.filter(
            pk=job.pk, lease_token=token, state=RecordingState.IMPORTING
        ).update(reason=retry.code, lease_until=None)
        return _settled(job, storage, token)
    except AttachmentError as error:
        transient = error.status in {429, 503} and error.code in {
            "source_unavailable",
            "throttled",
            "validation_unavailable",
        }
        job.refresh_from_db()
        if job.state in TERMINAL_RECORDING_STATES:
            return _settled(job, storage, token)
        if transient and job.attempts < MAX_ATTEMPTS:
            AttachmentImportJob.objects.filter(
                pk=job.pk, lease_token=token, state=RecordingState.IMPORTING
            ).update(reason=public_reason(error.code), lease_until=None)
            return _settled(job, storage, token)
        state = {
            "expired": RecordingState.EXPIRED,
            "deleted": RecordingState.DELETED,
        }.get(
            error.code,
            RecordingState.FAILED if transient else RecordingState.REJECTED,
        )
        return _finish(
            job,
            state=state,
            reason=public_reason(error.code),
            storage=storage,
            token=token,
        ).state
    except Exception as error:  # noqa: BLE001 - job boundary; fails closed, retried up to MAX_ATTEMPTS
        # Never persist or log exception text (it may contain a URL or a path).
        logger.warning(
            "attachment_import_attempt_failed",
            job_id=str(job.pk),
            error=type(error).__name__,
        )
        job.refresh_from_db()
        if job.state in TERMINAL_RECORDING_STATES:
            # Failed after publication (the release, the envelope destroy):
            # the job is READY; never relabel it, and retry the release.
            return _settled(job, storage, token)
        if job.attempts < MAX_ATTEMPTS:
            AttachmentImportJob.objects.filter(
                pk=job.pk, lease_token=token, state=RecordingState.IMPORTING
            ).update(reason="source_unavailable", lease_until=None)
            return _settled(job, storage, token)
        return _finish(
            job,
            state=RecordingState.FAILED,
            reason="source_unavailable",
            storage=storage,
            token=token,
        ).state


def _settled(job, storage, token=None):
    """The job's state after a failed step.

    A finished job (expired by the sweep, fenced, published) has its staged
    objects erased and then its reservation released; the worker gives up its
    lease first, so the parts it allocated are erasable at once.
    """
    job.refresh_from_db()
    if job.state in TERMINAL_RECORDING_STATES:
        try:
            _drop_lease(job, token)
            _cleanup(job, storage)
            if job.envelope_id:
                envelopes.destroy(scope_id=job.scope_id, envelope_id=job.envelope_id)
        except Exception:  # noqa: BLE001 - ``sweep`` erases and releases; envelopes expire
            logger.warning("attachment_import_release_deferred", job_id=str(job.pk))
        return job.state
    return RecordingState.IMPORTING


def sweep(*, storage, limit=100):
    """Expire jobs past their deadline; erase staged parts; release once.

    Also revisits every finished job whose reservation is still held or that
    still has a ``staged`` segment row (an erase or a release that failed, a
    worker that was still writing when the job expired): its staged objects
    are erased first and its reservation is released only once none is left,
    so a transient error can never leak budget or bytes for good. One job's
    failure never stops the sweep.
    """
    count = 0
    for job in AttachmentImportJob.objects.filter(
        state=RecordingState.IMPORTING, deadline__lte=timezone.now()
    )[:limit]:
        try:
            _finish(
                job, state=RecordingState.EXPIRED, reason="expired", storage=storage
            )
        except Exception as error:  # noqa: BLE001 - per-job boundary; the next sweep retries
            logger.warning(
                "attachment_import_sweep_failed",
                job_id=str(job.pk),
                error=type(error).__name__,
            )
            continue
        count += 1
    staged = AttachmentRecordingSegment.objects.filter(
        state=RecordingSegmentState.STAGED
    ).values("recording_id")
    for job in AttachmentImportJob.objects.filter(
        Q(released_at__isnull=True)
        | (Q(recording_id__in=staged) & ~Q(state=RecordingState.READY)),
        state__in=TERMINAL_RECORDING_STATES,
    ).order_by("finished_at", "pk")[:limit]:
        try:
            count += bool(_cleanup(job, storage))
        except Exception as error:  # noqa: BLE001 - per-job boundary; the next sweep retries
            logger.warning(
                "attachment_import_sweep_failed",
                job_id=str(job.pk),
                error=type(error).__name__,
            )
    return count


def pending(*, limit=100):
    """Job ids a dispatcher should (re)run; ids and scopes only."""
    now = timezone.now()
    return list(
        AttachmentImportJob.objects.filter(
            Q(lease_until__isnull=True) | Q(lease_until__lte=now),
            state=RecordingState.IMPORTING,
            deadline__gt=now,
        ).values_list("pk", "scope_id")[:limit]
    )


__all__ = [
    "Importer",
    "PUBLIC_REASONS",
    "admission_check",
    "check_import_share",
    "completion_check",
    "import_budgets",
    "lifetime_need",
    "parse_request",
    "pending",
    "public_reason",
    "run",
    "server_limits",
    "status",
    "submit",
    "sweep",
]
