"""F01 imports on the shared allocation, source and capacity contracts.

A job owns its lease and scratch, not the lifetime of its published media.
Allocation keys precede PUT. Only a current epoch may record a returned version;
an unrecorded retired version is NEVER adopted. Consequently a stale writer can
safely erase its exact unreferenced result without holding a DB lock during IO.
"""

import hashlib
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentManifestRevision,
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.attachment_recording import (
    TERMINAL_RECORDING_STATES,
    AttachmentImportJob,
    AttachmentProviderRecording,
    AttachmentRecording,
    AttachmentRecordingSegment,
    RecordingSegmentState,
    RecordingState,
)
from tracer.services.attachments import capacity, recordings, sandbox
from tracer.services.attachments.bootstrap import check_session, session_policy
from tracer.services.attachments.contract import (
    AttachmentError,
    admit_audio,
    fingerprint,
)
from tracer.services.attachments.interfaces import (
    DecoderRequirements,
    LeaseFence,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    ReservationOwner,
    ResourceVector,
    SourceFacts,
    SourceRef,
    StoredObject,
    VerifiedAssetFacts,
)

logger = structlog.get_logger(__name__)
MAX_ATTEMPTS = 3
# Claim-time revalidation may retire unclaimed work only for these permanent
# source/policy revocations (import turned off or destination not approved).
# Every other denial, including redaction_unavailable, 5xx, 429 and conflict,
# re-raises unchanged and leaves the job exactly as it was.
CLAIM_RETIRING_DENIALS = frozenset({("source_unavailable", 403)})
LEASE_SECONDS = 900
PUT_SECONDS = 30
DRAIN_SECONDS = 15
BUFFER_BYTES = 256 * 1024
WINDOW_BYTES = 8 * 1024 * 1024
IMPORTED_SOURCE_KIND = "imported_recording"
IMPORT_MIME_TYPES = frozenset(
    {
        "audio/wav",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/webm",
        "audio/flac",
        "audio/L16",
    }
)
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
    "cleanup_unavailable": "incomplete",
}
STATUS_REASONS = frozenset(PUBLIC_REASONS.values()) | {"queued", "ready"}


@dataclass(frozen=True)
class Importer:
    kind: str
    id: uuid.UUID


class LeaseLost(Exception):
    """Internal fence refusal; never a new public reason."""


def public_reason(code):
    return PUBLIC_REASONS.get(code, "validation_failed")


def server_limits():
    value = getattr(settings, "FI_ATTACHMENTS_RECORDING_LIMITS", None) or {}
    return recordings.Limits(
        max_upload_bytes=min(value.get("max_upload_bytes", WINDOW_BYTES), WINDOW_BYTES),
        segment_bytes=min(value.get("segment_bytes", WINDOW_BYTES), WINDOW_BYTES),
        segment_duration_ms=min(value.get("segment_duration_ms", 30000), 30000),
        recording_bytes=min(value.get("recording_bytes", 1024**3), 1024**3),
        recording_duration_ms=min(
            value.get("recording_duration_ms", 7_200_000), 7_200_000
        ),
        reserved_bytes=min(value.get("reserved_bytes", 1024**3), 1024**3),
    )


def policy_token(scope):
    return str(scope.policy_version)


def parse_request(body):
    """Use the pinned F01 validator for BOTH wire forms, without new wire fields."""
    from tracer.services.attachments import wire

    whole = isinstance(body, dict) and body.get("mode") == "completed_recording"
    wire.validate(
        "RecordingImportRequest" if whole else "SingleAttachmentImportRequest", body
    )
    return body["recording" if whole else "upload"], body["source"]


def _source_ref(scope_id, source):
    if source["kind"] == "RemoteUrlEnvelope":
        return SourceRef(source["kind"], uuid.UUID(source["envelope_id"]))
    row = AttachmentProviderRecording.objects.filter(
        scope_id=scope_id,
        connection_id=source["connection_id"],
        registered_output_id=source["registered_output_id"],
    ).first()
    if row is None:
        raise AttachmentError("source_unavailable", 403)
    return SourceRef("ProviderFile", row.id)


def _facts(job):
    connection_id = None
    if job.provider_recording_id:
        connection_id = AttachmentProviderRecording.objects.values_list(
            "connection_id", flat=True
        ).get(pk=job.provider_recording_id, scope_id=job.scope_id)
    return SourceFacts(
        job.source_kind,
        job.envelope_id or job.provider_recording_id,
        job.source_creator_kind,
        job.source_creator_id,
        job.source_generation_kind,
        job.source_generation,
        job.source_byte_length,
        job.source_mime_type,
        job.source_duration_ms,
        job.source_expires_at,
        job.source_authority_generation,
        job.source_trace_id,
        job.source_span_id,
        job.source_direction,
        connection_id,
    )


def _revalidate(job):
    # This frozen API is local authorization/metadata only, including under locks.
    # open_source/read_range provide the remote generation check outside locks.
    from tracer.services.attachments import source_providers

    source_providers.revalidate(
        facts=_facts(job),
        scope_id=job.scope_id,
        importer=Importer(job.importer_kind, job.importer_id),
        capture_session_id=job.capture_session_id,
    )


def _lock_creator(scope, importer, *, required=True):
    """Lock real writer and any delegated user BEFORE key/grant/session rows."""
    from accounts.models import OrgApiKey, User

    users = {importer.id} if importer.kind == "user" else set()
    key = None
    grants = []
    if importer.kind == "api_key":
        key = OrgApiKey.no_workspace_objects.filter(pk=importer.id).first()
        if key is None:
            if not required:
                return
            raise AttachmentError("permission_denied", 403)
        users.update(value for value in (key.user_id, key.minted_by_id) if value)
        grants = list(
            AttachmentWriteGrant.objects.filter(scope=scope, api_key_id=key.id)
        )
        users.update(row.granted_by_id for row in grants if row.granted_by_id)
    elif importer.kind != "user":
        raise AttachmentError("permission_denied", 403)
    list(User.objects.select_for_update().filter(pk__in=users).order_by("pk"))
    if key is not None:
        locked = OrgApiKey.no_workspace_objects.select_for_update().get(pk=key.id)
        if (locked.user_id, locked.minted_by_id) != (key.user_id, key.minted_by_id):
            raise AttachmentError("conflict", 409)
        locked_grants = list(
            AttachmentWriteGrant.objects.select_for_update()
            .filter(scope=scope, api_key_id=key.id)
            .order_by("pk")
        )
        if {(row.id, row.granted_by_id) for row in locked_grants} != {
            (row.id, row.granted_by_id) for row in grants
        }:
            raise AttachmentError("conflict", 409)


def _lock_source(scope_id, ref):
    from tracer.services.attachments.source_providers import lock_source

    return lock_source(scope_id=scope_id, source=ref)


def _admit_policy(scope, session, descriptor):
    policy = session_policy(session)
    if not scope.allow_import or not getattr(policy, "allow_import", False):
        raise AttachmentError("source_unavailable", 403)
    admit_audio(
        scope,
        policy,
        direction=descriptor["direction"],
        source_kind=IMPORTED_SOURCE_KIND,
        modality=descriptor.get("modality", "audio"),
        creates_recording="recording_id" in descriptor,
    )


def _check_job(job, scope, session, target, asset, now):
    if job.storage_binding_id is None or job.capture_session_id != session.id:
        raise AttachmentError("storage_unverified", 503)
    if job.state in TERMINAL_RECORDING_STATES:
        raise LeaseLost
    if now >= job.deadline:
        raise AttachmentError("expired", 410)
    if job.deletion_generation != scope.deletion_generation:
        raise AttachmentError("deleted", 410)
    if job.policy_version != scope.policy_version:
        raise AttachmentError("policy_denied", 403)
    if (session.writer_kind, session.writer_id) != (job.importer_kind, job.importer_id):
        raise AttachmentError("permission_denied", 403)
    check_session(session)
    if job.upload_id:
        occurrence = AttachmentOccurrence.objects.select_for_update().get(
            pk=target.occurrence_id
        )
        if (
            asset.deleted_at
            or asset.state in {AttachmentState.DELETED, AttachmentState.EXPIRED}
            or occurrence.deleted_at
            or occurrence.expires_at <= now
            or asset.storage_binding_id != job.storage_binding_id
            or target.producer_kind != "import"
        ):
            raise AttachmentError("deleted", 410)
        descriptor = {"direction": occurrence.direction, "modality": asset.modality}
        if target.recording_id:
            descriptor["recording_id"] = target.recording_id
    else:
        if target.deleted_at or target.state in TERMINAL_RECORDING_STATES:
            raise AttachmentError("deleted", 410)
        descriptor = {"direction": target.direction, "recording_id": target.id}
    _admit_policy(scope, session, descriptor)
    _revalidate(job)


