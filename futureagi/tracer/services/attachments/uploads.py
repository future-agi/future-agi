"""Bounded authenticated transfer primitives; no direct presigns or multipart.

``initialize`` authenticates the eligible original identity plus an F01
``Association``; every later step uses the upload-bound write capability.
HTTP adapters enforce body/header read deadlines before handing chunks to this
service. Completion queues validation and cannot make any occurrence ready.

Backend requirements implemented here (SDK design section 11):
- BR-1: replaying ``POST .../uploads`` with the same key and identical body
  for a live pending upload returns the same identifiers, the remaining
  reservation and a freshly minted capability with a later expiry. The upload
  job has its own fixed lifetime (``UPLOAD_SECONDS`` from admission), separate
  from its 300 s capabilities, so a capability that expired mid-transfer can be
  renewed (SDK design 3.2(b)) until the job's own horizon.
- BR-3: ``PUT .../content`` may be replayed in full until ``complete``. A
  partial transfer is discarded. After a snapshot is persisted, a byte-identical
  replay succeeds and any other body conflicts (409).
- BR-4: a recording ``segment_index`` is reusable once the earlier upload for
  it is terminal (aborted, failed, rejected or expired).
"""

import hashlib
import tempfile
import time
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import OrgApiKey, User
from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentRecording,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    AttachmentWriteGrant,
    CaptureSession,
)
from tracer.models.attachment_recording import (
    AttachmentImportJob,
)
from tracer.services.attachments import accounting, capacity, capacity_db
from tracer.services.attachments.bootstrap import (
    CAPABILITY_SECONDS,
    LIMITS,
    authorize,
    capability,
    check_session,
    check_writer,
    find_session,
    session_policy,
)
from tracer.services.attachments.contract import (
    PLACEMENT_FIELDS,
    SEGMENT_MIME_TYPES,
    SERVER_MODALITIES,
    UPLOAD_SOURCES,
    AttachmentError,
    admit_audio,
    fingerprint,
    span_id,
    trace_uuid,
)
from tracer.services.attachments.interfaces import (
    ObjectLocator,
    PutPermit,
    ReservationOwner,
    ResourceVector,
)
from tracer.services.attachments.publication import check_import_fence

CHUNK_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = LIMITS["max_upload_bytes"]
TRANSFER_SECONDS = LIMITS["transfer_timeout_seconds"]
# Lifetime of one upload job and its reservation, fixed at admission and never
# extended. Capabilities are CAPABILITY_SECONDS slices of it. The SDK renews at
# most 3 times per upload (SDK design 3.2), so this outlives its useful range.
UPLOAD_SECONDS = 30 * 60
LIVE_STATES = (
    AttachmentState.PENDING,
    AttachmentState.VALIDATING,
    AttachmentState.READY,
)
# Compatibility entry point for lifecycle callers; all producer pools, not only
# upload shards. The returned transaction handle is accepted by capacity_db.
GLOBAL_SHARDS = capacity.GLOBAL_SHARDS
global_shard = capacity.global_shard


def lock_all_budgets(scope):
    return capacity_db.lock_capacity(
        scope_id=scope.id, pool_keys=capacity.GLOBAL_POOL_KEYS
    )


def _count_attempt(scope, shard):
    return capacity_db.count_attempt(
        scope_id=scope.id, pool_key=f"global:{shard}", now=timezone.now()
    )


def _capability_expiry(session):
    return min(
        session.expires_at, timezone.now() + timedelta(seconds=CAPABILITY_SECONDS)
    )


def upload_response(upload, token, expires_at):
    """F01 ``UploadResponse``. Never log, cache or export this body."""
    return {
        "upload_id": str(upload.id),
        "attachment_id": str(upload.attachment.client_id),
        "occurrence_id": str(upload.occurrence.client_id),
        "state": "pending_upload",
        "reserved_bytes": upload.expected_bytes,
        "expires_at": expires_at.isoformat(),
        "transfer_mode": "authenticated_stream",
        "write_capability": token,
    }


def _check_generation(scope, body):
    if (
        body["policy_version"] != str(scope.policy_version)
        or body["deletion_generation"] != scope.deletion_generation
    ):
        raise AttachmentError("expired", 409)


