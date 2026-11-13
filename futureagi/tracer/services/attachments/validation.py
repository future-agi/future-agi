"""Immutable snapshot -> isolated probe -> server-only promotion -> fenced publish.

A ready blob is not a readable occurrence. Ingestion binding, source retention,
viewer authorization and authenticated playback are independent later gates.
"""

import json
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
)
from tracer.services.attachments.bootstrap import check_session, session_policy
from tracer.services.attachments.contract import (
    SERVER_MODALITIES,
    AttachmentError,
    admit_audio,
)
from tracer.services.attachments.probe import REJECTED_EXIT
from tracer.services.attachments.storage import ObjectIdentity
from tracer.services.attachments.uploads import settle

VALIDATED_MIME_TYPES = frozenset(
    mime for types in SERVER_MODALITIES.values() for mime in types
)
SANDBOX_NPROC = 16
# A validate job that keeps finding the sandbox unavailable settles FAILED after
# this many claimed attempts, which releases its reservation and budget slots.
MAX_VALIDATION_ATTEMPTS = 5
# Longer than the sandbox timeout, so a live worker keeps its claim.
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


def _check(upload):
    scope = check_session(upload.capture_session)
    if (
        upload.state != AttachmentState.VALIDATING
        or upload.deletion_generation != scope.deletion_generation
        or upload.policy_version != scope.policy_version
        or upload.occurrence.deleted_at
        or upload.occurrence.expires_at <= timezone.now()
        or (
            upload.occurrence.source_expires_at is not None
            and upload.occurrence.source_expires_at <= timezone.now()
        )
    ):
        raise AttachmentError("validation_fenced", 409)
    admit_audio(
        scope,
        session_policy(upload.capture_session),
        direction=upload.occurrence.direction,
        source_kind=upload.attachment.source_kind,
        creates_recording=upload.recording_id is not None,
        modality=upload.attachment.modality,
    )


def accept(facts, upload):
    """The sniffed type must be exactly the declared one; the blob stores it.

    A PNG declared as image/jpeg, or a WAV declared as audio/ogg, is rejected:
    the gateway serves the validated type, so a mismatch is never relabelled.
    """
    if facts["mime_type"] != upload.mime_type:
        raise AttachmentError("validation_rejected", 422)
    return facts


def validate(upload_id, *, scope_id, storage):
    """Job arguments are server IDs + scope only; no URL/credential envelope."""
    upload = AttachmentUpload.objects.select_related(
        "capture_session", "attachment", "occurrence"
    ).get(pk=upload_id, scope_id=scope_id)
    if upload.state == AttachmentState.READY:
        # A worker may have died after publication but before settlement.
        settle(upload_id, scope_id=scope_id, outcome=AttachmentState.READY)
        return AttachmentBlob.objects.get(
            scope_id=scope_id, attachment_id=upload.attachment_id
        )
    try:
        _check(upload)
        with tempfile.NamedTemporaryFile() as snapshot:
            digest = storage.snapshot(
                ObjectIdentity(upload.quarantine_key, upload.quarantine_version),
                snapshot,
                expected_length=upload.expected_bytes,
            )
            snapshot.flush()
            facts = accept(sandbox_probe(snapshot.name), upload)
            # Scope lock serializes policy/deletion fencing and publication. The
            # bounded promotion is intentionally inside this transaction.
            with transaction.atomic():
                AttachmentScope.objects.select_for_update().get(pk=scope_id)
                locked = AttachmentUpload.objects.select_for_update().get(
                    pk=upload_id, scope_id=scope_id
                )
                if locked.state == AttachmentState.READY:
                    blob = AttachmentBlob.objects.get(
                        scope_id=scope_id, attachment_id=locked.attachment_id
                    )
                else:
                    _check(locked)
                    identity = storage.promote(
                        snapshot,
                        upload_id=upload.id,
                        expected_length=upload.expected_bytes,
                        expected_sha256=digest,
                    )
                    blob = AttachmentBlob.objects.create(
                        scope_id=scope_id,
                        attachment_id=upload.attachment_id,
                        storage_key=identity.key,
                        storage_version=identity.version,
                        sha256=digest,
                        byte_length=upload.expected_bytes,
                        validated_at=timezone.now(),
                        **facts,
                    )
                    locked.state = AttachmentState.READY
                    locked.save(update_fields=["state"])
                    Attachment.objects.filter(
                        pk=upload.attachment_id, scope_id=scope_id
                    ).update(state=AttachmentState.READY, completed_at=timezone.now())
        settle(upload_id, scope_id=scope_id, outcome=AttachmentState.READY)
        return blob
    except Exception as exc:
        if isinstance(exc, AttachmentError) and exc.code == "validation_unavailable":
            # The sandbox failed, not the bytes: the upload stays VALIDATING with
            # its reservation and outbox row, so the job is retried until the
            # occurrence expiry fence settles it.
            raise
        settle(upload_id, scope_id=scope_id, outcome=AttachmentState.REJECTED)
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("validation_failed", 503) from None