@contextmanager
def _locked_job_rows(job_id, *, scope_id=None, require_creator=True):
    """One lock order for claim, record, publish and stale reference checks.

    The caller starts the transaction and, if needed, takes capacity locks first.
    Cleanup deliberately takes the same locks WITHOUT requiring live source keys.
    """
    before = AttachmentImportJob.objects.get(
        pk=job_id, **({"scope_id": scope_id} if scope_id else {})
    )
    scope = AttachmentScope.objects.select_for_update().get(pk=before.scope_id)
    _lock_creator(
        scope,
        Importer(before.importer_kind, before.importer_id),
        required=require_creator,
    )
    session = CaptureSession.objects.select_for_update().get(
        pk=before.capture_session_id
    )
    _lock_source(
        before.scope_id,
        SourceRef(
            before.source_kind, before.envelope_id or before.provider_recording_id
        ),
    )
    job = AttachmentImportJob.objects.select_for_update().get(pk=before.id)
    if (
        job.scope_id,
        job.capture_session_id,
        job.envelope_id,
        job.provider_recording_id,
    ) != (
        before.scope_id,
        before.capture_session_id,
        before.envelope_id,
        before.provider_recording_id,
    ):
        raise AttachmentError("conflict", 409)
    asset = None
    if job.recording_id:
        target = AttachmentRecording.objects.select_for_update().get(
            pk=job.recording_id
        )
    else:
        upload_before = AttachmentUpload.objects.get(pk=job.upload_id)
        if upload_before.recording_id:
            AttachmentRecording.objects.select_for_update().get(
                pk=upload_before.recording_id
            )
        target = AttachmentUpload.objects.select_for_update().get(pk=job.upload_id)
        asset = Attachment.objects.select_for_update().get(pk=target.attachment_id)
    if target.scope_id != scope.id:
        raise AttachmentError("conflict", 409)
    yield job, scope, session, target, asset


@contextmanager
def _locked_job(job_id, *, scope_id=None, authorize=True):
    with _locked_job_rows(job_id, scope_id=scope_id, require_creator=authorize) as rows:
        if authorize:
            _check_job(*rows, timezone.now())
        yield rows


def _hold(job, fence, now, *, phases=("writing",)):
    if (
        fence.owner_kind != "import"
        or fence.owner_id != job.id
        or fence.token != job.lease_token
        or fence.epoch != job.lease_epoch
        or fence.expires_at != job.lease_until
        or now >= fence.expires_at
        or now >= job.deadline
        or job.state in TERMINAL_RECORDING_STATES
        or job.lease_phase not in phases
        or job.reservation_settled_at is not None
    ):
        raise LeaseLost


def _owner(job):
    return ReservationOwner("import", job.id, job.scope_id)


def _vector(job):
    return ResourceVector(
        job.reservation_charge_bytes,
        job.reservation_transfers,
        job.reservation_validation_jobs,
        job.reservation_recordings,
        job.reservation_objects,
        job.reservation_links,
    )


def _reservation(now, deadline, amount, *, source_bytes, window):
    return {
        "reservation_id": uuid.uuid4(),
        "reservation_created_at": now,
        "reservation_expires_at": deadline,
        "reservation_pool_key": capacity.IMPORT_POOL_KEY,
        "reservation_reserved_bytes": source_bytes,
        "reservation_decoded_bytes": window,
        "reservation_object_count": amount.objects,
        "reservation_part_count": 0,
        "reservation_charge_bytes": 0,
        "reservation_transfers": 0,
        "reservation_validation_jobs": 0,
        "reservation_recordings": 0,
        "reservation_objects": 0,
        "reservation_links": 0,
    }


def _replay(scope_id, descriptor, whole, digest):
    query = (
        {"recording__client_id": descriptor["recording_id"]}
        if whole
        else {"upload__attachment__client_id": descriptor["attachment_id"]}
    )
    existing = AttachmentImportJob.objects.filter(scope_id=scope_id, **query).first()
    if existing is not None and existing.request_fingerprint != digest:
        raise AttachmentError("idempotency_conflict", 409)
    return existing


def _independent_replay_facts(job, importer, session):
    """Accepted whole-job metadata only; never a source-reader expiry override.

    Locator expiry does not revoke an accepted response. Retained sealed capture
    provenance, exact admitted facts and current authority are still mandatory.
    Swept envelopes or unavailable sealing keys cannot prove that provenance.
    """
    from tracer.models.attachment_recording import AttachmentSourceEnvelope
    from tracer.services.attachments import envelopes, source_providers

    if (
        job.scope_id != session.scope_id
        or job.capture_session_id != session.id
        or (job.importer_kind, job.importer_id) != (importer.kind, importer.id)
        or job.recording_id is None
        or job.upload_id is not None
        or job.source_kind != "RemoteUrlEnvelope"
        or job.envelope_id is None
        or job.provider_recording_id is not None
        or job.policy_version != session.policy_version
        or job.deletion_generation != session.deletion_generation
    ):
        raise AttachmentError("conflict", 409)
    scope, session, policy = source_providers._session(
        job.scope_id, importer, session.id
    )
    row = AttachmentSourceEnvelope.objects.filter(
        pk=job.envelope_id, scope_id=job.scope_id, revoked_at__isnull=True
    ).first()
    if (
        row is None
        or source_providers._envelope_provider(job.scope_id, row.id) is not None
    ):
        raise AttachmentError("source_unavailable", 403)
    envelopes.check_capture_session(row, session.id)
    facts = source_providers._job_facts(job)
    if (
        source_providers._envelope_facts(row) != facts
        or (facts.creator_kind, facts.creator_id) != (importer.kind, importer.id)
        or (job.mime_type, job.declared_bytes, job.declared_duration_ms)
        != (facts.mime_type, facts.byte_length, facts.duration_ms)
    ):
        raise AttachmentError("snapshot_changed", 409)
    admit_audio(
        scope, policy, direction=facts.direction, source_kind=IMPORTED_SOURCE_KIND
    )
    if not transaction.get_connection().in_atomic_block:
        source_providers._parent(facts, scope, importer)
    return facts


def _independent_replay_locked(
    importer, scope_id, session, ref, facts, descriptor, digest
):
    """Caller holds scope/creator/session/source locks, in that order."""
    if not transaction.get_connection().in_atomic_block:
        raise AttachmentError("conflict", 409)
    before = _replay(scope_id, descriptor, True, digest)
    if before is None:
        return None
    job = AttachmentImportJob.objects.select_for_update().filter(pk=before.id).first()
    if (
        job is None
        or session.scope_id != scope_id
        or job.request_fingerprint != digest
        or ref.kind != "RemoteUrlEnvelope"
        or job.envelope_id != ref.id
        or _independent_replay_facts(job, importer, session) != facts
    ):
        raise AttachmentError("conflict", 409)
    recording = (
        AttachmentRecording.objects.select_for_update()
        .filter(pk=job.recording_id)
        .first()
    )
    association = descriptor["association"]
    parent = association["parent"]
    if (
        recording is None
        or recording.scope_id != scope_id
        or recording.client_id != uuid.UUID(descriptor["recording_id"])
        or recording.capture_session_id != session.id
        or recording.capture_session_client_id != session.client_id
        or recording.correlation_nonce != session.correlation_nonce
        or session.client_id != uuid.UUID(association["capture_session_id"])
        or session.correlation_nonce != uuid.UUID(association["correlation_nonce"])
        or recording.trace_id != facts.parent_trace_id
        or recording.span_id != facts.parent_span_id
        or facts.parent_trace_id != uuid.UUID(parent["trace_id"])
        or facts.parent_span_id != parent["span_id"]
        or recording.direction != facts.direction
        or recording.policy_version != job.policy_version
        or recording.deletion_generation != job.deletion_generation
    ):
        raise AttachmentError("conflict", 409)
    job.recording = recording
    return job


def _independent_replay(importer, scope_id, session, ref, descriptor, digest):
    """Recheck current parent outside locks, then exact local accepted binding."""
    if transaction.get_connection().in_atomic_block:
        raise AttachmentError("conflict", 409)
    before = _replay(scope_id, descriptor, True, digest)
    if before is None:
        return None
    if ref.kind != "RemoteUrlEnvelope" or before.envelope_id != ref.id:
        raise AttachmentError("conflict", 409)
    facts = _independent_replay_facts(before, importer, session)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        _lock_creator(scope, importer)
        session = CaptureSession.objects.select_for_update().get(pk=session.id)
        _, _, registered = _lock_source(scope_id, ref)
        if registered is not None:
            raise AttachmentError("conflict", 409)
        check_session(session)
        _admit_policy(scope, session, descriptor)
        job = _independent_replay_locked(
            importer, scope_id, session, ref, facts, descriptor, digest
        )
        if job is None or job.id != before.id:
            raise AttachmentError("conflict", 409)
        return job


def _response(job):
    if job.upload_id:
        return {
            "job_id": str(job.id),
            "upload_id": str(job.upload_id),
            "state": "importing",
        }
    return {
        "mode": "completed_recording",
        "job_id": str(job.id),
        "recording_id": str(job.recording.client_id),
        "state": "importing",
    }