def _locked_session(session):
    """Creator rows precede CaptureSession; revocation serializes publication."""
    user_ids, grants, key = set(), [], None
    if session.writer_kind == "user":
        user_ids.add(session.writer_id)
    elif session.writer_kind == "api_key":
        key = OrgApiKey.no_workspace_objects.get(pk=session.writer_id)
        grants = list(
            AttachmentWriteGrant.objects.filter(
                scope_id=session.scope_id, api_key_id=key.id
            ).order_by("id")
        )
        user_ids.update(value for value in (key.user_id, key.minted_by_id) if value)
        user_ids.update(grant.granted_by_id for grant in grants if grant.granted_by_id)
    else:
        raise AttachmentError("permission_denied", 403)
    list(User.objects.select_for_update().filter(pk__in=user_ids).order_by("id"))
    if key is not None:
        locked_key = OrgApiKey.no_workspace_objects.select_for_update().get(pk=key.id)
        if (
            locked_key.expires_at is not None
            and locked_key.expires_at <= timezone.now()
        ):
            raise AttachmentError("permission_denied", 403)
        locked_grants = list(
            AttachmentWriteGrant.objects.select_for_update()
            .filter(scope_id=session.scope_id, api_key_id=key.id)
            .order_by("id")
        )
        if (locked_key.user_id, locked_key.minted_by_id) != (
            key.user_id,
            key.minted_by_id,
        ) or [(g.id, g.granted_by_id) for g in locked_grants] != [
            (g.id, g.granted_by_id) for g in grants
        ]:
            raise AttachmentError(
                "transfer_conflict", 409
            )  # discover again, never lock backwards
    session = CaptureSession.objects.select_for_update(no_key=True).get(
        pk=session.pk, scope_id=session.scope_id
    )
    check_session(session)
    return session


def _source_rows(job):
    """Discover ancestry before taking connection -> envelope -> registration."""
    from tracer.services.attachments.interfaces import SourceRef
    from tracer.services.attachments.source_providers import lock_source

    return lock_source(
        scope_id=job.scope_id,
        source=SourceRef(job.source_kind, job.envelope_id or job.provider_recording_id),
    )


def _check_source(job, session, rows):
    now = timezone.now()
    if (
        job.capture_session_id != session.id
        or (job.importer_kind, job.importer_id)
        != (session.writer_kind, session.writer_id)
        or (job.source_creator_kind, job.source_creator_id)
        != (session.writer_kind, session.writer_id)
        or job.source_expires_at is None
        or job.source_expires_at <= now
        or job.deadline > min(job.source_expires_at, session.expires_at)
        or job.policy_version != session.policy_version
        or job.deletion_generation != session.deletion_generation
    ):
        raise AttachmentError("source_unavailable", 403)
    # Uses ordinary reads of rows already locked above; never locks a connection
    # after the Job/Upload. Includes sealed capture provenance and alias ancestry.
    # Envelope and provider authority generations are independent frozen values:
    # the envelope compares to admitted facts; the provider compares to connection.
    from tracer.services.attachments import source_providers

    source_providers.revalidate(
        facts=source_providers._job_facts(job),
        scope_id=job.scope_id,
        importer=SimpleNamespace(kind=job.importer_kind, id=job.importer_id),
        capture_session_id=session.id,
    )


def _lock_upload(ref, *, authority=True, scope=None):
    """Scope -> creator/session/source -> Job -> Recording -> Upload -> asset/link.

    Capacity-changing callers acquire their handle first. Terminal cleanup may
    use authority=False; revocation cannot prevent receipts or physical cleanup.
    """
    scope = scope or AttachmentScope.objects.select_for_update().get(pk=ref.scope_id)
    session = CaptureSession.objects.get(pk=ref.capture_session_id, scope_id=scope.id)
    job_ref = (
        AttachmentImportJob.objects.filter(upload_id=ref.id, scope_id=scope.id).first()
        if ref.producer_kind == "import"
        else None
    )
    if authority:
        session = _locked_session(session)
        source_rows = _source_rows(job_ref) if job_ref is not None else None
    job = (
        AttachmentImportJob.objects.select_for_update().get(
            pk=job_ref.id, scope_id=scope.id
        )
        if job_ref is not None
        else None
    )
    if authority and job is not None:
        _check_source(job, session, source_rows)
    if ref.recording_id:
        AttachmentRecording.objects.select_for_update().get(
            pk=ref.recording_id, scope_id=scope.id
        )
    upload = AttachmentUpload.objects.select_for_update().get(
        pk=ref.id, scope_id=scope.id
    )
    attachment = Attachment.objects.select_for_update().get(
        pk=upload.attachment_id, scope_id=scope.id
    )
    occurrence = AttachmentOccurrence.objects.select_for_update().get(
        pk=upload.occurrence_id, scope_id=scope.id
    )
    upload.capture_session, upload.attachment, upload.occurrence, upload.scope = (
        session,
        attachment,
        occurrence,
        scope,
    )
    if (
        upload.capture_session_id != ref.capture_session_id
        or upload.recording_id != ref.recording_id
        or (upload.producer_kind == "import" and job is None)
    ):
        raise AttachmentError("validation_fenced", 409)
    return scope, session, job, upload, attachment, occurrence