def _claim(event_id):
    """Count the attempt when it starts, not when it ends.

    A worker that dies inside a job (OOM kill, a host restart) never reports
    back, but its attempt still counts, so a poison upload cannot be retried
    forever. The lease keeps other workers off the row while this one runs.
    """
    now = timezone.now()
    with transaction.atomic():
        event = (
            AttachmentOutbox.objects.select_for_update()
            .filter(pk=event_id, event="validate")
            .first()
        )
        if event is None or event.processed_at or event.available_at > now:
            return None, False
        exhausted = event.attempts >= MAX_VALIDATION_ATTEMPTS
        if not exhausted:
            event.attempts += 1
            event.available_at = now + timedelta(seconds=VALIDATION_LEASE_SECONDS)
            event.save(update_fields=["attempts", "available_at"])
        return event, exhausted


def _finish(event):
    AttachmentOutbox.objects.filter(pk=event.pk, processed_at__isnull=True).update(
        processed_at=timezone.now()
    )


def consume_validation(event_id, *, storage):
    """Run one outbox ``validate`` job. True once the job is finished.

    Accepted, rejected and fenced bytes are settled by ``validate`` itself. Only
    ``validation_unavailable`` (the sandbox failed, not the bytes) is retried,
    with backoff, up to ``MAX_VALIDATION_ATTEMPTS`` claimed attempts. Then the
    upload settles FAILED: the reservation and budget slots are released and the
    quarantined bytes are queued for erasure. Settling never overrides READY.
    """
    event, exhausted = _claim(event_id)
    if event is None:
        return False
    if not exhausted:
        try:
            validate(event.upload_id, scope_id=event.scope_id, storage=storage)
        except AttachmentError as exc:
            if exc.code != "validation_unavailable":
                _finish(event)
                return True
            if event.attempts < MAX_VALIDATION_ATTEMPTS:
                delay = min(
                    VALIDATION_BACKOFF_SECONDS * 2 ** (event.attempts - 1),
                    VALIDATION_MAX_BACKOFF_SECONDS,
                )
                AttachmentOutbox.objects.filter(pk=event.pk).update(
                    available_at=timezone.now() + timedelta(seconds=delay)
                )
                return False
            exhausted = True
    if exhausted:
        settle(event.upload_id, scope_id=event.scope_id, outcome=AttachmentState.FAILED)
    _finish(event)
    return True


def drain_validations(*, storage, limit=32):
    """Consume up to ``limit`` due validate jobs, oldest first. Returns how many finished."""
    due = (
        AttachmentOutbox.objects.filter(
            event="validate",
            processed_at__isnull=True,
            available_at__lte=timezone.now(),
        )
        .order_by("available_at", "id")
        .values_list("pk", flat=True)[:limit]
    )
    return sum(consume_validation(pk, storage=storage) for pk in list(due))
