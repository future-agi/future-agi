"""Exact snapshot -> bounded probe -> fenced PUT -> atomic verified publication.

All decoder/storage calls are outside database transactions. The existing
validate outbox is also the bounded reconciliation cursor until temporary and
unknown objects are gone; no new accounting-only events are emitted.
"""

import hashlib
import json
import os
import struct
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import AttachmentOutbox, AttachmentState, AttachmentUpload
from tracer.services.attachments import accounting, capacity_db
from tracer.services.attachments.bootstrap import LIMITS, check_session, session_policy
from tracer.services.attachments.contract import (
    SERVER_MODALITIES,
    AttachmentError,
    admit_audio,
)
from tracer.services.attachments.interfaces import (
    DecoderRequirements,
    ObjectIdentity,
    ObjectLocator,
    PutPermit,
    ReservationOwner,
    ResourceVector,
    StoredObject,
    VerifiedAssetFacts,
)
from tracer.services.attachments.probe import REJECTED_EXIT
from tracer.services.attachments.publication import (
    check_import_fence,
    publish_verified_locked,
)
from tracer.services.attachments.uploads import (
    _lock_upload,
    _recover_key,
    _storage_for,
    cleanup_unowned_result,
    settle,
)

VALIDATED_MIME_TYPES = frozenset(
    mime for types in SERVER_MODALITIES.values() for mime in types
)
SANDBOX_NPROC = 16
MAX_VALIDATION_ATTEMPTS = 5
VALIDATION_LEASE_SECONDS = 60
VALIDATION_BACKOFF_SECONDS = 30
VALIDATION_MAX_BACKOFF_SECONDS = 600


def sandbox_probe(path):
    """Linux bubblewrap with no network, credentials, app tree or writable host FS.

    No permissive fallback if user namespaces/bubblewrap are unavailable. The
    only writable filesystem is a private /tmp, with output limited by RLIMIT.

    The process cap is set inside the sandbox, after bwrap made its namespaces.
    RLIMIT_NPROC counts every process of the caller's UID, so a cap on bwrap
    itself makes a non-root worker that already runs >=16 processes fail to
    create the namespace. Only probe.py's own REJECTED_EXIT means the bytes were
    refused; any other exit (bwrap or prlimit setup, SIGXCPU/SIGKILL, a missing
    interpreter) is the sandbox failing and is retryable, never a rejection.
    """
    command = [
        "/usr/bin/prlimit",
        "--as=536870912",
        "--cpu=10",
        "--fsize=8192",
        "--nofile=32",
        "--",
        "/usr/bin/bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
    ]
    if os.path.exists("/lib64"):
        command += ["--ro-bind", "/lib64", "/lib64"]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(Path(__file__).with_name("probe.py")),
        "/probe.py",
        "--ro-bind",
        str(path),
        "/input",
        "--chdir",
        "/tmp",
        "/usr/bin/prlimit",
        f"--nproc={SANDBOX_NPROC}",
        "--",
        "/usr/bin/python3",
        "-I",
        "-S",
        "/probe.py",
        "/input",
    ]
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                command,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
                close_fds=True,
            )
            if completed.returncode == REJECTED_EXIT:
                raise AttachmentError("validation_rejected", 422)
            if completed.returncode != 0:
                raise ValueError
            output.seek(0)
            result = json.loads(output.read(8193))
        if (
            set(result) != {"mime_type", "sample_rate", "channels", "frame_count"}
            or result["mime_type"] not in VALIDATED_MIME_TYPES
            or any(
                type(result[key]) is not int or result[key] < 0
                for key in ("sample_rate", "channels", "frame_count")
            )
        ):
            raise ValueError
        return result
    except AttachmentError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AttachmentError("validation_unavailable", 503) from None


def require_supported_plan(mime_type):
    if mime_type == "audio/L16":
        # No trusted PCM description in SourceFacts; never guess rate/channels
        # from a caller's MIME or body. Full L16 qualification remains blocked.
        raise AttachmentError("unsupported_media", 422)