def _renew(upload, session):
    """BR-1: same job, same reservation, fresh capability and later expiry.

    Works whether or not the previous capability has expired: the job stays
    renewable while it is pending, its reservation is unsettled and neither the
    occurrence nor the job's own horizon has passed. The horizon is never moved.
    """
    if upload.state != AttachmentState.PENDING:
        # UploadResponse.state is pending_upload by contract; a completed job is
        # not re-issued. Its existing capability stays valid until it expires.
        raise AttachmentError("idempotency_conflict", 409)
    occurrence = upload.occurrence
    now = timezone.now()
    if (
        occurrence.deleted_at
        or occurrence.expires_at <= now
        or upload.expires_at <= now
        or upload.reservation_settled_at is not None
        or upload.producer_kind != "authenticated_stream"
    ):
        raise AttachmentError("expired", 409)
    expires_at = min(_capability_expiry(session), upload.expires_at)
    return upload, capability(session, upload, expires_at=expires_at), expires_at


def placement(body):
    """The placement fields present in ``body``; absent ones stay NULL."""
    return {name: body[name] for name in PLACEMENT_FIELDS if body.get(name) is not None}


def initialize(
    writer,
    *,
    scope_id,
    body,
    key,
    binding_id=None,
    producer_kind="authenticated_stream",
):
    """F01 ``initializeAttachmentUpload``; ``body`` is a validated UploadRequest."""
    if (
        producer_kind != "authenticated_stream"
        or body["source_kind"] not in UPLOAD_SOURCES
    ):
        raise AttachmentError("permission_denied", 403)
    scope = AttachmentScope.objects.get(pk=scope_id)
    check_writer(writer, scope)
    session = find_session(writer, scope, body["association"])
    scope = check_session(session)
    _check_generation(scope, body)
    parent = body["association"]["parent"]
    trace_id, parent_span_id = (
        trace_uuid(parent["trace_id"]),
        span_id(parent["span_id"]),
    )
    recording_id = body.get("recording_id")
    # Admission before hashing any request metadata, allocating or accessing bytes.
    admit_audio(
        scope,
        session_policy(session),
        direction=body["direction"],
        source_kind=body["source_kind"],
        creates_recording=recording_id is not None,
        modality=body["modality"],
    )
    if body["mime_type"] not in SERVER_MODALITIES.get(body["modality"], ()) or (
        recording_id is not None and body["mime_type"] not in SEGMENT_MIME_TYPES
    ):
        raise AttachmentError("unsupported_media", 403)
    expected_bytes = body["declared_bytes"]
    if not 0 < expected_bytes <= MAX_UPLOAD_BYTES:
        raise AttachmentError("too_large", 413)
    digest = fingerprint({"session": session.id, "body": body})
    existing = AttachmentUpload.objects.filter(
        scope=scope, capture_session=session, idempotency_key=key
    ).first()
    if existing is not None:
        if existing.request_fingerprint != digest:
            raise AttachmentError("idempotency_conflict", 409)
        with transaction.atomic():
            _, session, _, locked, _, _ = _lock_upload(existing)
            return _renew(locked, session)
    # Allocated before admission: the id picks the global budget shard, and
    # settle() releases the same shard from it.
    upload_id = uuid4()
    shard = global_shard(upload_id)
    if not _count_attempt(scope, shard):
        raise AttachmentError("throttled", 429)
    from tracer.services.attachments.interfaces import DecoderRequirements
    from tracer.services.attachments.readiness import require_new_admission
    from tracer.services.attachments.validation import require_decoder

    require_decoder(DecoderRequirements("validate", body["mime_type"], expected_bytes))
    if binding_id is None:
        binding_id = require_new_admission(operation="upload")
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=scope.id, pool_keys=(capacity.upload_pool_key(upload_id),)
        )
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        session = _locked_session(session)
        check_session(session)
        _check_generation(scope, body)
        existing = AttachmentUpload.objects.filter(
            scope=scope, capture_session=session, idempotency_key=key
        ).first()
        if existing is not None:
            if existing.request_fingerprint != digest:
                raise AttachmentError("idempotency_conflict", 409)
            return _renew(existing, session)
        if (
            Attachment.objects.filter(
                scope=scope, client_id=UUID(body["attachment_id"])
            ).exists()
            or AttachmentOccurrence.objects.filter(
                scope=scope, client_id=UUID(body["occurrence_id"])
            ).exists()
        ):
            raise AttachmentError("idempotency_conflict", 409)
        recording = None
        if recording_id is not None:
            recording = _recording_for(
                scope, session, UUID(recording_id), trace_id, parent_span_id, body
            )
            if AttachmentUpload.objects.filter(
                scope=scope,
                recording=recording,
                segment_index=body["segment_index"],
                state__in=LIVE_STATES,
            ).exists():
                raise AttachmentError("segment_conflict", 409)
        common = {
            "scope": scope,
            "policy_version": scope.policy_version,
            "deletion_generation": scope.deletion_generation,
        }
        now = timezone.now()
        # Unbound deadline only; bind_retention replaces it with the source's.
        occurrence_expiry = now + timedelta(hours=24)
        horizon = min(occurrence_expiry, now + timedelta(seconds=UPLOAD_SECONDS))
        expiry = min(_capability_expiry(session), horizon)
        try:
            with transaction.atomic():
                attachment = Attachment.objects.create(
                    **common,
                    storage_binding_id=binding_id,
                    client_id=UUID(body["attachment_id"]),
                    modality=body["modality"],
                    mime_type=body["mime_type"],
                    source_kind=body["source_kind"],
                )
                occurrence = AttachmentOccurrence.objects.create(
                    **common,
                    client_id=UUID(body["occurrence_id"]),
                    attachment=attachment,
                    capture_session=session,
                    trace_id=trace_id,
                    span_id=parent_span_id,
                    direction=body["direction"],
                    purpose=body.get("purpose", ""),
                    recording=recording,
                    expires_at=occurrence_expiry,
                    **placement(body),
                )
                upload = AttachmentUpload.objects.create(
                    **common,
                    id=upload_id,
                    capture_session=session,
                    attachment=attachment,
                    occurrence=occurrence,
                    idempotency_key=key,
                    request_fingerprint=digest,
                    expected_bytes=expected_bytes,
                    mime_type=body["mime_type"],
                    recording=recording,
                    segment_index=body.get("segment_index"),
                    expires_at=horizon,
                    producer_kind="authenticated_stream",
                    storage_object_id=upload_id,
                    quarantine_key=f"quarantine/{upload_id}",
                    serving_key=f"original/{upload_id}",
                    cleanup_not_before=horizon + timedelta(seconds=15),
                    admission_event_id=uuid4(),
                    **capacity_db.reservation_fields(
                        pool_key=capacity.upload_pool_key(upload_id),
                        expires_at=horizon,
                        reserved_bytes=expected_bytes,
                        decoded_bytes=MAX_UPLOAD_BYTES,
                        object_count=4,
                        part_count=1,
                        now=now,
                    ),
                )
        except IntegrityError:
            raise AttachmentError("idempotency_conflict", 409) from None
        capacity_db.reserve_locked(
            locks,
            owner=ReservationOwner("upload", upload.id, scope.id),
            amount=capacity.ordinary_upload_peak(
                expected_bytes=expected_bytes, decoder_scratch_bytes=MAX_UPLOAD_BYTES
            ),
            expires_at=horizon,
        )
        accounting.admit_locked(upload)
        return upload, capability(session, upload, expires_at=expiry), expiry