def _check_registered_descriptor(row, session, descriptor):
    from tracer.services.attachments.contract import trace_uuid
    from tracer.services.attachments.provider_recordings import _dispatch_body

    expected = _dispatch_body(row, session)["recording"]
    actual = {
        **descriptor,
        "association": {
            **descriptor["association"],
            "parent": {**descriptor["association"]["parent"]},
        },
    }
    if actual["source_kind"] not in {"ProviderFile", "RemoteUrl"}:
        raise AttachmentError("conflict", 409)
    actual["source_kind"] = "ProviderFile"
    actual["association"]["parent"]["trace_id"] = trace_uuid(
        actual["association"]["parent"]["trace_id"]
    ).hex
    if actual != expected:
        raise AttachmentError("conflict", 409)


def _registered_job(
    row, session, importer, descriptor, facts, *, link=True, recover_auto=False
):
    """Under ordered source locks: exact semantic replay, including legacy aliases.

    Stored fingerprints are never rewritten. Independent sources and single
    imports continue to use their original fingerprint comparison.
    """
    from tracer.services.attachments import source_providers

    if recover_auto:
        if row.dispatch_mode != "auto" or descriptor is not None or not link:
            raise AttachmentError("conflict", 409)
    else:
        _check_registered_descriptor(row, session, descriptor)
    candidates = list(
        AttachmentImportJob.objects.select_for_update(of=("self",))
        .filter(
            scope_id=row.scope_id,
        )
        .filter(
            Q(pk=row.import_job_id)
            | Q(recording__client_id=row.recording_client_id)
            | Q(provider_recording_id=row.id, recording__isnull=False)
            | Q(envelope_id=row.output_envelope_id, recording__isnull=False)
        )
        .order_by("id")[:2]
    )
    if not candidates:
        if row.import_job_id is not None:
            raise AttachmentError("conflict", 409)
        return None
    if len(candidates) != 1:
        raise AttachmentError("conflict", 409)
    job = candidates[0]
    if (
        job.recording_id is None
        or job.upload_id is not None
        or (row.import_job_id is not None and row.import_job_id != job.id)
        or (job.importer_kind, job.importer_id) != (importer.kind, importer.id)
        or job.capture_session_id != session.id
        or (not recover_auto and job.policy_version != session.policy_version)
        or (not recover_auto and job.deletion_generation != session.deletion_generation)
        or job.mime_type != facts.mime_type
        or job.declared_bytes != facts.byte_length
        or job.declared_duration_ms != facts.duration_ms
        or not (
            (
                job.source_kind == "ProviderFile"
                and job.provider_recording_id == row.id
                and job.envelope_id is None
            )
            or (
                job.source_kind == "RemoteUrlEnvelope"
                and job.envelope_id == row.output_envelope_id
                and job.provider_recording_id is None
            )
        )
    ):
        raise AttachmentError("conflict", 409)
    recording = AttachmentRecording.objects.select_for_update().get(pk=job.recording_id)
    if (
        recording.scope_id != row.scope_id
        or recording.client_id != row.recording_client_id
        or recording.capture_session_id != session.id
        or recording.capture_session_client_id != session.client_id
        or recording.correlation_nonce != session.correlation_nonce
        or recording.trace_id != row.trace_id
        or recording.span_id != row.span_id
        or recording.direction != row.direction
        or recording.policy_version != job.policy_version
        or recording.deletion_generation != job.deletion_generation
    ):
        raise AttachmentError("conflict", 409)
    # Includes independent envelope/provider epochs and exact sealed generation.
    source_providers.revalidate(
        facts=_facts(job),
        scope_id=row.scope_id,
        importer=importer,
        capture_session_id=session.id,
    )
    if link and row.import_job_id is None:
        row.import_job_id, row.dispatch_due_at = job.id, None
        row.save(update_fields=["import_job_id", "dispatch_due_at"])
    return job


def recover_auto_dispatch(row, importer):
    """Recover an old, exact orphan link before constructing any renewed body.

    This is not admission or policy renewal: the existing job and its immutable
    original policy/fingerprint stay unchanged; the worker still reauthorizes.
    No caller descriptor can request this recovery mode.
    """
    from tracer.services.attachments import source_providers
    from tracer.services.attachments.provider_recordings import _registration_session

    if row.dispatch_mode != "auto":
        raise AttachmentError("conflict", 409)
    session = _registration_session(row, importer, row.scope_id)
    ref = SourceRef("ProviderFile", row.id)
    facts = source_providers.resolve(
        source=ref,
        scope_id=row.scope_id,
        importer=importer,
        capture_session_id=session.id,
    )
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=row.scope_id)
        _lock_creator(scope, importer)
        session = CaptureSession.objects.select_for_update().get(pk=session.id)
        _, _, locked = _lock_source(row.scope_id, ref)
        check_session(session)
        source_providers.revalidate(
            facts=facts,
            scope_id=row.scope_id,
            importer=importer,
            capture_session_id=session.id,
        )
        return _registered_job(
            locked, session, importer, None, facts, recover_auto=True
        )