def require_decoder(requirements):
    from tracer.services.attachments import readiness

    require_supported_plan(requirements.mime_type)
    readiness.require_decoder(requirements)
    if requirements.operation == "validate":
        # Exercise the SAME bwrap/prlimit/interpreter path used by _snapshot.
        # This is a tiny known PCM WAV, not an untrusted source-body fetch.
        with tempfile.NamedTemporaryFile() as sample:
            sample.write(
                struct.pack(
                    "<4sI4s4sIHHIIHH4sIh",
                    b"RIFF",
                    38,
                    b"WAVE",
                    b"fmt ",
                    16,
                    1,
                    1,
                    8000,
                    16000,
                    2,
                    16,
                    b"data",
                    2,
                    0,
                )
            )
            sample.flush()
            try:
                facts = sandbox_probe(sample.name)
            except AttachmentError:
                raise AttachmentError("validation_unavailable", 503) from None
            if facts != {
                "mime_type": "audio/wav",
                "sample_rate": 8000,
                "channels": 1,
                "frame_count": 1,
            }:
                raise AttachmentError("validation_unavailable", 503)


def _check(upload):
    scope = check_session(upload.capture_session)
    now = timezone.now()
    if (
        upload.state != AttachmentState.VALIDATING
        or upload.reservation_settled_at is not None
        or upload.expires_at <= now
        or upload.attachment.deleted_at is not None
        or upload.deletion_generation != scope.deletion_generation
        or upload.policy_version != scope.policy_version
        or upload.occurrence.deleted_at
        or upload.occurrence.expires_at <= now
        or (
            upload.occurrence.source_expires_at is not None
            and upload.occurrence.source_expires_at <= now
        )
    ):
        raise AttachmentError("validation_fenced", 409)
    policy = session_policy(upload.capture_session)
    if upload.producer_kind == "import" and (
        not scope.allow_import or not policy.allow_import
    ):
        raise AttachmentError("policy_denied", 403)
    admit_audio(
        scope,
        policy,
        direction=upload.occurrence.direction,
        source_kind=upload.attachment.source_kind,
        creates_recording=upload.recording_id is not None,
        modality=upload.attachment.modality,
    )


def accept(facts, upload):
    if facts["mime_type"] != upload.mime_type:
        raise AttachmentError("validation_rejected", 422)
    if upload.mime_type.startswith("audio/"):
        if any(
            type(facts[name]) is not int or facts[name] <= 0
            for name in ("sample_rate", "channels", "frame_count")
        ):
            raise AttachmentError("validation_rejected", 422)
        if (
            upload.recording_id
            and facts["frame_count"] * 1000
            > facts["sample_rate"] * LIMITS["segment_duration_ms"]
        ):
            raise AttachmentError("validation_rejected", 422)
    return facts


def _facts(upload, identity, digest, probe):
    # The probe uses 0 for absent non-audio facts. Shared values use None, never
    # fabricated audio rates/channels for images, documents or video.
    return VerifiedAssetFacts(
        StoredObject(identity, upload.expected_bytes, digest),
        probe["mime_type"],
        timezone.now(),
        probe["sample_rate"] or None,
        probe["channels"] or None,
        probe["frame_count"] or None,
    )


def _snapshot(upload, storage, snapshot):
    _check(upload)
    if (
        not upload.quarantine_version
        or not 0 < upload.expected_bytes <= LIMITS["max_upload_bytes"]
    ):
        raise AttachmentError("validation_rejected", 422)
    require_decoder(
        DecoderRequirements(
            "validate", upload.mime_type, upload.reservation_decoded_bytes
        )
    )
    identity = ObjectIdentity(
        storage.binding_id,
        "quarantine",
        upload.quarantine_key,
        upload.quarantine_version,
    )
    digest = storage.snapshot(identity, snapshot, expected_length=upload.expected_bytes)
    snapshot.flush()
    snapshot.seek(0)
    hasher, size = hashlib.sha256(), 0
    while chunk := snapshot.read(256 * 1024):
        size += len(chunk)
        if size > upload.expected_bytes:
            raise AttachmentError("snapshot_changed", 409)
        hasher.update(chunk)
    if (
        size != upload.expected_bytes
        or digest != hasher.hexdigest()
        or digest != upload.quarantine_sha256
    ):
        raise AttachmentError("snapshot_changed", 409)
    probe = accept(sandbox_probe(snapshot.name), upload)
    snapshot.seek(0)
    return digest, probe