def _recording_for(scope, session, recording_id, trace_id, parent_span_id, body):
    """A recording belongs to one session and parent; it is never relinked."""
    recording, created = AttachmentRecording.objects.get_or_create(
        scope=scope,
        client_id=recording_id,
        defaults={
            "capture_session": session,
            "trace_id": trace_id,
            "span_id": parent_span_id,
            "capture_session_client_id": session.client_id,
            "correlation_nonce": session.correlation_nonce,
            "direction": body["direction"],
            "policy_version": scope.policy_version,
            "deletion_generation": scope.deletion_generation,
        },
    )
    recording = AttachmentRecording.objects.select_for_update().get(
        pk=recording.id, scope=scope
    )
    if not created and (
        recording.capture_session_id != session.id
        or (recording.trace_id, recording.span_id) != (trace_id, parent_span_id)
        or recording.deleted_at
        or recording.deletion_generation != scope.deletion_generation
    ):
        raise AttachmentError("idempotency_conflict", 409)
    return recording


def _live_upload(token, scope_id, upload_id, operation, *, states=None):
    session, _ = authorize(
        token, operation=operation, scope_id=scope_id, upload_id=upload_id
    )
    upload = (
        AttachmentUpload.objects.select_related("occurrence", "attachment")
        .filter(pk=upload_id, scope_id=scope_id, capture_session=session)
        .first()
    )
    if upload is None or upload.producer_kind != "authenticated_stream":
        raise AttachmentError("not_found", 404)
    now = timezone.now()
    occurrence = upload.occurrence
    if (
        upload.deletion_generation != session.deletion_generation
        or upload.expires_at <= now
        or upload.attachment.deleted_at is not None
        or occurrence.deleted_at
        or occurrence.expires_at <= now
        or (
            occurrence.source_expires_at is not None
            and occurrence.source_expires_at <= now
        )
    ):
        raise AttachmentError("deleted", 409)
    if states is not None and upload.state not in states:
        raise AttachmentError("transfer_conflict", 409)
    admit_audio(
        check_session(session),
        session_policy(session),
        direction=occurrence.direction,
        source_kind=upload.attachment.source_kind,
        creates_recording=upload.recording_id is not None,
        modality=upload.attachment.modality,
    )
    return upload


