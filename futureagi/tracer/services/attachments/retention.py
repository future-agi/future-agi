"""Deletion primitives and replayable erase jobs, not deletion-hook integration.

Callers must invoke these from authoritative ingestion/deletion transactions.
CH-only/remapped source deletion and the deployed enterprise retention scheduler
are not implemented by this module. Fail closed on unknown retention provenance.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import get_occurrence
from tracer.services.attachments.uploads import TRANSFER_SECONDS, _lock_budgets, settle


@transaction.atomic
def bind_retention(*, scope_id, occurrence_id, source):
    """Internal post-ingestion seam. This method does NOT establish writer proof.

    The binding adapter must first verify the authenticated ingest writer, nonce
    and exact occurrence. Never expose this function as an ID-only HTTP action.
    """
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    row = AttachmentOccurrence.objects.select_for_update().get(
        pk=occurrence_id, scope=scope
    )
    now = timezone.now()
    if (
        not scope.enabled
        or row.deleted_at
        or row.expires_at <= now
        or row.deletion_generation != scope.deletion_generation
        or row.policy_version != scope.policy_version
        or source.scope_id != scope.id
        or (row.trace_id, row.span_id)
        != (source.parent.trace_id, source.parent.span_id)
        or type(source.generation) is not int
        or source.generation < 0
        or source.started_at is None
        or source.expires_at is None
        or timezone.is_naive(source.started_at)
        or timezone.is_naive(source.expires_at)
        or not source.started_at <= now < source.expires_at
    ):
        raise AttachmentError("binding_fenced", 409)
    if row.bound_at:
        if (
            row.source_started_at != source.started_at
            or row.source_generation != source.generation
            or row.source_expires_at != source.expires_at
        ):
            raise AttachmentError("binding_fenced", 409)
        return row
    row.bound_at = now
    row.source_started_at, row.source_expires_at, row.source_generation = (
        source.started_at,
        source.expires_at,
        source.generation,
    )
    row.expires_at = min(row.expires_at, source.expires_at)
    row.save(
        update_fields=[
            "bound_at",
            "source_started_at",
            "source_expires_at",
            "source_generation",
            "expires_at",
        ]
    )
    audit(scope=scope, actor_id=None, entity_id=row.id, event="binding", reason="bound")
    return row


def _unreferenced(scope, attachment_id):
    return not AttachmentOccurrence.objects.filter(
        scope=scope,
        attachment_id=attachment_id,
        deleted_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


def _tombstone(row, scope, *, actor_id=None, reason):
    if row.deleted_at:
        return False
    row.deleted_at = timezone.now()
    row.save(update_fields=["deleted_at"])
    audit(
        scope=scope,
        actor_id=actor_id,
        entity_id=row.id,
        event="deletion",
        reason=reason,
    )
    if _unreferenced(scope, row.attachment_id):
        upload = AttachmentUpload.objects.select_for_update().get(
            scope=scope, attachment_id=row.attachment_id
        )
        # Release unsettled reservations before marking READY storage deleted.
        settle(upload.id, scope_id=scope.id, outcome=AttachmentState.DELETED)
        Attachment.objects.filter(pk=row.attachment_id, scope=scope).update(
            state=AttachmentState.DELETED, deleted_at=timezone.now()
        )
        AttachmentUpload.objects.filter(pk=upload.id, scope=scope).update(
            state=AttachmentState.DELETED
        )
        AttachmentOutbox.objects.update_or_create(
            scope=scope,
            upload=upload,
            event="erase",
            generation=upload.deletion_generation,
            defaults={"processed_at": None, "available_at": timezone.now()},
        )
    return True


@transaction.atomic
def delete_occurrence(viewer, authority, *, occurrence_id, parent):
    row, scope, _ = get_occurrence(viewer, authority, occurrence_id, parent, write=True)
    _lock_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
    row, _, _ = get_occurrence(viewer, authority, occurrence_id, parent, write=True)
    return _tombstone(row, scope, actor_id=viewer.user_id, reason="requested")


@transaction.atomic
def tombstone_parent(*, scope_id, parent, source_generation):
    """Internal authoritative deletion hook. Stale source versions cannot erase newer bindings."""
    if type(source_generation) is not int or source_generation < 0:
        raise AttachmentError("invalid_generation")
    scope = AttachmentScope.objects.get(pk=scope_id)
    _lock_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(
        AttachmentOccurrence.objects.filter(
            scope=scope,
            trace_id=parent.trace_id,
            span_id=parent.span_id,
            deleted_at__isnull=True,
        )
        .filter(
            Q(source_generation__isnull=True)
            | Q(source_generation__lte=source_generation)
        )
        .order_by("id")[:101]
    )
    if len(rows) > 100:
        raise AttachmentError("deletion_batch_required", 409)
    return sum(_tombstone(row, scope, reason="retention") for row in rows)


@transaction.atomic
def expire_occurrences(*, scope_id, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    scope = AttachmentScope.objects.get(pk=scope_id)
    _lock_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(
        AttachmentOccurrence.objects.filter(scope=scope, deleted_at__isnull=True)
        .filter(
            Q(expires_at__lte=timezone.now()) | Q(source_expires_at__lte=timezone.now())
        )
        .order_by("id")[:limit]
    )
    return sum(_tombstone(row, scope, reason="retention") for row in rows)


@transaction.atomic
def fence_scope(*, scope_id):
    """Immediate read/admission denial. Caller must separately schedule physical cleanup."""
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    scope.deletion_generation += 1
    scope.policy_version += 1
    scope.enabled = False
    scope.save(update_fields=["deletion_generation", "policy_version", "enabled"])
    audit(
        scope=scope,
        actor_id=None,
        entity_id=scope.id,
        event="deletion",
        reason="scope_deleted",
    )
    return scope.deletion_generation


@transaction.atomic
def erase(event_id, *, storage):
    """At-least-once erase, rescheduled through the last accepted transfer window."""
    snapshot = AttachmentOutbox.objects.get(pk=event_id, event="erase")
    # Scope -> event is also the order used by deletion publication.
    scope = AttachmentScope.objects.select_for_update().get(pk=snapshot.scope_id)
    event = AttachmentOutbox.objects.select_for_update().get(
        pk=event_id, event="erase", scope=scope
    )
    now = timezone.now()
    if event.processed_at or event.available_at > now:
        return False
    upload = AttachmentUpload.objects.select_related("capture_session").get(
        pk=event.upload_id, scope=scope
    )
    if not _unreferenced(scope, upload.attachment_id):
        return False  # Never erase separately authorized survivors.
    if upload.state not in {
        AttachmentState.DELETED,
        AttachmentState.EXPIRED,
        AttachmentState.FAILED,
        AttachmentState.REJECTED,
    }:
        raise AttachmentError("cleanup_fenced", 409)
    storage.erase_upload(upload.id)
    event.attempts = min(event.attempts + 1, 65535)
    horizon = max(upload.expires_at, upload.capture_session.expires_at) + timedelta(
        seconds=TRANSFER_SECONDS + 15
    )
    if now >= horizon:
        event.processed_at = now
    else:
        event.available_at = min(horizon, now + timedelta(seconds=30))
    event.save(update_fields=["attempts", "processed_at", "available_at"])
    return True