def _asset_facts(asset):
    return VerifiedAssetFacts(
        StoredObject(
            ObjectIdentity(
                asset.storage_binding_id,
                "serving",
                asset.verified_storage_key,
                asset.verified_storage_version,
            ),
            asset.verified_byte_length,
            asset.verified_sha256,
        ),
        asset.verified_mime_type,
        asset.verified_at,
        asset.verified_sample_rate,
        asset.verified_channels,
        asset.verified_frame_count,
    )


def _claim(event_id):
    now = timezone.now()
    ref = AttachmentOutbox.objects.filter(
        pk=event_id, event="validate", processed_at__isnull=True, available_at__lte=now
    ).first()
    if ref is None:
        return None, False
    upload = AttachmentUpload.objects.get(
        pk=ref.upload_id,  # type: ignore[misc]  # Django accepts pk=None (no match); preserve DoesNotExist for an unowned event.
        scope_id=ref.scope_id,
    )
    if upload.producer_kind != "authenticated_stream":
        raise AttachmentError("validation_fenced", 409)
    # Reconciliation of already terminal uploads is not another admission.
    if upload.state == AttachmentState.VALIDATING and not capacity_db.count_attempt(
        scope_id=ref.scope_id, pool_key=upload.reservation_pool_key, now=now
    ):
        return None, False
    with transaction.atomic():
        upload = AttachmentUpload.objects.select_for_update().get(
            pk=upload.id, scope_id=ref.scope_id
        )
        event = AttachmentOutbox.objects.select_for_update().get(
            pk=event_id, event="validate"
        )
        if event.processed_at or event.available_at > now:
            return None, False
        exhausted = (
            upload.state == AttachmentState.VALIDATING
            and event.attempts >= MAX_VALIDATION_ATTEMPTS
        )
        if not exhausted:
            event.attempts += 1
            event.available_at = now + timedelta(seconds=VALIDATION_LEASE_SECONDS)
            event.save(update_fields=["attempts", "available_at"])
        return event, exhausted


def _event_matches(event, claim, *, live=True):
    return (
        event.processed_at is None
        and event.attempts == claim.attempts
        and event.available_at == claim.available_at
        and (not live or event.available_at > timezone.now())
    )


def _guard(ref, claim):
    chain = _lock_upload(ref)
    upload = chain[3]
    _check(upload)
    # Upload lock serializes _claim/_finish; no early Outbox lock that could
    # invert the derivative enqueue/publication ordering.
    event = AttachmentOutbox.objects.get(
        pk=claim.id, upload_id=upload.id, scope_id=upload.scope_id
    )
    if not _event_matches(event, claim):
        raise AttachmentError("validation_fenced", 409)
    return chain


def _finish(claim, *, retry_at=None):
    with transaction.atomic():
        AttachmentUpload.objects.select_for_update().get(
            pk=claim.upload_id, scope_id=claim.scope_id
        )
        event = AttachmentOutbox.objects.select_for_update().get(pk=claim.id)
        if not _event_matches(event, claim, live=False):
            return False
        if retry_at is None:
            event.processed_at = timezone.now()
            event.save(update_fields=["processed_at"])
        else:
            event.available_at = retry_at
            event.save(update_fields=["available_at"])
        return True


