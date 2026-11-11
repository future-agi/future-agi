"""Content-free, idempotent operational receipts. No billing/edition changes."""

from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentAuditEvent,
    AttachmentBlob,
    AttachmentOutbox,
    AttachmentState,
    AttachmentUpload,
    AttachmentUsageReceipt,
    UploadReservation,
)
from tracer.services.attachments.contract import AttachmentError

EVENT_REASONS = {
    "admission": {"accepted"},
    "binding": {"bound"},
    "grant": {"issued"},
    "deletion": {"requested", "retention", "scope_deleted"},
    "settlement": {"ready", "failed", "rejected", "expired", "deleted"},
    "policy": {"changed"},
}


def audit(*, scope, actor_id, entity_id, event, reason, event_id=None):
    if event not in EVENT_REASONS or reason not in EVENT_REASONS[event]:
        raise AttachmentError("invalid_audit_event")
    if (actor_id is not None and not isinstance(actor_id, UUID)) or not isinstance(
        entity_id, UUID
    ):
        raise AttachmentError("invalid_audit_event")
    if event_id is not None and not isinstance(event_id, UUID):
        raise AttachmentError("invalid_audit_event")
    fields = {
        "actor_id": actor_id,
        "entity_id": entity_id,
        "event": event,
        "reason": reason,
    }
    row, created = AttachmentAuditEvent.objects.get_or_create(
        scope=scope, event_id=event_id or uuid4(), defaults=fields
    )
    if not created and any(getattr(row, key) != value for key, value in fields.items()):
        raise AttachmentError("idempotency_conflict", 409)
    return row


@transaction.atomic
def consume_settlement(event_id):
    event = AttachmentOutbox.objects.select_for_update().get(
        pk=event_id, event="settled"
    )
    upload = AttachmentUpload.objects.get(pk=event.upload_id, scope=event.scope)
    if not UploadReservation.objects.filter(
        upload=upload, scope=event.scope, settled_at__isnull=False
    ).exists():
        raise AttachmentError("unsettled_reservation", 409)
    existing = AttachmentUsageReceipt.objects.filter(
        upload=upload, scope=event.scope
    ).first()
    if existing:
        return existing
    # A delayed consumer can observe a deleted upload. The immutable verified blob
    # proves storage occurred even if deletion ran before accounting caught up.
    blob = AttachmentBlob.objects.filter(
        attachment=upload.attachment, scope=event.scope
    ).first()
    if upload.state not in {
        AttachmentState.READY,
        AttachmentState.FAILED,
        AttachmentState.REJECTED,
        AttachmentState.EXPIRED,
        AttachmentState.DELETED,
    }:
        raise AttachmentError("unsettled_reservation", 409)
    if upload.state == AttachmentState.READY and blob is None:
        raise AttachmentError("not_validated", 409)
    receipt, _ = AttachmentUsageReceipt.objects.get_or_create(
        upload=upload,
        defaults={
            "scope": event.scope,
            "stored_bytes": blob.byte_length if blob else 0,
            "outcome": AttachmentState.READY if blob else upload.state,
        },
    )
    audit(
        scope=event.scope,
        actor_id=None,
        entity_id=upload.id,
        event="settlement",
        reason=receipt.outcome,
        event_id=event.id,
    )
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at"])
    return receipt


def expire_reservations(*, limit=100):
    """Bounded sweeper; caller owns scheduling, retries and crash monitoring."""
    from tracer.services.attachments.uploads import settle

    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    rows = list(
        UploadReservation.objects.filter(
            settled_at__isnull=True, expires_at__lte=timezone.now()
        ).order_by("expires_at")[:limit]
    )
    changed = 0
    for row in rows:
        # READY publication may have preceded a worker crash. Reconcile verified
        # storage rather than reporting already-stored bytes as expired/free.
        upload = AttachmentUpload.objects.get(pk=row.upload_id, scope_id=row.scope_id)
        outcome = (
            AttachmentState.READY
            if upload.state == AttachmentState.READY
            else AttachmentState.EXPIRED
        )
        if upload.state == AttachmentState.DELETED:
            outcome = AttachmentState.DELETED
        elif upload.state in {
            AttachmentState.FAILED,
            AttachmentState.REJECTED,
            AttachmentState.EXPIRED,
        }:
            outcome = upload.state
        changed += bool(settle(upload.id, scope_id=row.scope_id, outcome=outcome))
    return changed
