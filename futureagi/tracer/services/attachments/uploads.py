"""Bounded authenticated transfer primitives; no direct presigns or multipart.

HTTP adapters must enforce body/header read deadlines before handing chunks to
this service. Completion queues validation and cannot make any occurrence ready.
"""

import tempfile
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    UploadReservation,
)
from tracer.services.attachments.bootstrap import (
    authorize,
    capability,
    check_session,
    session_policy,
)
from tracer.services.attachments.contract import (
    AttachmentError,
    admit_audio,
    fingerprint,
)

CHUNK_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
TRANSFER_SECONDS = 60
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


def initialize(
    token,
    *,
    scope_id,
    upload_id,
    attachment_id,
    occurrence_id,
    expected_bytes,
    mime_type,
    direction,
    source_kind,
):
    session = authorize(token, operation="create", scope_id=scope_id)
    scope = check_session(session)
    # Admission before hashing any request metadata, allocating or accessing bytes.
    admit_audio(
        scope, session_policy(session), direction=direction, source_kind=source_kind
    )
    if type(expected_bytes) is not int or not 0 < expected_bytes <= MAX_UPLOAD_BYTES:
        raise AttachmentError("too_large", 413)
    if mime_type != "audio/wav":
        raise AttachmentError("unsupported_media", 415)
    if not _count_attempt(scope):
        raise AttachmentError("throttled", 429)
    normalized = {
        "session": session.id,
        "attachment": attachment_id,
        "occurrence": occurrence_id,
        "length": expected_bytes,
        "mime": mime_type,
        "direction": direction,
        "source_kind": source_kind,
    }
    digest = fingerprint(normalized)
    with transaction.atomic():
        # One ordering for all budget-mutating operations, then scope/upload.
        budgets = _lock_budgets(scope)
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        check_session(session)
        existing = AttachmentUpload.objects.filter(
            scope=scope, client_id=upload_id
        ).first()
        if existing:
            if (
                existing.request_fingerprint != digest
                or existing.capture_session_id != session.id
            ):
                raise AttachmentError("idempotency_conflict", 409)
            return existing, capability(session, "transfer", existing)
        for row, limits in zip(budgets, BUDGETS, strict=False):
            if (
                row.active_transfers >= limits[0]
                or row.reserved_bytes + expected_bytes > limits[1]
                or row.validation_jobs >= limits[2]
            ):
                raise AttachmentError("throttled", 429)
        if (
            Attachment.objects.filter(scope=scope, client_id=attachment_id).exists()
            or AttachmentOccurrence.objects.filter(
                scope=scope, client_id=occurrence_id
            ).exists()
        ):
            raise AttachmentError("idempotency_conflict", 409)
        common = {
            "scope": scope,
            "policy_version": scope.policy_version,
            "deletion_generation": scope.deletion_generation,
        }
        attachment = Attachment.objects.create(
            **common, client_id=attachment_id, source_kind=source_kind
        )
        expiry = min(session.expires_at, timezone.now() + timedelta(minutes=5))
        occurrence = AttachmentOccurrence.objects.create(
            **common,
            client_id=occurrence_id,
            attachment=attachment,
            capture_session=session,
            trace_id=session.trace_id,
            span_id=session.span_id,
            direction=direction,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        upload = AttachmentUpload.objects.create(
            **common,
            client_id=upload_id,
            capture_session=session,
            attachment=attachment,
            occurrence=occurrence,
            request_fingerprint=digest,
            expected_bytes=expected_bytes,
            mime_type=mime_type,
            expires_at=expiry,
        )
        UploadReservation.objects.create(
            scope=scope,
            upload=upload,
            reserved_bytes=expected_bytes,
            decoded_bytes=MAX_UPLOAD_BYTES,
            expires_at=expiry,
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
            event="admitted",
            generation=scope.deletion_generation,
            available_at=timezone.now(),
        )
        return upload, capability(session, "transfer", upload)


def _live_upload(token, scope_id, upload_id, operation):
    session = authorize(
        token, operation=operation, scope_id=scope_id, upload_id=upload_id
    )
    upload = (
        AttachmentUpload.objects.select_related("occurrence")
        .filter(pk=upload_id, scope_id=scope_id, capture_session=session)
        .first()
    )
    if upload is None:
        raise AttachmentError("not_found", 404)
    if (
        upload.expires_at <= timezone.now()
        or upload.deletion_generation != session.deletion_generation
    ):
        raise AttachmentError("capability_expired", 403)
    admit_audio(
        check_session(session),
        session_policy(session),
        direction=upload.occurrence.direction,
        source_kind=upload.attachment.source_kind,
    )
    return upload


def transfer(token, *, scope_id, upload_id, chunks, storage):
    upload = _live_upload(token, scope_id, upload_id, "upload")
    with transaction.atomic():
        locked = AttachmentUpload.objects.select_for_update().get(
            pk=upload.id, scope_id=scope_id
        )
        if (
            locked.state != AttachmentState.PENDING
            or locked.transfer_started_at
            or locked.quarantine_version
        ):
            raise AttachmentError("transfer_conflict", 409)
        locked.transfer_started_at = timezone.now()
        locked.save(update_fields=["transfer_started_at"])
    started, size = time.monotonic(), 0
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
                snapshot.write(chunk)
            if size != upload.expected_bytes:
                raise AttachmentError("length_mismatch")
            snapshot.seek(0)
            identity = storage.put_quarantine(upload.id, snapshot, size)
        with transaction.atomic():
            scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
            locked = AttachmentUpload.objects.select_for_update().get(
                pk=upload.id, scope=scope
            )
            _live_upload(token, scope_id, upload_id, "upload")
            if locked.state != AttachmentState.PENDING:
                raise AttachmentError("transfer_conflict", 409)
            locked.quarantine_key, locked.quarantine_version = (
                identity.key,
                identity.version,
            )
            locked.received_bytes = size
            locked.save(
                update_fields=["quarantine_key", "quarantine_version", "received_bytes"]
            )
    except Exception as exc:
        settle(upload.id, scope_id=scope_id, outcome=AttachmentState.FAILED)
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("transfer_failed", 503) from None


@transaction.atomic
def complete(token, *, scope_id, upload_id):
    _live_upload(token, scope_id, upload_id, "complete")
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    upload = AttachmentUpload.objects.select_for_update().get(pk=upload_id, scope=scope)
    _live_upload(token, scope_id, upload_id, "complete")
    if upload.state == AttachmentState.VALIDATING:
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
        defaults={"available_at": timezone.now()},
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
        defaults={"available_at": timezone.now()},
    )
    if outcome != AttachmentState.READY:
        AttachmentOutbox.objects.get_or_create(
            scope=scope,
            upload=upload,
            event="erase",
            generation=upload.deletion_generation,
            defaults={
                "available_at": timezone.now() + timedelta(seconds=TRANSFER_SECONDS)
            },
        )
    return True


def abort(token, *, scope_id, upload_id):
    _live_upload(token, scope_id, upload_id, "abort")
    return settle(upload_id, scope_id=scope_id, outcome=AttachmentState.DELETED)


def status(token, *, scope_id, upload_id):
    upload = _live_upload(token, scope_id, upload_id, "status")
    return {
        "state": upload.state
    }  # No source, blob, filename, hash or parent metadata.