def reconcile_ready_upload(ref, storage):
    """Retain READY original; reconcile temporary/unknown versions after horizon.

    Depends on the storage contract's enforced watchdog/zero hidden retries.
    Any ambiguous erasure leaves cleanup due and every live charge intact.
    """
    now = timezone.now()
    if ref.reservation_settled_at is not None:
        return True
    if (
        ref.cleanup_not_before is None
        or ref.cleanup_not_before > now
        or ref.transfer_started_at is not None
    ):
        return False
    with transaction.atomic():
        _, _, _, upload, attachment, _ = _lock_upload(ref, authority=False)
        if upload.state != AttachmentState.READY or attachment.deleted_at is not None:
            return False  # unified retention owns tombstoned assets
        keep = _asset_facts(attachment).object.identity
    result = storage.erase_all_versions(
        ObjectLocator(storage.binding_id, "quarantine", upload.quarantine_key)
    )
    if (
        not result.empty
        or result.retryable
        or result.checked_at < upload.cleanup_not_before
    ):
        return False
    for erase in (True, False):
        cursor = None
        for _ in range(100):
            page = storage.list_versions(
                ObjectLocator(storage.binding_id, "serving", upload.serving_key),
                cursor=cursor,
                limit=100,
            )
            for identity in page.versions + page.delete_markers:
                if identity == keep:
                    continue
                if not erase:
                    return False
                cleanup_unowned_result(
                    upload.id,
                    scope_id=upload.scope_id,
                    identity=identity,
                    storage=storage,
                )
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        else:
            return False
    with transaction.atomic():
        locks = capacity_db.lock_capacity(
            scope_id=ref.scope_id, pool_keys=(ref.reservation_pool_key,)
        )
        _, _, _, current, attachment, _ = _lock_upload(ref, authority=False)
        if (
            current.state != AttachmentState.READY
            or attachment.deleted_at is not None
            or _asset_facts(attachment).object.identity != keep
            or current.cleanup_not_before != upload.cleanup_not_before
            or current.transfer_started_at is not None
        ):
            return False
        current.cleanup_checked_at, current.unowned_cleanup_due_at = (
            timezone.now(),
            None,
        )
        current.save(update_fields=["cleanup_checked_at", "unowned_cleanup_due_at"])
        accounting.receipt_locked(
            current, attachment=attachment, outcome=AttachmentState.READY
        )
        return capacity_db.release_locked(
            locks,
            owner=ReservationOwner("upload", current.id, current.scope_id),
            cleanup_complete=True,
            now=timezone.now(),
        )


_cleanup_ready = reconcile_ready_upload