def _registered_replay(
    importer, scope_id, session, ref, facts, original_facts, descriptor
):
    from tracer.services.attachments import capacity_db, source_providers

    with transaction.atomic():
        capacity_db.lock_capacity(
            scope_id=scope_id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        _lock_creator(scope, importer)
        session = CaptureSession.objects.select_for_update().get(pk=session.id)
        _, _, row = _lock_source(scope_id, ref)
        check_session(session)
        _admit_policy(scope, session, descriptor)
        for current in (original_facts, facts):
            source_providers.revalidate(
                facts=current,
                scope_id=scope_id,
                importer=importer,
                capture_session_id=session.id,
            )
        return _registered_job(row, session, importer, descriptor, facts)


def submit(importer, *, scope_id, body):
    from tracer.services.attachments import (
        capacity_db,
        readiness,
        source_providers,
        uploads,
        validation,
    )

    descriptor, source = parse_request(body)
    whole = body.get("mode") == "completed_recording"
    scope = AttachmentScope.objects.get(pk=scope_id)
    association = descriptor["association"]
    session = CaptureSession.objects.filter(
        scope=scope,
        client_id=association["capture_session_id"],
        correlation_nonce=association["correlation_nonce"],
        writer_kind=importer.kind,
        writer_id=importer.id,
    ).first()
    if session is None:
        raise AttachmentError("not_found", 404)
    check_session(session)
    _admit_policy(scope, session, descriptor)
    digest = fingerprint({"importer": [importer.kind, importer.id], "body": body})
    if not whole:
        replay = _replay(scope_id, descriptor, whole, digest)
        if replay is not None:
            return _response(replay), False
    ref = _source_ref(scope_id, source)
    registered = None
    if whole:
        registered = (
            AttachmentProviderRecording.objects.get(pk=ref.id, scope_id=scope_id)
            if ref.kind == "ProviderFile"
            else source_providers._envelope_provider(scope_id, ref.id)
        )
        if registered is None:
            replay = _independent_replay(
                importer, scope_id, session, ref, descriptor, digest
            )
            if replay is not None:
                return _response(replay), False
    facts = source_providers.resolve(
        source=ref, scope_id=scope_id, importer=importer, capture_session_id=session.id
    )
    original_facts = facts
    if whole:
        if registered is not None:
            _check_registered_descriptor(registered, session, descriptor)
            # All NEW registered whole jobs use the same canonical source ref.
            # The incoming alias was authenticated above, and is rechecked below.
            ref = SourceRef("ProviderFile", registered.id)
            facts = source_providers.resolve(
                source=ref,
                scope_id=scope_id,
                importer=importer,
                capture_session_id=session.id,
            )
            replay = _registered_replay(
                importer, scope_id, session, ref, facts, original_facts, descriptor
            )
        else:
            replay = _independent_replay(
                importer, scope_id, session, ref, descriptor, digest
            )
        if replay is not None:
            return _response(replay), False
    parent = association["parent"]
    if (
        facts.byte_length != descriptor["declared_bytes"]
        or facts.mime_type != descriptor["mime_type"]
        or facts.parent_trace_id != uuid.UUID(parent["trace_id"])
        or facts.parent_span_id != parent["span_id"]
        or (facts.creator_kind, facts.creator_id) != (importer.kind, importer.id)
    ):
        raise AttachmentError("snapshot_changed", 409)
    if (
        descriptor["direction"] != "mixed"
        and descriptor["direction"] != facts.direction
    ):
        raise AttachmentError("policy_denied", 403)
    # SourceFacts does not carry trusted PCM rate/channel/endianness metadata.
    # Keep the full-format contract visible, but do not admit an unexecutable plan.
    validation.require_supported_plan(facts.mime_type)
    limits = server_limits()
    now = timezone.now()
    deadline = min(now + timedelta(hours=3), facts.expires_at, session.expires_at)
    if deadline <= now:
        raise AttachmentError("expired", 410)
    if whole:
        if (
            facts.duration_ms != descriptor["declared_duration_ms"]
            or facts.mime_type not in IMPORT_MIME_TYPES
            or facts.byte_length > limits.recording_bytes
            or not facts.duration_ms
            or facts.duration_ms > limits.recording_duration_ms
        ):
            raise AttachmentError("validation_failed", 422)
        amount = capacity.whole_import_floor(
            source_bytes=facts.byte_length, output_scratch_bytes=limits.window
        )
        upload_amount = None
    else:
        upload_amount, job_amount = capacity.single_import_peaks(
            source_bytes=facts.byte_length,
            decoder_scratch_bytes=WINDOW_BYTES,
            extra_reader_scratch_bytes=0,
        )
        amount = upload_amount + job_amount
    if any(
        not capacity.fits_within(amount, cap)
        for cap in capacity.limits_for(
            scope_id=scope_id, pool_key=capacity.IMPORT_POOL_KEY
        )
    ):
        raise AttachmentError("too_large", 413)
    if not capacity_db.count_attempt(
        scope_id=scope_id, pool_key=capacity.IMPORT_POOL_KEY, now=now
    ):
        raise AttachmentError("throttled", 429)
    binding_id = readiness.require_new_admission(operation="import")
    validation.require_decoder(
        DecoderRequirements(
            "normalize" if whole else "validate", facts.mime_type, limits.window
        )
    )
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=scope_id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        _lock_creator(scope, importer)
        session = CaptureSession.objects.select_for_update().get(pk=session.id)
        _, _, locked_registration = _lock_source(scope_id, ref)
        if whole and (
            locked_registration.id if locked_registration is not None else None
        ) != (registered.id if registered is not None else None):
            raise AttachmentError("conflict", 409)
        check_session(session)
        _admit_policy(scope, session, descriptor)
        source_providers.revalidate(
            facts=facts,
            scope_id=scope_id,
            importer=importer,
            capture_session_id=session.id,
        )
        if (
            descriptor["policy_version"] != policy_token(scope)
            or descriptor["deletion_generation"] != scope.deletion_generation
        ):
            raise AttachmentError("conflict", 409)
        if registered is not None:
            source_providers.revalidate(
                facts=original_facts,
                scope_id=scope_id,
                importer=importer,
                capture_session_id=session.id,
            )
            if locked_registration is None or locked_registration.id != registered.id:
                raise AttachmentError("conflict", 409)
            replay = _registered_job(
                locked_registration, session, importer, descriptor, facts
            )
        elif whole:
            # A concurrent admission may have committed since the outside pass.
            # Its persisted facts must match the source authorized before locks.
            replay = _independent_replay_locked(
                importer, scope_id, session, ref, facts, descriptor, digest
            )
        else:
            replay = _replay(scope_id, descriptor, whole, digest)
        if replay is not None:
            return _response(replay), False
        job_id = uuid.uuid4()
        job = AttachmentImportJob(
            id=job_id,
            scope=scope,
            capture_session=session,
            storage_binding_id=binding_id,
            importer_kind=importer.kind,
            importer_id=importer.id,
            request_fingerprint=digest,
            source_kind=ref.kind,
            envelope_id=ref.id if ref.kind == "RemoteUrlEnvelope" else None,
            provider_recording_id=ref.id if ref.kind == "ProviderFile" else None,
            mime_type=facts.mime_type,
            declared_bytes=facts.byte_length,
            declared_duration_ms=facts.duration_ms or 0,
            window_bytes=limits.window,
            max_segment_ms=limits.segment_duration_ms,
            recording_bytes=limits.recording_bytes,
            deadline=deadline,
            policy_version=scope.policy_version,
            deletion_generation=scope.deletion_generation,
            scratch_id=job_id,
            cleanup_not_before=now,
            source_generation_kind=facts.generation_kind,
            source_generation=facts.generation,
            source_byte_length=facts.byte_length,
            source_mime_type=facts.mime_type,
            source_duration_ms=facts.duration_ms,
            source_expires_at=facts.expires_at,
            source_authority_generation=facts.authority_generation,
            source_creator_kind=facts.creator_kind,
            source_creator_id=facts.creator_id,
            source_trace_id=facts.parent_trace_id,
            source_span_id=facts.parent_span_id,
            source_direction=facts.direction,
            **_reservation(
                now,
                deadline,
                amount,
                source_bytes=facts.byte_length,
                window=limits.window,
            ),
        )
        if whole:
            job.recording = AttachmentRecording.objects.create(
                scope=scope,
                client_id=descriptor["recording_id"],
                capture_session=session,
                trace_id=facts.parent_trace_id,
                span_id=facts.parent_span_id,
                capture_session_client_id=session.client_id,
                correlation_nonce=session.correlation_nonce,
                direction=facts.direction,
                state=RecordingState.IMPORTING,
                policy_version=scope.policy_version,
                deletion_generation=scope.deletion_generation,
            )
        else:
            # Unsaved job supplies the preallocated ID and immutable admission facts.
            # The helper creates a zero-vector Upload, not a second reservation.
            job.upload = uploads.initialize_import_locked(
                scope=scope,
                capture_session=session,
                body={**descriptor, "direction": facts.direction},
                job=job,
                binding_id=binding_id,
                locks=locks,
            )
        job.save(force_insert=True)  # exact-one target holds even at INSERT
        capacity_db.reserve_locked(
            locks, owner=_owner(job), amount=amount, expires_at=deadline
        )
        if registered is not None:
            locked_registration.import_job_id, locked_registration.dispatch_due_at = (
                job.id,
                None,
            )
            locked_registration.save(update_fields=["import_job_id", "dispatch_due_at"])
        if upload_amount is not None:
            capacity_db.transfer_locked(
                locks,
                from_owner=_owner(job),
                to_owner=ReservationOwner("upload", job.upload_id, scope_id),
                amount=upload_amount,
            )
    return _response(job), True


def status(importer, *, scope_id, job_id):
    job = AttachmentImportJob.objects.filter(
        pk=job_id,
        scope_id=scope_id,
        importer_kind=importer.kind,
        importer_id=importer.id,
    ).first()
    if job is None:
        raise AttachmentError("not_found", 404)
    # Original credentials must remain eligible; expired work is still inspectable.
    from tracer.services.attachments.bootstrap import Writer, check_writer

    scope = AttachmentScope.objects.get(pk=scope_id)
    check_writer(
        Writer(importer.kind, importer.id, scope.organization_id, scope.workspace_id),
        scope,
    )
    return {
        "job_id": str(job.id),
        "state": job.state,
        "reason": job.reason
        if job.reason in STATUS_REASONS
        else public_reason(job.reason),
    }


def claim(job_id, *, scope_id, now):
    from tracer.services.attachments import capacity_db

    if not capacity_db.count_attempt(
        scope_id=scope_id, pool_key=capacity.IMPORT_POOL_KEY, now=now
    ):
        return None
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=scope_id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        with _locked_job_rows(job_id, scope_id=scope_id) as rows:
            job, _, _, target, asset = rows
            if job.state in TERMINAL_RECORDING_STATES or (
                job.lease_until and job.lease_until > now
            ):
                return None
            try:
                _check_job(*rows, timezone.now())
            except AttachmentError as error:
                # Only an allowlisted permanent revocation may retire unclaimed work.
                if (error.code, error.status) not in CLAIM_RETIRING_DENIALS:
                    raise
                _retire_locked(
                    job,
                    target,
                    asset,
                    error.code,
                    _terminal_denial(error.code),
                    claim_denied=True,
                )
                _log_deferred(job.id, error.code)
                return None
            return _claim_locked(job, locks, now)


def _claim_locked(job, locks, now):
    from tracer.services.attachments import capacity_db

    if job.attempts >= MAX_ATTEMPTS or (job.lease_until and job.lease_until > now):
        return None
    # Save a valid replacement lease before resize updates this guarded row.
    # Both writes belong to claim's complete-lock transaction: capacity failure
    # rolls the replacement lease back as well as the reservation/budget writes.
    previous_epoch = job.lease_epoch
    job.attempts += 1
    job.lease_epoch += 1
    job.lease_token = uuid.uuid4()
    job.lease_until = min(now + timedelta(seconds=LEASE_SECONDS), job.deadline)
    job.lease_phase = "recovering"
    job.cleanup_not_before = max(
        job.cleanup_not_before or now,
        job.lease_until + timedelta(seconds=DRAIN_SECONDS),
    )
    job.save(
        update_fields=[
            "attempts",
            "lease_epoch",
            "lease_token",
            "lease_until",
            "lease_phase",
            "cleanup_not_before",
        ]
    )
    if previous_epoch:
        # A paused previous process may still hold its source/output files.
        # Replacement scratch is new physical allocation, not a second base U.
        extra = ResourceVector(job.source_byte_length + job.window_bytes, 1, 1, 0, 2, 0)
        capacity_db.resize_locked(locks, owner=_owner(job), amount=_vector(job) + extra)
    # Create the guard only after capacity succeeds, before any accepted IO.
    sandbox.prepare_import_scratch(job.id, job.lease_epoch)
    return LeaseFence(
        "import", job.id, job.lease_token, job.lease_epoch, job.lease_until
    )


def _allocation(job, allocation_id, kind):
    if kind == "upload":
        if allocation_id != job.upload_id:
            raise AttachmentError("conflict", 409)
        return AttachmentUpload.objects.select_for_update().get(pk=allocation_id)
    if kind != "segment" or job.recording_id is None:
        raise AttachmentError("conflict", 409)
    return AttachmentRecordingSegment.objects.select_for_update().get(
        pk=allocation_id,
        import_job_id=job.id,
        scope_id=job.scope_id,
        recording_id=job.recording_id,
        storage_binding_id=job.storage_binding_id,
    )


def _locator(job, row, kind, purpose):
    if purpose == "quarantine":
        key = row.quarantine_key
    elif purpose == "serving":
        key = row.storage_key if kind == "segment" else row.serving_key
    else:
        raise AttachmentError("conflict", 409)
    return ObjectLocator(job.storage_binding_id, purpose, key)


def _version_field(kind, purpose):
    return (
        "quarantine_version"
        if purpose == "quarantine"
        else ("storage_version" if kind == "segment" else "serving_version")
    )


def _referenced(job, row, kind, identity):
    if identity.binding_id != job.storage_binding_id:
        return False
    loc = _locator(job, row, kind, identity.purpose)
    if loc.key != identity.key:
        return False
    if kind == "segment" and identity.purpose == "quarantine":
        return False
    if getattr(row, _version_field(kind, identity.purpose)) == identity.version:
        return True
    return Attachment.objects.filter(
        storage_binding_id=identity.binding_id,
        verified_storage_key=identity.key,
        verified_storage_version=identity.version,
    ).exists()


def _permit_matches(job, row, kind, permit, identity):
    if (
        permit.allocation_kind != kind
        or permit.allocation_id != row.id
        or permit.fence is None
        or permit.fence.owner_id != job.id
        or permit.locator != _locator(job, row, kind, identity.purpose)
        or (identity.binding_id, identity.purpose, identity.key)
        != (permit.locator.binding_id, permit.locator.purpose, permit.locator.key)
    ):
        raise AttachmentError("conflict", 409)


def prepare_put(*, job_id, allocation_id, allocation_kind, fence, purpose, now):
    with transaction.atomic(), _locked_job(job_id) as (job, _, _, _, _):
        _hold(job, fence, now)
        row = _allocation(job, allocation_id, allocation_kind)
        if allocation_kind == "segment" and (
            purpose != "serving" or row.state != RecordingSegmentState.STAGED
        ):
            raise LeaseLost
        if allocation_kind == "upload" and row.state not in {
            AttachmentState.PENDING,
            AttachmentState.VALIDATING,
        }:
            raise LeaseLost
        if getattr(row, _version_field(allocation_kind, purpose)) is not None:
            raise AttachmentError("conflict", 409)
        field = (
            "write_attempts"
            if allocation_kind == "segment"
            else f"{purpose}_write_attempts"
        )
        if getattr(row, field) >= MAX_ATTEMPTS:
            raise AttachmentError("source_unavailable", 403)
        # Only this live epoch's process may dispatch; missing supervisor proof is
        # not a license for a detached writer or an arbitrary caller to PUT.
        sandbox.require_import_execution(job.id, fence.epoch)
        setattr(row, field, getattr(row, field) + 1)
        row.allocation_epoch = fence.epoch
        row.write_deadline_at = min(
            now + timedelta(seconds=PUT_SECONDS), fence.expires_at, job.deadline
        )
        row.cleanup_not_before = max(
            row.cleanup_not_before or now,
            fence.expires_at + timedelta(seconds=DRAIN_SECONDS),
        )
        row.unowned_cleanup_due_at = row.cleanup_not_before
        row.save(
            update_fields=[
                field,
                "allocation_epoch",
                "write_deadline_at",
                "cleanup_not_before",
                "unowned_cleanup_due_at",
            ]
        )
        job.cleanup_not_before = max(job.cleanup_not_before, row.cleanup_not_before)
        job.save(update_fields=["cleanup_not_before"])
        return PutPermit(
            allocation_kind,
            row.id,
            _locator(job, row, allocation_kind, purpose),
            fence,
            getattr(row, field),
            row.write_deadline_at,
        )


def record_put(*, job_id, allocation_id, allocation_kind, permit, identity, facts):
    with (
        transaction.atomic(),
        _locked_job(job_id, authorize=False) as (job, scope, session, target, asset),
    ):
        # Ownership is checked BEFORE current-lease checks: a stale retry of an
        # accepted exact version must not erase the replacement's reused media.
        row = _allocation(job, allocation_id, allocation_kind)
        _permit_matches(job, row, allocation_kind, permit, identity)
        if _referenced(job, row, allocation_kind, identity):
            return "already_owned"
        try:
            _hold(job, permit.fence, timezone.now())
        except LeaseLost:
            return "stale_unowned"
        if (
            row.allocation_epoch != permit.fence.epoch
            or row.write_deadline_at != permit.deadline_at
            or timezone.now() >= permit.deadline_at
        ):
            return "stale_unowned"
        if allocation_kind == "segment" and row.state != RecordingSegmentState.STAGED:
            return "stale_unowned"
        _check_job(job, scope, session, target, asset, timezone.now())
        field = (
            "write_attempts"
            if allocation_kind == "segment"
            else f"{identity.purpose}_write_attempts"
        )
        if getattr(row, field) != permit.write_attempt:
            return "stale_unowned"
        version_field = _version_field(allocation_kind, identity.purpose)
        if getattr(row, version_field) is not None:
            return "stale_unowned"
        if facts is None or facts.object.identity != identity:
            # Quarantine snapshots are normally accepted by the shared Upload
            # helper, which records hash/length/version atomically with its fence.
            raise AttachmentError("validation_failed", 422)
        if allocation_kind == "segment":
            if (
                facts.mime_type != "audio/wav"
                or facts.object.sha256 != row.sha256
                or facts.object.byte_length != row.byte_length
                or facts.frame_count != row.frame_count
                or facts.sample_rate != target.sample_rate
                or facts.channels != target.channels
            ):
                raise AttachmentError("snapshot_changed", 409)
        else:
            if facts.object.byte_length != row.expected_bytes:
                raise AttachmentError("snapshot_changed", 409)
        setattr(row, version_field, identity.version)
        fields = [version_field]
        if allocation_kind == "upload" and identity.purpose == "quarantine":
            row.quarantine_sha256 = facts.object.sha256
            row.received_bytes = facts.object.byte_length
            fields += ["quarantine_sha256", "received_bytes"]
        row.save(update_fields=fields)
        return "recorded"


def cleanup_stale_put(*, job_id, allocation_id, allocation_kind, permit, identity):
    from tracer.services.attachments.readiness import resolve_historical

    with (
        transaction.atomic(),
        _locked_job(job_id, authorize=False) as (job, _, _, _, _),
    ):
        row = _allocation(job, allocation_id, allocation_kind)
        _permit_matches(job, row, allocation_kind, permit, identity)
        if _referenced(job, row, allocation_kind, identity):
            return "already_owned"
        # A still-current result must first pass record_put; no live worker may
        # delete its own eligible-to-be-recorded object out from under publication.
        if (
            job.lease_token == permit.fence.token
            and job.lease_epoch == permit.fence.epoch
            and job.lease_phase == "writing"
            and job.state not in TERMINAL_RECORDING_STATES
            and job.lease_until
            and timezone.now() < min(job.lease_until, permit.deadline_at)
            and row.allocation_epoch == permit.fence.epoch
        ):
            raise AttachmentError("conflict", 409)
        row.unowned_cleanup_due_at = timezone.now()
        row.save(update_fields=["unowned_cleanup_due_at"])
    # No code can subsequently adopt this unrecorded retired version. Never
    # erase_upload/all_versions here, and never restore allocation state/version.
    resolve_historical(identity.binding_id, operation="erase").erase_version(identity)
    return "stale_unowned"


def _storage(job, storage, operation):
    if storage is None:
        from tracer.services.attachments.readiness import resolve_historical

        return resolve_historical(job.storage_binding_id, operation=operation)
    if getattr(storage, "binding_id", None) != job.storage_binding_id:
        raise AttachmentError("storage_unverified", 503)
    storage.verify_identity(operation=operation)
    return storage


def _allocations(job):
    if job.upload_id:
        return [("upload", AttachmentUpload.objects.get(pk=job.upload_id))]
    return [
        ("segment", row)
        for row in AttachmentRecordingSegment.objects.filter(
            import_job_id=job.id
        ).order_by("index")
    ]


def _clean_versions(job, storage, *, fence=None):
    """Recovery/terminal enumeration, not a key-wide deletion or adoption."""
    for kind, before in _allocations(job):
        for purpose in ("quarantine", "serving"):
            locator = _locator(job, before, kind, purpose)
            cursor = None
            for _ in range(100):
                page = storage.list_versions(locator, cursor=cursor, limit=100)
                for identity in (*page.versions, *page.delete_markers):
                    if (identity.binding_id, identity.purpose, identity.key) != (
                        locator.binding_id,
                        locator.purpose,
                        locator.key,
                    ):
                        raise AttachmentError("cleanup_unavailable", 503)
                    with (
                        transaction.atomic(),
                        _locked_job(job.id, authorize=False) as (held, _, _, _, _),
                    ):
                        if fence is not None:
                            _hold(held, fence, timezone.now(), phases=("recovering",))
                        elif held.state not in TERMINAL_RECORDING_STATES:
                            raise LeaseLost
                        row = _allocation(held, before.id, kind)
                        owned = _referenced(held, row, kind, identity)
                    if not owned:
                        storage.erase_version(identity)
                if page.next_cursor is None:
                    break
                if page.next_cursor == cursor:
                    raise AttachmentError("cleanup_unavailable", 503)
                cursor = page.next_cursor
            else:
                raise AttachmentError("cleanup_unavailable", 503)
    if fence is None:
        with (
            transaction.atomic(),
            _locked_job(job.id, authorize=False) as (held, _, _, _, _),
        ):
            if held.state not in TERMINAL_RECORDING_STATES:
                raise LeaseLost
            for kind, before in _allocations(held):
                row = _allocation(held, before.id, kind)
                row.cleanup_checked_at = timezone.now()
                row.unowned_cleanup_due_at = None
                row.save(update_fields=["cleanup_checked_at", "unowned_cleanup_due_at"])
    if fence is not None:
        with transaction.atomic(), _locked_job(job.id) as (held, _, _, _, _):
            _hold(held, fence, timezone.now(), phases=("recovering",))
            held.lease_phase = "writing"
            held.save(update_fields=["lease_phase"])


def _fetch(job, fence, reader, snapshot):
    if reader.facts != _facts(job):
        raise AttachmentError("snapshot_changed", 409)
    offset, digest = 0, hashlib.sha256()
    while offset < job.source_byte_length:
        with transaction.atomic(), _locked_job(job.id) as (held, _, _, _, _):
            _hold(held, fence, timezone.now())
        # The reader enforces generation before/after every range. Request only
        # one transport buffer so even the returned bytes object stays bounded.
        length = min(BUFFER_BYTES, job.source_byte_length - offset)
        block = reader.read_range(start=offset, end=offset + length - 1)
        if not isinstance(block, bytes) or len(block) != length:
            raise AttachmentError("snapshot_changed", 409)
        snapshot.write(block)
        digest.update(block)
        offset += length
    snapshot.flush()
    if os.fstat(snapshot.fileno()).st_size != job.source_byte_length:
        raise AttachmentError("length_mismatch", 422)
    return digest.hexdigest()


def _allocate(job, fence, plan, digest):
    from tracer.services.attachments import capacity_db

    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=job.scope_id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        with _locked_job(job.id) as (held, _, _, target, _):
            _hold(held, fence, timezone.now())
            existing = (
                AttachmentRecordingSegment.objects.select_for_update()
                .filter(import_job_id=job.id, index=plan.index)
                .first()
            )
            if existing is not None:
                if existing.state != RecordingSegmentState.STAGED or (
                    existing.start_frame,
                    existing.frame_count,
                    existing.start_ms,
                    existing.duration_ms,
                    existing.byte_length,
                    existing.sha256,
                    existing.storage_binding_id,
                ) != (
                    plan.start_frame,
                    plan.frame_count,
                    plan.start_ms,
                    plan.duration_ms,
                    plan.byte_length,
                    digest,
                    held.storage_binding_id,
                ):
                    raise AttachmentError("snapshot_changed", 409)
                return existing
            amount = _vector(held) + capacity.whole_import_segment_peak(
                segment_bytes=plan.byte_length
            )
            capacity_db.resize_locked(locks, owner=_owner(held), amount=amount)
            held.reservation_part_count += 1
            held.save(update_fields=["reservation_part_count"])
            identifier = uuid.uuid4()
            return AttachmentRecordingSegment.objects.create(
                id=identifier,
                scope_id=held.scope_id,
                recording=target,
                import_job=held,
                storage_binding_id=held.storage_binding_id,
                storage_object_id=identifier,
                index=plan.index,
                attachment_client_id=uuid.uuid4(),
                occurrence_client_id=uuid.uuid4(),
                start_frame=plan.start_frame,
                frame_count=plan.frame_count,
                start_ms=plan.start_ms,
                duration_ms=plan.duration_ms,
                byte_length=plan.byte_length,
                sha256=digest,
                quarantine_key=f"quarantine/{identifier}",
                storage_key=f"original/{identifier}",
                allocation_epoch=fence.epoch,
                cleanup_not_before=held.cleanup_not_before,
            )


def _segment_source(job, fence, snapshot, directory, storage):
    limits = recordings.Limits(
        job.window_bytes,
        job.window_bytes,
        job.max_segment_ms,
        job.recording_bytes,
        7_200_000,
        1024**3,
    )
    facts = sandbox.run("source", snapshot.name, job.source_mime_type)
    if (
        facts["duration_ms"] != job.source_duration_ms
        or facts["mime_type"] != job.source_mime_type
    ):
        raise AttachmentError("snapshot_changed", 409)
    plans = recordings.plan_segments(
        frame_count=facts["frame_count"],
        rate=facts["sample_rate"],
        channels=facts["channels"],
        limits=limits,
    )
    total = sum(plan.byte_length for plan in plans)
    if total > job.recording_bytes:
        raise AttachmentError("too_large", 413)
    peak = capacity.whole_import_floor(
        source_bytes=job.source_byte_length, output_scratch_bytes=job.window_bytes
    )
    for plan in plans:
        peak += capacity.whole_import_segment_peak(segment_bytes=plan.byte_length)
    if any(
        not capacity.fits_within(peak, cap)
        for cap in capacity.limits_for(
            scope_id=job.scope_id, pool_key=capacity.IMPORT_POOL_KEY
        )
    ):
        raise AttachmentError("too_large", 413)
    with transaction.atomic(), _locked_job(job.id) as (held, _, _, target, _):
        _hold(held, fence, timezone.now())
        if target.sample_rate is not None and (target.sample_rate, target.channels) != (
            facts["sample_rate"],
            facts["channels"],
        ):
            raise AttachmentError("snapshot_changed", 409)
        target.sample_rate, target.channels = facts["sample_rate"], facts["channels"]
        target.save(update_fields=["sample_rate", "channels"])
    rows = []
    # One decoder, source snapshot + ONE output window, independent of call length.
    with sandbox.pcm_stream(
        snapshot.name, job.source_mime_type, facts, deadline=fence.expires_at
    ) as pcm:
        for plan in plans:
            with sandbox.scratch_file(directory, "segment") as part:
                sandbox.write_wav_segment(pcm, part, plan, facts)
                probed = sandbox.probe(part.name)
                if (
                    probed["frame_count"],
                    probed["sample_rate"],
                    probed["channels"],
                ) != (plan.frame_count, facts["sample_rate"], facts["channels"]):
                    raise AttachmentError("validation_failed", 422)
                digest = sandbox.hash_file(part)
                row = _allocate(job, fence, plan, digest)
                if row.storage_version is None:
                    permit = prepare_put(
                        job_id=job.id,
                        allocation_id=row.id,
                        allocation_kind="segment",
                        fence=fence,
                        purpose="serving",
                        now=timezone.now(),
                    )
                    part.seek(0)
                    identity = storage.promote(
                        part,
                        permit=permit,
                        expected_length=row.byte_length,
                        expected_sha256=row.sha256,
                    )
                    verified = VerifiedAssetFacts(
                        StoredObject(identity, row.byte_length, row.sha256),
                        "audio/wav",
                        timezone.now(),
                        facts["sample_rate"],
                        facts["channels"],
                        row.frame_count,
                    )
                    result = record_put(
                        job_id=job.id,
                        allocation_id=row.id,
                        allocation_kind="segment",
                        permit=permit,
                        identity=identity,
                        facts=verified,
                    )
                    if result == "stale_unowned":
                        cleanup_stale_put(
                            job_id=job.id,
                            allocation_id=row.id,
                            allocation_kind="segment",
                            permit=permit,
                            identity=identity,
                        )
                        raise LeaseLost
                    row.refresh_from_db()
                rows.append(row)
    manifest = recordings.build_manifest(
        recording_id=target.client_id,
        rate=facts["sample_rate"],
        channels=facts["channels"],
        direction=target.direction,
        track_ids=[
            uuid.uuid5(target.client_id, f"track:{n}") for n in range(facts["channels"])
        ],
        segments=[
            (row.occurrence_client_id, plan)
            for row, plan in zip(rows, plans, strict=True)
        ],
        duration_ms=facts["duration_ms"],
        capture_complete=True,
    )
    return rows, manifest


def _single_snapshot(job, fence, snapshot, digest, storage):
    from tracer.services.attachments import uploads

    with transaction.atomic(), _locked_job(job.id) as (held, _, _, upload, asset):
        _hold(held, fence, timezone.now())
        if held.upload_id is None or upload.id != job.upload_id:
            raise AttachmentError("conflict", 409)
        if upload.quarantine_version is not None:
            if (
                upload.quarantine_sha256 != digest
                or upload.received_bytes != held.source_byte_length
                or upload.expected_bytes != held.source_byte_length
                or upload.state != AttachmentState.VALIDATING
                or upload.reservation_settled_at is not None
                or asset.verified_blob_id is not None
                or upload.allocation_epoch > fence.epoch
            ):
                raise AttachmentError("snapshot_changed", 409)
            # Adopt only the already-recorded snapshot (and serving revision, if
            # recorded). No enumeration result, PUT, or new base reservation is
            # accepted here. Current source/creator authority was checked above.
            if upload.allocation_epoch != fence.epoch:
                upload.allocation_epoch = fence.epoch
                upload.save(update_fields=["allocation_epoch"])
            return
    permit = prepare_put(
        job_id=job.id,
        allocation_id=upload.id,
        allocation_kind="upload",
        fence=fence,
        purpose="quarantine",
        now=timezone.now(),
    )
    snapshot.seek(0)
    identity = storage.put_quarantine(permit, snapshot, job.source_byte_length)
    try:
        uploads.accept_import_snapshot(
            upload_id=upload.id,
            scope_id=job.scope_id,
            identity=identity,
            sha256=digest,
            byte_length=job.source_byte_length,
            fence=fence,
        )
    except Exception:
        # Ownership helper fences accepted snapshots. Retire our epoch before
        # exact cleanup, so this version can never later be adopted.
        _retire(job.id, fence, reason="source_unavailable")
        try:
            cleanup_stale_put(
                job_id=job.id,
                allocation_id=upload.id,
                allocation_kind="upload",
                permit=permit,
                identity=identity,
            )
        except AttachmentError:
            # Current-epoch ambiguity or cleanup failure remains discoverable
            # from the pre-PUT key and is retried by fenced recovery/sweep.
            pass
        raise


def _remote_recheck(job, fence):
    from tracer.services.attachments import readiness, source_providers

    readiness.require_source_keys(
        source=SourceRef(job.source_kind, job.envelope_id or job.provider_recording_id)
    )
    with source_providers.open_source(job_id=job.id, fence=fence) as reader:
        if (
            reader.facts != _facts(job)
            or len(
                reader.read_range(
                    start=job.source_byte_length - 1, end=job.source_byte_length - 1
                )
            )
            != 1
        ):
            raise AttachmentError("snapshot_changed", 409)


def publish(*, job_id, fence, segment_ids, manifest):
    """Remote checks outside locks; local authority and ALL publication in one txn."""
    from tracer.services.attachments import derivatives, publication, validation

    before = AttachmentImportJob.objects.get(pk=job_id)
    with transaction.atomic(), _locked_job(job_id) as (held, _, _, _, _):
        _hold(held, fence, timezone.now())
    _remote_recheck(before, fence)
    verified = None
    if before.upload_id:
        if segment_ids or manifest is not None:
            raise AttachmentError("conflict", 409)
        # Shared bounded validation owns the serving PUT and calls this module's
        # prepare_put/record_put with the same job fence; never publish there.
        verified = validation.validate_import_snapshot(
            upload_id=before.upload_id, scope_id=before.scope_id, fence=fence
        )
        _remote_recheck(before, fence)
    else:
        recordings.validate_manifest(manifest)
    with (
        transaction.atomic(),
        _locked_job(job_id) as (job, scope, session, target, asset),
    ):
        now = timezone.now()
        _hold(job, fence, now)
        job.lease_phase = "publishing"
        job.state = RecordingState.VALIDATING
        job.save(update_fields=["lease_phase", "state"])
        if job.recording_id:
            target.state = RecordingState.VALIDATING
            target.save(update_fields=["state"])
        if job.upload_id:
            publication.publish_verified_locked(
                attachment=asset,
                facts=verified,
                expected_binding_id=job.storage_binding_id,
                scope=scope,
                fence=fence,
                allocation=target,
            )
            target.state = AttachmentState.READY
            target.save(update_fields=["state"])
            from tracer.services.attachments import accounting

            accounting.receipt_locked(
                target, attachment=asset, outcome=AttachmentState.READY
            )
            derivatives.enqueue_for_asset_locked(asset)
        else:
            rows = list(
                AttachmentRecordingSegment.objects.select_for_update()
                .filter(import_job=job, pk__in=segment_ids)
                .order_by("pk")
            )
            rows.sort(key=lambda row: row.index)
            entries = manifest["segments"]
            if (
                len(rows) != len(segment_ids)
                or len(set(segment_ids)) != len(rows)
                or len(rows) != len(entries)
                or not rows
                or AttachmentRecordingSegment.objects.filter(import_job=job).count()
                != len(rows)
                or manifest["recording_id"] != str(target.client_id)
                or manifest["sample_rate_hz"] != target.sample_rate
                or manifest["channels"] != target.channels
                or manifest["duration_ms"] != job.source_duration_ms
                or not manifest["capture_complete"]
                or any(
                    track["direction"] != target.direction
                    for track in manifest["tracks"]
                )
            ):
                raise AttachmentError("validation_failed", 422)
            for row, entry in zip(rows, entries, strict=True):
                if (
                    row.state != RecordingSegmentState.STAGED
                    or row.storage_version is None
                    or row.storage_binding_id != job.storage_binding_id
                    or row.erased_at is not None
                    or (
                        entry["occurrence_id"],
                        entry["index"],
                        entry["start_ms"],
                        entry["duration_ms"],
                    )
                    != (
                        str(row.occurrence_client_id),
                        row.index,
                        row.start_ms,
                        row.duration_ms,
                    )
                ):
                    raise AttachmentError("validation_failed", 422)
                asset = Attachment.objects.create(
                    scope=scope,
                    client_id=row.attachment_client_id,
                    source_kind=IMPORTED_SOURCE_KIND,
                    storage_binding_id=job.storage_binding_id,
                    state=AttachmentState.PENDING,
                    policy_version=scope.policy_version,
                    deletion_generation=scope.deletion_generation,
                )
                asset.state = AttachmentState.VALIDATING
                asset.save(update_fields=["state"])
                # Link the allocation before publishing verified facts; DB fences
                # compare its immutable key/version and current job epoch.
                row.attachment = asset
                row.allocation_epoch = fence.epoch
                row.save(update_fields=["attachment", "allocation_epoch"])
                facts = VerifiedAssetFacts(
                    StoredObject(
                        ObjectIdentity(
                            job.storage_binding_id,
                            "serving",
                            row.storage_key,
                            row.storage_version,
                        ),
                        row.byte_length,
                        row.sha256,
                    ),
                    "audio/wav",
                    now,
                    target.sample_rate,
                    target.channels,
                    row.frame_count,
                )
                publication.publish_verified_locked(
                    attachment=asset,
                    facts=facts,
                    expected_binding_id=job.storage_binding_id,
                    scope=scope,
                    fence=fence,
                    allocation=row,
                )
                AttachmentOccurrence.objects.create(
                    scope=scope,
                    client_id=row.occurrence_client_id,
                    attachment=asset,
                    capture_session=session,
                    trace_id=target.trace_id,
                    span_id=target.span_id,
                    direction=target.direction,
                    recording=target,
                    purpose="conversation_recording",
                    expires_at=min(now + timedelta(hours=24), job.source_expires_at),
                    capture_complete=True,
                    policy_version=scope.policy_version,
                    deletion_generation=scope.deletion_generation,
                )
                row.state = RecordingSegmentState.PUBLISHED
                row.save(update_fields=["state"])
                derivatives.enqueue_for_asset_locked(asset)
            # Keep current manifest AND the immutable old idempotency receipt.
            revision = target.manifest_revision + 1
            AttachmentManifestRevision.objects.create(
                scope=scope,
                recording=target,
                idempotency_key=f"import:{job.id}",
                fingerprint=fingerprint(manifest),
                revision=revision,
            )
            target.manifest, target.manifest_revision = manifest, revision
            target.duration_ms, target.capture_complete = manifest["duration_ms"], True
            target.state, target.published_at = RecordingState.READY, now
            target.save(
                update_fields=[
                    "manifest",
                    "manifest_revision",
                    "duration_ms",
                    "capture_complete",
                    "state",
                    "published_at",
                ]
            )
        job.state, job.reason, job.finished_at = RecordingState.READY, "ready", now
        job.lease_phase, job.lease_token, job.lease_until = "terminal", None, None
        job.save(
            update_fields=[
                "state",
                "reason",
                "finished_at",
                "lease_phase",
                "lease_token",
                "lease_until",
            ]
        )


def _retire(job_id, fence, *, reason, terminal=None):
    with (
        transaction.atomic(),
        _locked_job(job_id, authorize=False) as (job, _, _, target, asset),
    ):
        if (job.lease_token, job.lease_epoch) != (
            fence.token,
            fence.epoch,
        ) or job.state in TERMINAL_RECORDING_STATES:
            return
        _retire_locked(job, target, asset, reason, terminal)


def _terminal_denial(code):
    return {"expired": RecordingState.EXPIRED, "deleted": RecordingState.DELETED}.get(
        code, RecordingState.REJECTED
    )


def _log_deferred(job_id, code):
    logger.warning(
        "attachment_import_attempt_deferred",
        job_id=str(job_id),
        reason=public_reason(code),
    )


def _retire_targets(target, asset, terminal):
    # Both rows are locked by _locked_job_rows. Whole recordings have no asset
    # at this point; a single import owns both an upload and its pending asset.
    for row in (target, asset):
        if row is not None and row.state not in TERMINAL_RECORDING_STATES:
            row.state = terminal
            row.save(update_fields=["state"])


def _retire_locked(job, target, asset, reason, terminal, *, claim_denied=False):
    job.reason = public_reason(reason)
    # The schema deliberately forbids writing->idle. A transient attempt
    # leaves the old lease to expire; only a new epoch may enter recovery.
    # No capability is issued after this execution has left its guard.
    fields = ["reason"]
    if claim_denied and job.attempts < MAX_ATTEMPTS:
        # Revalidation consumed this attempt without issuing a worker lease.
        job.attempts += 1
        fields.append("attempts")
    if terminal is None and (not job.lease_until or job.lease_until <= timezone.now()):
        return
    if terminal:
        job.lease_phase = "terminal"
        job.lease_token, job.lease_until = None, None
        fields += ["lease_phase", "lease_token", "lease_until"]
        job.state, job.finished_at = terminal, timezone.now()
        fields += ["state", "finished_at"]
        _retire_targets(target, asset, terminal)
    job.save(update_fields=fields)


def run(job_id, *, scope_id, storage=None, source_factory=None):
    from tracer.services.attachments import readiness, source_providers, validation

    job = AttachmentImportJob.objects.get(pk=job_id, scope_id=scope_id)
    if job.state in TERMINAL_RECORDING_STATES:
        return job.state
    fence = claim(job_id, scope_id=scope_id, now=timezone.now())
    if fence is None:
        return AttachmentImportJob.objects.values_list("state", flat=True).get(
            pk=job_id
        )
    try:
        with sandbox.import_execution(job.id, fence.epoch) as directory:
            job.refresh_from_db()
            storage = _storage(job, storage, "write")
            _clean_versions(job, storage, fence=fence)
            validation.require_decoder(
                DecoderRequirements(
                    "normalize" if job.recording_id else "validate",
                    job.source_mime_type,
                    job.window_bytes,
                )
            )
            readiness.require_source_keys(
                source=SourceRef(
                    job.source_kind, job.envelope_id or job.provider_recording_id
                )
            )
            with (source_factory or source_providers.open_source)(
                job_id=job.id, fence=fence
            ) as reader:
                with sandbox.scratch_file(directory, "source") as snapshot:
                    digest = _fetch(job, fence, reader, snapshot)
                    if job.upload_id:
                        _single_snapshot(job, fence, snapshot, digest, storage)
                        publish(
                            job_id=job.id, fence=fence, segment_ids=(), manifest=None
                        )
                    else:
                        rows, manifest = _segment_source(
                            job, fence, snapshot, directory, storage
                        )
                        publish(
                            job_id=job.id,
                            fence=fence,
                            segment_ids=tuple(row.id for row in rows),
                            manifest=manifest,
                        )
    except LeaseLost:
        pass
    except Exception as error:  # noqa: BLE001 - durable bounded job boundary
        code = (
            error.code if isinstance(error, AttachmentError) else "source_unavailable"
        )
        transient = not isinstance(error, AttachmentError) or error.status in {429, 503}
        terminal = None
        if not transient or job.attempts >= MAX_ATTEMPTS:
            terminal = RecordingState.FAILED if transient else _terminal_denial(code)
        _retire(job.id, fence, reason=code, terminal=terminal)
        _log_deferred(job.id, code)
    return AttachmentImportJob.objects.values_list("state", flat=True).get(pk=job_id)


def _expire(job_id, now):
    with (
        transaction.atomic(),
        _locked_job(job_id, authorize=False) as (job, _, _, target, asset),
    ):
        if job.state in TERMINAL_RECORDING_STATES:
            return False
        if job.deadline > now and not (
            job.attempts >= MAX_ATTEMPTS
            and (not job.lease_until or job.lease_until <= now)
        ):
            return False
        job.state = (
            RecordingState.EXPIRED if job.deadline <= now else RecordingState.FAILED
        )
        job.reason = "expired" if job.deadline <= now else "source_unavailable"
        job.finished_at, job.lease_phase = now, "terminal"
        job.lease_token, job.lease_until = None, None
        job.save(
            update_fields=[
                "state",
                "reason",
                "finished_at",
                "lease_phase",
                "lease_token",
                "lease_until",
            ]
        )
        _retire_targets(target, asset, job.state)
        return True


def _cleanup(job, now):
    from tracer.services.attachments import capacity_db, readiness, uploads, validation

    if (
        job.state not in TERMINAL_RECORDING_STATES
        or job.cleanup_not_before is None
        or now < job.cleanup_not_before
    ):
        return False
    # Local process locks prove accepted operations have stopped; time alone is
    # NOT proof. Missing epoch guards/shared scratch keep capacity charged.
    if not sandbox.cleanup_import_scratch(job.id, job.lease_epoch):
        return False
    storage = readiness.resolve_historical(job.storage_binding_id, operation="erase")
    if job.upload_id:
        # Upload owns base cleanup, receipt and asset survivor fencing. J cannot
        # settle until that cleanup has reconciled unknown quarantine/serving PUTs.
        upload = AttachmentUpload.objects.get(pk=job.upload_id, scope_id=job.scope_id)
        if upload.state == AttachmentState.READY:
            # Shared reconciliation retains the published serving identity and
            # proves temporary/unknown absence before releasing U, then J.
            if not validation.reconcile_ready_upload(upload, storage):
                return False
        else:
            uploads.settle(job.upload_id, scope_id=job.scope_id, outcome=job.state)
        upload = AttachmentUpload.objects.get(pk=job.upload_id, scope_id=job.scope_id)
        if upload.reservation_settled_at is None:
            return False
        _clean_versions(job, storage)
    elif job.state == RecordingState.READY:
        _clean_versions(job, storage)
    else:
        for kind, before in _allocations(job):
            with (
                transaction.atomic(),
                _locked_job(job.id, authorize=False) as (held, _, _, _, _),
            ):
                if (
                    held.state == RecordingState.READY
                    or held.state not in TERMINAL_RECORDING_STATES
                ):
                    return False
                row = _allocation(held, before.id, kind)
                # Published assets are exclusively erased by unified retention.
                if row.attachment_id or row.state == RecordingSegmentState.PUBLISHED:
                    return False
            for purpose in ("quarantine", "serving"):
                result = storage.erase_all_versions(
                    _locator(job, before, kind, purpose)
                )
                if (
                    not result.empty
                    or result.retryable
                    or result.checked_at < job.cleanup_not_before
                ):
                    return False
            with (
                transaction.atomic(),
                _locked_job(job.id, authorize=False) as (held, _, _, _, _),
            ):
                row = _allocation(held, before.id, kind)
                if row.attachment_id or held.state == RecordingState.READY:
                    return False
                row.state, row.erased_at, row.cleanup_checked_at = (
                    RecordingSegmentState.ERASED,
                    row.erased_at or now,
                    now,
                )
                row.unowned_cleanup_due_at = None
                row.save(
                    update_fields=[
                        "state",
                        "erased_at",
                        "cleanup_checked_at",
                        "unowned_cleanup_due_at",
                    ]
                )
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=job.scope_id, pool_keys=(capacity.IMPORT_POOL_KEY,)
        )
        with _locked_job(job.id, authorize=False) as (held, _, _, _, _):
            if (
                held.state not in TERMINAL_RECORDING_STATES
                or held.cleanup_not_before > now
            ):
                return False
            held.cleanup_checked_at = now
            held.save(update_fields=["cleanup_checked_at"])
            return capacity_db.release_locked(
                locks, owner=_owner(held), cleanup_complete=True, now=now
            )