def status_document(upload):
    """F01 ``StatusResponse``: content-free state and reason only."""
    reasons = {
        AttachmentState.PENDING: "queued",
        AttachmentState.VALIDATING: "queued",
        AttachmentState.READY: "ready",
        AttachmentState.FAILED: "validation_failed",
        AttachmentState.REJECTED: "validation_failed",
        AttachmentState.EXPIRED: "expired",
        AttachmentState.DELETED: "deleted",
    }
    return {
        "upload_id": str(upload.id),
        "state": upload.state,
        "reason": reasons[upload.state],
    }


def _storage_for(upload, storage=None, *, operation="write"):
    from tracer.services.attachments.storage import for_binding

    binding_id = upload.attachment.storage_binding_id
    if binding_id is None:
        raise AttachmentError("storage_unverified", 503)
    storage = storage or for_binding(binding_id, operation=operation)
    if storage.binding_id != binding_id:
        raise AttachmentError("storage_unverified", 503)
    storage.verify_identity(operation=operation)
    return storage


def _same_identity(upload, identity):
    if identity.binding_id != upload.attachment.storage_binding_id:
        return False
    return (identity.purpose, identity.key, identity.version) in {
        ("quarantine", upload.quarantine_key, upload.quarantine_version),
        ("serving", upload.serving_key, upload.serving_version),
        (
            "serving",
            upload.attachment.verified_storage_key,
            upload.attachment.verified_storage_version,
        ),
    }


def cleanup_unowned_result(upload_id, *, scope_id, identity, storage):
    """Synchronized reference check. Unknown returned versions are never adopted."""
    ref = AttachmentUpload.objects.get(pk=upload_id, scope_id=scope_id)
    with transaction.atomic():
        _, _, _, upload, _, _ = _lock_upload(ref, authority=False)
        if _same_identity(upload, identity):
            return "already_owned"
        if (
            identity.binding_id != upload.attachment.storage_binding_id
            or identity.key
            != (
                upload.quarantine_key
                if identity.purpose == "quarantine"
                else upload.serving_key
            )
        ):
            raise AttachmentError("storage_unverified", 503)
    storage.erase_version(identity)  # exact returned version, NEVER an entire key
    return "stale_unowned"


def _recover_key(upload, *, storage, purpose, guard):
    """Only before a new permit; a callback rechecks the current fenced owner."""
    key = upload.quarantine_key if purpose == "quarantine" else upload.serving_key
    # An empty enumeration is NOT evidence that an accepted PUT has stopped.
    # Check before listing as well as per candidate, even when the page is empty.
    with transaction.atomic():
        locked = guard()
        if (
            locked.write_deadline_at
            and timezone.now() <= locked.write_deadline_at + timedelta(seconds=15)
        ):
            raise AttachmentError("transfer_conflict", 409)
    cursor = None
    for _ in range(100):
        page = storage.list_versions(
            ObjectLocator(storage.binding_id, purpose, key), cursor=cursor, limit=100
        )
        for identity in page.versions + page.delete_markers:
            with transaction.atomic():
                locked = guard()
                if (
                    locked.write_deadline_at
                    and timezone.now()
                    <= locked.write_deadline_at + timedelta(seconds=15)
                ):
                    raise AttachmentError("transfer_conflict", 409)
                owned = _same_identity(locked, identity)
            if not owned:
                cleanup_unowned_result(
                    upload.id,
                    scope_id=upload.scope_id,
                    identity=identity,
                    storage=storage,
                )
        if page.next_cursor is None:
            with transaction.atomic():
                guard()
            return
        cursor = page.next_cursor
    raise AttachmentError("cleanup_unavailable", 503)


