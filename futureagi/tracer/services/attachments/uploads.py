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
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentRecording,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    UploadReservation,
)
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
    AttachmentError,
    admit_audio,
    fingerprint,
    span_id,
    trace_uuid,
)

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
# Finite operational safety bounds, not edition limits. No ambient plan checks.
BUDGETS = (
    (128, 8 * 1024**3, 8192, 960),
    (32, 2 * 1024**3, 2048, 240),
    (16, 1024**3, 1024, 120),
)


def _budget_keys(scope):
    return ("global", f"org:{scope.organization_id}", f"project:{scope.project_id}")


def _lock_budgets(scope):
    rows = []
    for key in _budget_keys(scope):
        AttachmentBudget.objects.get_or_create(
            key=key, defaults={"window_started_at": timezone.now()}
        )
        rows.append(AttachmentBudget.objects.select_for_update().get(pk=key))
    return rows


@transaction.atomic
def _count_attempt(scope):
    rows = _lock_budgets(scope)
    allowed = True
    now = timezone.now()
    for row, limits in zip(rows, BUDGETS, strict=False):
        if now - row.window_started_at >= timedelta(minutes=1):
            row.window_started_at, row.initialization_attempts = now, 0
        row.initialization_attempts += 1
        allowed &= row.initialization_attempts <= limits[3]
        row.save(update_fields=["window_started_at", "initialization_attempts"])
    return allowed


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
        or not UploadReservation.objects.filter(
            upload=upload, scope_id=upload.scope_id, settled_at__isnull=True
        ).exists()
    ):
        raise AttachmentError("expired", 409)
    expires_at = min(_capability_expiry(session), upload.expires_at)
    return upload, capability(session, upload, expires_at=expires_at), expires_at


def placement(body):
    """The placement fields present in ``body``; absent ones stay NULL."""
    return {name: body[name] for name in PLACEMENT_FIELDS if body.get(name) is not None}