def validate(upload_id, *, scope_id, storage=None, _claim_event=None):
    ref = AttachmentUpload.objects.select_related(
        "capture_session", "attachment", "occurrence"
    ).get(pk=upload_id, scope_id=scope_id)
    if ref.producer_kind != "authenticated_stream":
        raise AttachmentError("validation_fenced", 409)
    storage = _storage_for(
        ref,
        storage,
        operation="erase" if ref.state == AttachmentState.READY else "write",
    )
    if ref.state == AttachmentState.READY:
        settle(ref.id, scope_id=scope_id, outcome=AttachmentState.READY)
        _cleanup_ready(ref, storage)
        return _asset_facts(ref.attachment)
    claim = _claim_event
    if claim is None:
        event = AttachmentOutbox.objects.get(
            upload_id=upload_id,
            scope_id=scope_id,
            event="validate",
            generation=ref.deletion_generation,
        )
        claim, exhausted = _claim(event.id)
        if claim is None or exhausted:
            raise AttachmentError("validation_unavailable", 503)
    with transaction.atomic():
        _guard(ref, claim)
    identity = None
    from tracer.services.attachments.readiness import snapshot_file

    with snapshot_file(named=True) as snapshot:
        digest, probe = _snapshot(ref, storage, snapshot)
        if ref.serving_version:
            # Only the previously recorded immutable result, never an unrecorded
            # version discovered by listing, can be reused by a new worker.
            assert ref.serving_key is not None  # Prewrite locator fence (0142).
            identity = ObjectIdentity(
                storage.binding_id, "serving", ref.serving_key, ref.serving_version
            )
        else:
            if ref.serving_write_attempts:
                _recover_key(
                    ref,
                    storage=storage,
                    purpose="serving",
                    guard=lambda: _guard(ref, claim)[3],
                )
            with transaction.atomic():
                locks = capacity_db.lock_capacity(
                    scope_id=scope_id, pool_keys=(ref.reservation_pool_key,)
                )
                _, _, _, upload, _, _ = _guard(ref, claim)
                if upload.serving_version:
                    raise AttachmentError("validation_fenced", 409)
                if upload.serving_write_attempts >= 3:
                    raise AttachmentError("validation_rejected", 422)
                if upload.serving_write_attempts:
                    capacity_db.resize_locked(
                        locks,
                        owner=ReservationOwner("upload", upload.id, scope_id),
                        amount=capacity_db.owner_vector(upload)
                        + ResourceVector(upload.expected_bytes, 0, 0, 0, 1, 0),
                    )
                upload.serving_write_attempts += 1
                upload.write_deadline_at = min(
                    timezone.now() + timedelta(seconds=30),
                    claim.available_at,
                    upload.expires_at,
                )
                upload.cleanup_not_before = max(
                    upload.cleanup_not_before,
                    upload.write_deadline_at + timedelta(seconds=15),
                )
                upload.unowned_cleanup_due_at = upload.cleanup_not_before
                upload.save(
                    update_fields=[
                        "serving_write_attempts",
                        "write_deadline_at",
                        "cleanup_not_before",
                        "unowned_cleanup_due_at",
                    ]
                )
                permit = PutPermit(
                    "upload",
                    upload.id,
                    ObjectLocator(storage.binding_id, "serving", upload.serving_key),
                    None,
                    upload.serving_write_attempts,
                    upload.write_deadline_at,
                )
            identity = storage.promote(
                snapshot,
                permit=permit,
                expected_length=ref.expected_bytes,
                expected_sha256=digest,
            )
        facts = _facts(ref, identity, digest, probe)
        try:
            with transaction.atomic():
                scope, _, _, upload, attachment, _ = _guard(ref, claim)
                if (identity.binding_id, identity.purpose, identity.key) != (
                    attachment.storage_binding_id,
                    "serving",
                    upload.serving_key,
                ):
                    raise AttachmentError("snapshot_changed", 409)
                if upload.serving_version not in (None, identity.version):
                    raise AttachmentError("snapshot_changed", 409)
                upload.serving_version = identity.version
                upload.save(update_fields=["serving_version"])
                publish_verified_locked(
                    attachment=attachment,
                    facts=facts,
                    expected_binding_id=attachment.storage_binding_id,
                    scope=scope,
                    fence=None,
                    allocation=upload,
                )
                upload.state = AttachmentState.READY
                upload.save(update_fields=["state"])
                from tracer.services.attachments.derivatives import (
                    enqueue_for_asset_locked,
                )

                enqueue_for_asset_locked(attachment)
                accounting.receipt_locked(
                    upload, attachment=attachment, outcome=AttachmentState.READY
                )
        except Exception:
            cleanup_unowned_result(
                upload_id, scope_id=scope_id, identity=identity, storage=storage
            )
            raise
    _cleanup_ready(
        AttachmentUpload.objects.select_related("attachment").get(
            pk=upload_id, scope_id=scope_id
        ),
        storage,
    )
    return facts