def transfer(token, *, scope_id, upload_id, chunks, storage, content_length=None):
    upload = _live_upload(
        token, scope_id, upload_id, "upload", states=(AttachmentState.PENDING,)
    )
    storage = _storage_for(upload, storage)
    if content_length is not None and content_length != upload.expected_bytes:
        raise AttachmentError("length_mismatch")
    if (
        upload.quarantine_write_attempts
        and not upload.quarantine_version
        and not capacity_db.count_attempt(
            scope_id=scope_id, pool_key=upload.reservation_pool_key, now=timezone.now()
        )
    ):
        raise AttachmentError("throttled", 429)
    with transaction.atomic():
        _, _, _, locked, _, _ = _lock_upload(upload)
        _live_upload(
            token, scope_id, upload_id, "upload", states=(AttachmentState.PENDING,)
        )
        replay = bool(locked.quarantine_version)
        if not replay:
            if (
                locked.transfer_started_at
                and timezone.now()
                < locked.transfer_started_at + timedelta(seconds=TRANSFER_SECONDS + 15)
            ):
                raise AttachmentError("transfer_conflict", 409)
            locked.transfer_started_at = timezone.now()
            locked.allocation_epoch += 1
            locked.save(update_fields=["transfer_started_at", "allocation_epoch"])
        started, epoch = locked.transfer_started_at, locked.allocation_epoch

    def guard():
        _, _, _, current, _, _ = _lock_upload(upload)
        _live_upload(
            token, scope_id, upload_id, "upload", states=(AttachmentState.PENDING,)
        )
        if (
            current.allocation_epoch != epoch
            or current.transfer_started_at != started
            or current.quarantine_version
        ):
            raise AttachmentError("transfer_conflict", 409)
        return current

    began, size, hasher, identity = time.monotonic(), 0, hashlib.sha256(), None
    try:
        with tempfile.TemporaryFile() as snapshot:
            for chunk in chunks:
                _live_upload(token, scope_id, upload_id, "upload")
                if time.monotonic() - began > TRANSFER_SECONDS:
                    raise AttachmentError("transfer_timeout", 408)
                if not isinstance(chunk, bytes) or len(chunk) > CHUNK_BYTES:
                    raise AttachmentError("invalid_chunk")
                size += len(chunk)
                if size > upload.expected_bytes:
                    raise AttachmentError("too_large", 413)
                hasher.update(chunk)
                if not replay:
                    snapshot.write(chunk)
            if size != upload.expected_bytes:
                raise AttachmentError("length_mismatch")
            if replay:
                if hasher.hexdigest() != locked.quarantine_sha256:
                    raise AttachmentError("transfer_conflict", 409)
                return status_document(
                    _live_upload(token, scope_id, upload_id, "upload")
                )
            if locked.quarantine_write_attempts:
                _recover_key(upload, storage=storage, purpose="quarantine", guard=guard)
            with transaction.atomic():
                locks = capacity_db.lock_capacity(
                    scope_id=scope_id, pool_keys=(upload.reservation_pool_key,)
                )
                current = guard()
                if current.quarantine_write_attempts >= 3:
                    raise AttachmentError("transfer_conflict", 409)
                if current.quarantine_write_attempts:
                    capacity_db.resize_locked(
                        locks,
                        owner=ReservationOwner("upload", upload_id, scope_id),
                        amount=capacity_db.owner_vector(current)
                        + ResourceVector(upload.expected_bytes, 0, 0, 0, 1, 0),
                    )
                current.quarantine_write_attempts += 1
                current.write_deadline_at = min(
                    started + timedelta(seconds=TRANSFER_SECONDS), current.expires_at
                )
                if current.write_deadline_at <= timezone.now():
                    raise AttachmentError("transfer_timeout", 408)
                current.cleanup_not_before = max(
                    current.cleanup_not_before,
                    current.write_deadline_at + timedelta(seconds=15),
                )
                current.unowned_cleanup_due_at = current.cleanup_not_before
                current.save(
                    update_fields=[
                        "quarantine_write_attempts",
                        "write_deadline_at",
                        "cleanup_not_before",
                        "unowned_cleanup_due_at",
                    ]
                )
                permit = PutPermit(
                    "upload",
                    current.id,
                    ObjectLocator(
                        storage.binding_id, "quarantine", current.quarantine_key
                    ),
                    None,
                    current.quarantine_write_attempts,
                    current.write_deadline_at,
                )
            snapshot.seek(0)
            identity = storage.put_quarantine(permit, snapshot, size)
        with transaction.atomic():
            current = guard()
            if (identity.binding_id, identity.purpose, identity.key) != (
                storage.binding_id,
                "quarantine",
                current.quarantine_key,
            ):
                raise AttachmentError("snapshot_changed", 409)
            current.quarantine_version, current.quarantine_sha256 = (
                identity.version,
                hasher.hexdigest(),
            )
            current.received_bytes, current.transfer_started_at = size, None
            current.save(
                update_fields=[
                    "quarantine_version",
                    "quarantine_sha256",
                    "received_bytes",
                    "transfer_started_at",
                ]
            )
            return status_document(current)
    except Exception as exc:
        if identity is not None:
            cleanup_unowned_result(
                upload_id, scope_id=scope_id, identity=identity, storage=storage
            )
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("transfer_failed", 503) from None
    finally:
        if not replay:
            AttachmentUpload.objects.filter(
                pk=upload_id,
                scope_id=scope_id,
                transfer_started_at=started,
                allocation_epoch=epoch,
            ).update(transfer_started_at=None)


def _check_request(upload, body):
    if body is not None and (
        body["policy_version"] != str(upload.policy_version)
        or body["deletion_generation"] != upload.deletion_generation
    ):
        raise AttachmentError("expired", 409)