def sweep(*, limit=100):
    now = timezone.now()
    counts = {"expired": 0, "released": 0, "deferred": 0}
    orphan_jobs = AttachmentRecordingSegment.objects.filter(
        unowned_cleanup_due_at__lte=now
    ).values("import_job_id")
    due = list(
        AttachmentImportJob.objects.filter(
            Q(state__in=(RecordingState.IMPORTING, RecordingState.VALIDATING))
            & (Q(deadline__lte=now) | Q(attempts__gte=MAX_ATTEMPTS))
            | Q(
                state__in=TERMINAL_RECORDING_STATES, reservation_settled_at__isnull=True
            )
            | Q(pk__in=orphan_jobs)
        )
        .order_by("deadline", "id")
        .values_list("id", flat=True)
        .distinct()[:limit]
    )
    for job_id in due:
        try:
            counts["expired"] += int(_expire(job_id, now))
            job = AttachmentImportJob.objects.get(pk=job_id)
            counts["released"] += int(_cleanup(job, now))
        except Exception:  # noqa: BLE001 - one unavailable binding never blocks the next
            counts["deferred"] += 1
            logger.warning("attachment_import_cleanup_deferred", job_id=str(job_id))
    return counts


def pending(*, limit=100):
    now = timezone.now()
    return list(
        AttachmentImportJob.objects.filter(
            Q(lease_until__isnull=True) | Q(lease_until__lte=now),
            state__in=(RecordingState.IMPORTING, RecordingState.VALIDATING),
            deadline__gt=now,
            attempts__lt=MAX_ATTEMPTS,
        )
        .order_by("deadline", "id")
        .values_list("id", "scope_id")[:limit]
    )
