"""Immutable snapshot -> isolated probe -> server-only promotion -> fenced publish.

A ready blob is not a readable occurrence. Ingestion binding, source retention,
viewer authorization and authenticated playback are independent later gates.
"""

import subprocess  # noqa: F401 - tests patch validation.subprocess.run
import tempfile

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
)
from tracer.services.attachments import sandbox
from tracer.services.attachments.bootstrap import check_session, session_policy
from tracer.services.attachments.contract import AttachmentError, admit_audio
from tracer.services.attachments.storage import ObjectIdentity
from tracer.services.attachments.uploads import settle


def sandbox_probe(path):
    """Probe in the seccomp-confined media worker; no in-process fallback.

    The earlier bubblewrap runner could not create a network namespace on GCP
    VMs or in default containers (RTM_NEWADDR EPERM). See sandbox.py/SANDBOX.md.
    """
    return sandbox.probe(path)


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
    )


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
            facts = sandbox_probe(snapshot.name)
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
        settle(upload_id, scope_id=scope_id, outcome=AttachmentState.REJECTED)
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("validation_failed", 503) from None