@transaction.atomic
def complete(token, *, scope_id, upload_id, body=None):
    upload = _live_upload(token, scope_id, upload_id, "complete")
    _check_request(upload, body)
    scope, _, _, upload, _, _ = _lock_upload(upload)
    _live_upload(token, scope_id, upload_id, "complete")
    if upload.state in {AttachmentState.VALIDATING, AttachmentState.READY}:
        return upload
    if (
        upload.state != AttachmentState.PENDING
        or not upload.quarantine_version
        or upload.received_bytes != upload.expected_bytes
    ):
        raise AttachmentError("incomplete_upload", 409)
    upload.state = AttachmentState.VALIDATING
    upload.save(update_fields=["state"])
    Attachment.objects.filter(pk=upload.attachment_id, scope=scope).update(
        state=AttachmentState.VALIDATING
    )
    AttachmentOutbox.objects.get_or_create(
        scope=scope,
        upload=upload,
        event="validate",
        generation=scope.deletion_generation,
        defaults={
            "available_at": timezone.now(),
            "attachment_id": upload.attachment_id,
        },
    )
    return upload


@transaction.atomic
def settle(upload_id, *, scope_id, outcome):
    if outcome not in accounting.TERMINAL:
        raise ValueError("terminal outcome required")
    ref = AttachmentUpload.objects.get(pk=upload_id, scope_id=scope_id)
    locks = capacity_db.lock_capacity(
        scope_id=scope_id, pool_keys=(ref.reservation_pool_key,)
    )
    scope, _, _, upload, attachment, _ = _lock_upload(ref, authority=False)
    now = timezone.now()
    if attachment.verified_blob_id is not None:
        outcome = AttachmentState.READY
    if outcome == AttachmentState.READY and attachment.verified_blob_id is None:
        raise AttachmentError("not_validated", 409)
    if upload.state not in accounting.TERMINAL:
        upload.state = outcome
        upload.save(update_fields=["state"])
        if attachment.state not in accounting.TERMINAL:
            attachment.state = outcome
            attachment.save(update_fields=["state"])
    # Terminal operational state never changes on replay, and usage remains the
    # original verified outcome even after deletion. Cleanup is independent.
    changed = accounting.receipt_locked(
        upload, attachment=attachment, outcome=upload.state
    )
    if outcome != AttachmentState.READY:
        AttachmentOutbox.objects.get_or_create(
            scope=scope,
            upload=upload,
            event="erase",
            generation=upload.deletion_generation,
            defaults={
                "available_at": max(now, upload.cleanup_not_before),
                "attachment_id": attachment.id,
            },
        )
    released = capacity_db.release_locked(
        locks,
        owner=ReservationOwner("upload", upload.id, scope.id),
        cleanup_complete=upload.cleanup_checked_at is not None,
        now=now,
    )
    return changed or released


def abort(token, *, scope_id, upload_id, body=None):
    """Abort only pending work, rechecking after the same ordered mutation locks.

    Complete/publication can win the race, but must never be undone by an abort
    that read PENDING before waiting for those locks.
    """
    ref = _live_upload(token, scope_id, upload_id, "abort")
    _check_request(ref, body)
    with transaction.atomic():
        capacity_db.lock_capacity(
            scope_id=scope_id, pool_keys=(ref.reservation_pool_key,)
        )
        _, _, _, upload, _, _ = _lock_upload(ref)
        _live_upload(token, scope_id, upload_id, "abort")
        if upload.state == AttachmentState.DELETED:
            return upload
        if upload.state != AttachmentState.PENDING:
            raise AttachmentError("transfer_conflict", 409)
        settle(upload_id, scope_id=scope_id, outcome=AttachmentState.DELETED)
    return AttachmentUpload.objects.get(pk=upload_id, scope_id=scope_id)


def status(token, *, scope_id, upload_id):
    session, _ = authorize(
        token, operation="status", scope_id=scope_id, upload_id=upload_id
    )
    upload = AttachmentUpload.objects.filter(
        pk=upload_id, scope_id=scope_id, capture_session=session
    ).first()
    if upload is None:
        raise AttachmentError("not_found", 404)
    # No source, blob, filename, hash or parent metadata.
    return status_document(upload)