def initialize(writer, *, scope_id, body, key):
    """F01 ``initializeAttachmentUpload``; ``body`` is a validated UploadRequest."""
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
            locked = AttachmentUpload.objects.select_for_update().get(
                pk=existing.pk, scope=scope
            )
            check_session(session)
            return _renew(locked, session)
    if not _count_attempt(scope):
        raise AttachmentError("throttled", 429)
    with transaction.atomic():
        # One ordering for all budget-mutating operations, then scope/upload.
        budgets = _lock_budgets(scope)
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        check_session(session)
        _check_generation(scope, body)
        existing = AttachmentUpload.objects.filter(
            scope=scope, capture_session=session, idempotency_key=key
        ).first()
        if existing is not None:
            if existing.request_fingerprint != digest:
                raise AttachmentError("idempotency_conflict", 409)
            return _renew(existing, session)
        for row, limits in zip(budgets, BUDGETS, strict=False):
            if (
                row.active_transfers >= limits[0]
                or row.reserved_bytes + expected_bytes > limits[1]
                or row.validation_jobs >= limits[2]
            ):
                raise AttachmentError("throttled", 429)
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
        occurrence_expiry = now + timedelta(hours=24)
        horizon = min(occurrence_expiry, now + timedelta(seconds=UPLOAD_SECONDS))
        expiry = min(_capability_expiry(session), horizon)
        try:
            with transaction.atomic():
                attachment = Attachment.objects.create(
                    **common,
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
                )
        except IntegrityError:
            raise AttachmentError("idempotency_conflict", 409) from None
        UploadReservation.objects.create(
            scope=scope,
            upload=upload,
            reserved_bytes=expected_bytes,
            decoded_bytes=MAX_UPLOAD_BYTES,
            expires_at=horizon,
        )
        for row in budgets:
            row.active_transfers += 1
            row.reserved_bytes += expected_bytes
            # Reserve the validation slot now, not after accepting bytes.
            row.validation_jobs += 1
            row.save(
                update_fields=["active_transfers", "reserved_bytes", "validation_jobs"]
            )
        AttachmentOutbox.objects.create(
            scope=scope,
            upload=upload,
            attachment=attachment,
            event="admitted",
            generation=scope.deletion_generation,
            available_at=timezone.now(),
        )
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
    if upload is None:
        raise AttachmentError("not_found", 404)
    now = timezone.now()
    occurrence = upload.occurrence
    if (
        upload.deletion_generation != session.deletion_generation
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


def transfer(token, *, scope_id, upload_id, chunks, storage, content_length=None):
    """F01 ``streamAttachmentContent``; replayable in full until ``complete``."""
    upload = _live_upload(
        token, scope_id, upload_id, "upload", states=(AttachmentState.PENDING,)
    )
    if content_length is not None and content_length != upload.expected_bytes:
        raise AttachmentError("length_mismatch")
    with transaction.atomic():
        locked = AttachmentUpload.objects.select_for_update().get(
            pk=upload.id, scope_id=scope_id
        )
        if locked.state != AttachmentState.PENDING:
            raise AttachmentError("transfer_conflict", 409)
        replay = bool(locked.quarantine_version)
        if not replay:
            started = locked.transfer_started_at
            if started and timezone.now() - started < timedelta(
                seconds=TRANSFER_SECONDS
            ):
                # Another transfer holds the lease; a stale lease is taken over.
                raise AttachmentError("transfer_conflict", 409)
            locked.transfer_started_at = timezone.now()
            locked.save(update_fields=["transfer_started_at"])
    started, size, hasher = time.monotonic(), 0, hashlib.sha256()
    try:
        # Disk belongs to the bounded server transfer, not an SDK disk spool.
        with tempfile.TemporaryFile() as snapshot:
            for chunk in chunks:
                _live_upload(token, scope_id, upload_id, "upload")
                if time.monotonic() - started > TRANSFER_SECONDS:
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
                return status_document(locked)
            snapshot.seek(0)
            identity = storage.put_quarantine(upload.id, snapshot, size)
        with transaction.atomic():
            scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
            locked = AttachmentUpload.objects.select_for_update().get(
                pk=upload.id, scope=scope
            )
            _live_upload(token, scope_id, upload_id, "upload")
            if locked.state != AttachmentState.PENDING or locked.quarantine_version:
                raise AttachmentError("transfer_conflict", 409)
            locked.quarantine_key, locked.quarantine_version = (
                identity.key,
                identity.version,
            )
            locked.quarantine_sha256 = hasher.hexdigest()
            locked.received_bytes = size
            locked.transfer_started_at = None
            locked.save(
                update_fields=[
                    "quarantine_key",
                    "quarantine_version",
                    "quarantine_sha256",
                    "received_bytes",
                    "transfer_started_at",
                ]
            )
            return status_document(locked)
    except Exception as exc:
        if not replay:
            # Discard the partial transfer and release the lease so the SDK can
            # replay the whole body (BR-3). The reservation stays until expiry.
            AttachmentUpload.objects.filter(
                pk=upload.id,
                scope_id=scope_id,
                state=AttachmentState.PENDING,
                quarantine_version="",
            ).update(transfer_started_at=None)
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("transfer_failed", 503) from None


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
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    upload = AttachmentUpload.objects.select_for_update().get(pk=upload_id, scope=scope)
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
    if outcome not in {
        AttachmentState.READY,
        AttachmentState.FAILED,
        AttachmentState.REJECTED,
        AttachmentState.EXPIRED,
        AttachmentState.DELETED,
    }:
        raise ValueError("terminal outcome required")
    scope = AttachmentScope.objects.get(pk=scope_id)
    rows = _lock_budgets(scope)
    upload = AttachmentUpload.objects.select_for_update().get(pk=upload_id, scope=scope)
    reservation = UploadReservation.objects.select_for_update().get(
        upload=upload, scope=scope
    )
    if reservation.settled_at or (
        upload.state == AttachmentState.READY and outcome != AttachmentState.READY
    ):
        return False
    if outcome == AttachmentState.READY and upload.state != AttachmentState.READY:
        raise AttachmentError("not_validated", 409)
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["settled_at"])
    if outcome != AttachmentState.READY:
        upload.state = outcome
        upload.save(update_fields=["state"])
        Attachment.objects.filter(pk=upload.attachment_id, scope=scope).update(
            state=outcome
        )
    for row in rows:
        row.active_transfers -= 1
        row.reserved_bytes -= reservation.reserved_bytes
        row.validation_jobs -= 1
        row.save(
            update_fields=["active_transfers", "reserved_bytes", "validation_jobs"]
        )
    AttachmentOutbox.objects.get_or_create(
        scope=scope,
        upload=upload,
        event="settled",
        generation=upload.deletion_generation,
        defaults={
            "available_at": timezone.now(),
            "attachment_id": upload.attachment_id,
        },
    )
    if outcome != AttachmentState.READY:
        AttachmentOutbox.objects.get_or_create(
            scope=scope,
            upload=upload,
            event="erase",
            generation=upload.deletion_generation,
            defaults={
                "available_at": timezone.now() + timedelta(seconds=TRANSFER_SECONDS),
                "attachment_id": upload.attachment_id,
            },
        )
    return True


def abort(token, *, scope_id, upload_id, body=None):
    """Releases a pending job exactly once; repeating it reports the same state.

    A completed upload (validating or ready) is never aborted: its occurrence
    may already be a manifest segment, and deleting it is an occurrence
    deletion, not a transfer abort. That conflicts (409) and changes nothing.
    """
    upload = _live_upload(token, scope_id, upload_id, "abort")
    _check_request(upload, body)
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
