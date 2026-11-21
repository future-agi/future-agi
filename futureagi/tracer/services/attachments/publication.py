"""Exact allocation-to-asset publication. Caller holds the ordered owner chain.

This module never performs storage, source HTTP, or decoder operations. It does
not grant authority: the caller must check its locked creator/source/occurrence.
"""

from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import AttachmentState, AttachmentUpload
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentRecordingSegment,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import LeaseFence, VerifiedAssetFacts


def check_import_fence(job, fence, *, now=None):
    now = now or timezone.now()
    if (
        job is None
        or not isinstance(fence, LeaseFence)
        or fence.owner_kind != "import"
        or fence.owner_id != job.id
        or fence.token != job.lease_token
        or fence.epoch != job.lease_epoch
        or fence.expires_at != job.lease_until
        or fence.expires_at <= now
        or job.deadline <= now
        or job.lease_phase not in {"writing", "publishing"}
        or job.state not in {"importing", "validating"}
        or job.reservation_settled_at is not None
    ):
        raise AttachmentError("validation_fenced", 409)


def publish_verified_locked(
    *, attachment, facts, expected_binding_id, scope, fence, allocation
):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("publication requires ordered transaction locks")
    if not isinstance(facts, VerifiedAssetFacts):
        raise ValueError("verified facts required")
    now = timezone.now()
    identity = facts.object.identity
    if (
        attachment.scope_id != scope.id
        or allocation.scope_id != scope.id
        or expected_binding_id is None
        or attachment.storage_binding_id != expected_binding_id
        or identity.binding_id != expected_binding_id
        or identity.purpose != "serving"
        or attachment.deleted_at is not None
        or attachment.state
        not in {
            AttachmentState.PENDING,
            AttachmentState.VALIDATING,
            AttachmentState.READY,
        }
        or attachment.policy_version != scope.policy_version
        or attachment.deletion_generation != scope.deletion_generation
        or facts.mime_type != attachment.mime_type
        or facts.object.byte_length <= 0
        or facts.validated_at > now
    ):
        raise AttachmentError("validation_fenced", 409)
    if isinstance(allocation, AttachmentUpload):
        if (
            allocation.attachment_id != attachment.id
            or allocation.state
            not in {AttachmentState.VALIDATING, AttachmentState.READY}
            or allocation.reservation_settled_at is not None
            or allocation.serving_key != identity.key
            or allocation.serving_version != identity.version
            or allocation.received_bytes != facts.object.byte_length
            or allocation.expected_bytes != facts.object.byte_length
            or allocation.quarantine_sha256 != facts.object.sha256
            or allocation.mime_type != facts.mime_type
            or allocation.expires_at <= now
        ):
            raise AttachmentError("validation_fenced", 409)
        if allocation.producer_kind == "import":
            job = AttachmentImportJob.objects.get(
                upload_id=allocation.id, scope_id=scope.id
            )
            check_import_fence(job, fence, now=now)
            if (
                job.storage_binding_id != expected_binding_id
                or allocation.allocation_epoch != fence.epoch
            ):
                raise AttachmentError("validation_fenced", 409)
        elif fence is not None:
            raise AttachmentError("validation_fenced", 409)
    elif isinstance(allocation, AttachmentRecordingSegment):
        if allocation.import_job_id is None:
            raise AttachmentImportJob.DoesNotExist
        job = AttachmentImportJob.objects.get(
            pk=allocation.import_job_id, scope_id=scope.id
        )
        check_import_fence(job, fence, now=now)
        if (
            allocation.state not in {"staged", "published"}
            or allocation.attachment_id not in (None, attachment.id)
            or allocation.attachment_client_id != attachment.client_id
            or allocation.storage_binding_id != expected_binding_id
            or job.storage_binding_id != expected_binding_id
            or allocation.allocation_epoch != fence.epoch
            or allocation.storage_key != identity.key
            or allocation.storage_version != identity.version
            or allocation.sha256 != facts.object.sha256
            or allocation.byte_length != facts.object.byte_length
            or allocation.frame_count != facts.frame_count
        ):
            raise AttachmentError("validation_fenced", 409)
    else:
        raise ValueError("unsupported publication allocation")
    values = {
        "verified_storage_key": identity.key,
        "verified_storage_version": identity.version,
        "verified_sha256": facts.object.sha256,
        "verified_byte_length": facts.object.byte_length,
        "verified_mime_type": facts.mime_type,
        "verified_sample_rate": facts.sample_rate,
        "verified_channels": facts.channels,
        "verified_frame_count": facts.frame_count,
    }
    if attachment.verified_blob_id is not None:
        if any(getattr(attachment, key) != value for key, value in values.items()):
            raise AttachmentError("snapshot_changed", 409)
        return
    values.update(
        verified_blob_id=uuid4(),
        verified_blob_created_at=now,
        verified_at=facts.validated_at,
        state=AttachmentState.READY,
        completed_at=now,
    )
    for name, value in values.items():
        setattr(attachment, name, value)
    attachment.save(update_fields=list(values))