def initialize_import_locked(*, scope, capture_session, body, job, binding_id, locks):
    """Create the worker-only target, with ZERO live charge and no capability.

    Admission owns the surrounding transaction and original creator/source locks.
    A not-yet-inserted Job is required (its FK needs this Upload first). Caller
    inserts that Job, reserves U+J once and transfers U before committing. Keeping
    reservation in the caller lets its trusted reader declare E without guessing
    an undocumented body field here. No client-stream initializer is called.
    """
    capacity_db._check(locks, scope.id)
    now = timezone.now()
    if (
        not job._state.adding
        or job.recording_id is not None
        or job.scope_id != scope.id
        or job.capture_session_id != capture_session.id
        or job.storage_binding_id != binding_id
        or (job.importer_kind, job.importer_id)
        != (capture_session.writer_kind, capture_session.writer_id)
        or (job.source_creator_kind, job.source_creator_id)
        != (job.importer_kind, job.importer_id)
        or job.deadline > min(job.source_expires_at, capture_session.expires_at)
        or job.deadline <= now
        or job.reservation_pool_key != capacity.IMPORT_POOL_KEY
    ):
        raise AttachmentError("source_unavailable", 403)
    check_session(capture_session)
    if not scope.allow_import or not session_policy(capture_session).allow_import:
        raise AttachmentError("policy_denied", 403)
    expected_kind = (
        "RemoteUrl" if job.source_kind == "RemoteUrlEnvelope" else "ProviderFile"
    )
    if body["source_kind"] != expected_kind:
        raise AttachmentError("source_unavailable", 403)
    parent = body["association"]["parent"]
    if (
        trace_uuid(parent["trace_id"]),
        span_id(parent["span_id"]),
        body["direction"],
    ) != (job.source_trace_id, job.source_span_id, job.source_direction):
        raise AttachmentError("source_unavailable", 403)
    if (
        body["declared_bytes"] != job.source_byte_length
        or body["mime_type"] != job.source_mime_type
        or type(job.source_byte_length) is not int
        or not 0 < job.source_byte_length <= MAX_UPLOAD_BYTES
    ):
        raise AttachmentError("snapshot_changed", 409)
    _check_generation(scope, body)
    admit_audio(
        scope,
        session_policy(capture_session),
        direction=body["direction"],
        source_kind="imported_recording",
        creates_recording=False,
        modality=body["modality"],
    )
    if body["mime_type"] not in SERVER_MODALITIES.get(body["modality"], ()):
        raise AttachmentError("unsupported_media", 403)
    upload_id = job.upload_id or uuid4()
    common = {
        "scope": scope,
        "policy_version": scope.policy_version,
        "deletion_generation": scope.deletion_generation,
    }
    attachment = Attachment.objects.create(
        **common,
        client_id=UUID(body["attachment_id"]),
        storage_binding_id=binding_id,
        modality=body["modality"],
        mime_type=body["mime_type"],
        source_kind="imported_recording",
    )
    occurrence = AttachmentOccurrence.objects.create(
        **common,
        client_id=UUID(body["occurrence_id"]),
        attachment=attachment,
        capture_session=capture_session,
        trace_id=job.source_trace_id,
        span_id=job.source_span_id,
        direction=job.source_direction,
        purpose=body.get("purpose", ""),
        expires_at=job.source_expires_at,
        **placement(body),
    )
    upload = AttachmentUpload.objects.create(
        **common,
        id=upload_id,
        capture_session=capture_session,
        attachment=attachment,
        occurrence=occurrence,
        idempotency_key=f"import:{job.id}",
        request_fingerprint=job.request_fingerprint,
        expected_bytes=job.source_byte_length,
        mime_type=job.source_mime_type,
        expires_at=job.deadline,
        producer_kind="import",
        storage_object_id=upload_id,
        quarantine_key=f"quarantine/{upload_id}",
        serving_key=f"original/{upload_id}",
        cleanup_not_before=job.deadline + timedelta(seconds=15),
        admission_event_id=uuid4(),
        **capacity_db.reservation_fields(
            pool_key=capacity.IMPORT_POOL_KEY,
            expires_at=job.deadline,
            reserved_bytes=job.source_byte_length,
            decoded_bytes=job.reservation_decoded_bytes,
            object_count=4,
            part_count=1,
            now=now,
        ),
    )
    accounting.admit_locked(upload)
    return upload


def accept_import_snapshot(
    *, upload_id, scope_id, identity, sha256, byte_length, fence
):
    # Validate scalar digest/length with the shared value, not permissive strings.
    from tracer.services.attachments.interfaces import StoredObject

    StoredObject(identity, byte_length, sha256)
    ref = AttachmentUpload.objects.get(pk=upload_id, scope_id=scope_id)
    with transaction.atomic():
        _, _, job, upload, attachment, _ = _lock_upload(ref)
        check_import_fence(job, fence)
        if (
            upload.producer_kind != "import"
            or upload.allocation_epoch != fence.epoch
            or identity.binding_id != attachment.storage_binding_id
            or identity.purpose != "quarantine"
            or identity.key != upload.quarantine_key
            or byte_length != upload.expected_bytes
            or upload.quarantine_write_attempts < 1
            or upload.state not in {AttachmentState.PENDING, AttachmentState.VALIDATING}
        ):
            raise AttachmentError("validation_fenced", 409)
        if upload.quarantine_version is not None:
            if (
                upload.quarantine_version,
                upload.quarantine_sha256,
                upload.received_bytes,
            ) != (identity.version, sha256, byte_length):
                raise AttachmentError("snapshot_changed", 409)
        else:
            (
                upload.quarantine_version,
                upload.quarantine_sha256,
                upload.received_bytes,
            ) = identity.version, sha256, byte_length
        upload.state = AttachmentState.VALIDATING
        upload.save(
            update_fields=[
                "quarantine_version",
                "quarantine_sha256",
                "received_bytes",
                "state",
            ]
        )
        attachment.state = AttachmentState.VALIDATING
        attachment.save(update_fields=["state"])