def validate_import_snapshot(*, upload_id, scope_id, fence):
    """Probe/rehash and promote using the import owner's original lease.

    No client capability or default admission binding is minted. Caller performs
    source remote revalidation, then locked publication and terminal settlement.
    """
    from tracer.services.attachments import imports

    ref = AttachmentUpload.objects.select_related(
        "capture_session", "attachment", "occurrence"
    ).get(pk=upload_id, scope_id=scope_id)
    with transaction.atomic():
        _, _, job, upload, _, _ = _lock_upload(ref)
        check_import_fence(job, fence)
        _check(upload)
        if upload.producer_kind != "import" or upload.allocation_epoch != fence.epoch:
            raise AttachmentError("validation_fenced", 409)
    storage = _storage_for(ref)
    from tracer.services.attachments.readiness import snapshot_file

    with snapshot_file(named=True) as snapshot:
        digest, probe = _snapshot(ref, storage, snapshot)
        if ref.serving_version:
            assert ref.serving_key is not None  # Prewrite locator fence (0142).
            identity = ObjectIdentity(
                storage.binding_id, "serving", ref.serving_key, ref.serving_version
            )
            facts = _facts(ref, identity, digest, probe)
        else:
            permit = imports.prepare_put(
                job_id=job.id,
                allocation_id=upload_id,
                allocation_kind="upload",
                fence=fence,
                purpose="serving",
                now=timezone.now(),
            )
            identity = storage.promote(
                snapshot,
                permit=permit,
                expected_length=ref.expected_bytes,
                expected_sha256=digest,
            )
            facts = _facts(ref, identity, digest, probe)
            result = imports.record_put(
                job_id=job.id,
                allocation_id=upload_id,
                allocation_kind="upload",
                permit=permit,
                identity=identity,
                facts=facts,
            )
            if result == "stale_unowned":
                imports.cleanup_stale_put(
                    job_id=job.id,
                    allocation_id=upload_id,
                    allocation_kind="upload",
                    permit=permit,
                    identity=identity,
                )
                raise AttachmentError("validation_fenced", 409)
        with transaction.atomic():
            _, _, job, upload, _, _ = _lock_upload(ref)
            check_import_fence(job, fence)
            _check(upload)
            if (
                upload.serving_version != identity.version
                or upload.allocation_epoch != fence.epoch
            ):
                raise AttachmentError("validation_fenced", 409)
        return facts


def _settle_claim(claim, outcome):
    ref = AttachmentUpload.objects.get(pk=claim.upload_id, scope_id=claim.scope_id)
    with transaction.atomic():
        # Acquire every lock settle will need before inspecting the claim. Its
        # nested reacquisition is of already-owned rows, never another pool.
        capacity_db.lock_capacity(
            scope_id=ref.scope_id, pool_keys=(ref.reservation_pool_key,)
        )
        _lock_upload(ref, authority=False)
        event = AttachmentOutbox.objects.get(pk=claim.id)
        if not _event_matches(event, claim, live=False):
            return False
        settle(ref.id, scope_id=ref.scope_id, outcome=outcome)
    return True


def consume_validation(event_id, *, storage=None):
    claim, exhausted = _claim(event_id)
    if claim is None:
        return False
    current = AttachmentUpload.objects.get(pk=claim.upload_id, scope_id=claim.scope_id)
    if current.state in accounting.TERMINAL and current.state != AttachmentState.READY:
        _settle_claim(claim, current.state)
        return _finish(claim)  # unified erase work retains any pending charge
    if exhausted:
        _settle_claim(claim, AttachmentState.FAILED)
        return _finish(claim)
    try:
        validate(
            claim.upload_id,
            scope_id=claim.scope_id,
            storage=storage,
            _claim_event=claim,
        )
    except AttachmentError as exc:
        terminal = {
            "validation_rejected",
            "snapshot_changed",
            "policy_denied",
            "disabled",
            "deleted",
            "expired",
            "capability_expired",
        }
        if exc.code in terminal:
            _settle_claim(claim, AttachmentState.REJECTED)
            return _finish(claim)
        delay = min(
            VALIDATION_BACKOFF_SECONDS * 2 ** min(claim.attempts - 1, 10),
            VALIDATION_MAX_BACKOFF_SECONDS,
        )
        _finish(claim, retry_at=timezone.now() + timedelta(seconds=delay))
        return False
    upload = AttachmentUpload.objects.get(pk=claim.upload_id, scope_id=claim.scope_id)
    if upload.reservation_settled_at is None:
        assert upload.cleanup_not_before is not None  # Prewrite locator fence (0142).
        _finish(
            claim,
            retry_at=max(
                timezone.now() + timedelta(seconds=30), upload.cleanup_not_before
            ),
        )
        return False
    return _finish(claim)


def drain_validations(*, storage=None, limit=32):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    due = list(
        AttachmentOutbox.objects.filter(
            event="validate",
            processed_at__isnull=True,
            available_at__lte=timezone.now(),
        )
        .order_by("available_at", "id")
        .values_list("pk", flat=True)[:limit]
    )
    return sum(consume_validation(pk, storage=storage) for pk in due)
