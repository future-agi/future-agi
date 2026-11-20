"""Content-free same-transaction receipts; historical outbox compatibility only."""

from uuid import UUID, uuid4

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentOutbox,
    AttachmentState,
    AttachmentUpload,
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
TERMINAL = frozenset(EVENT_REASONS["settlement"])


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


def admit_locked(upload):
    if (
        not transaction.get_connection().in_atomic_block
        or upload.admission_event_id is None
    ):
        raise RuntimeError("admission requires an atomic durable event identity")
    return audit(
        scope=upload.scope,
        actor_id=None,
        entity_id=upload.id,
        event="admission",
        reason="accepted",
        event_id=upload.admission_event_id,
    )


def receipt_locked(upload, *, attachment, outcome, event_id=None):
    """First terminal outcome wins, independently of operational deletion/cleanup.

    Caller holds Upload then Attachment. A complete immutable publication is
    evidence of READY even when delayed historical accounting sees DELETED.
    Never replace historical receipt IDs/times with this recovery's time.
    """
    if not transaction.get_connection().in_atomic_block or outcome not in TERMINAL:
        raise ValueError("atomic terminal settlement required")
    if (attachment.id, attachment.scope_id) != (upload.attachment_id, upload.scope_id):
        raise AttachmentError("not_found", 404)
    if event_id is not None and upload.settlement_event_id not in (None, event_id):
        raise AttachmentError("idempotency_conflict", 409)
    changed = upload.usage_id is None
    if changed:
        if attachment.verified_blob_id is not None:
            if (
                attachment.verified_byte_length is None
                or attachment.verified_storage_version is None
            ):
                raise AttachmentError("not_validated", 409)
            outcome, stored = AttachmentState.READY, attachment.verified_byte_length
        elif outcome == AttachmentState.READY:
            raise AttachmentError("not_validated", 409)
        else:
            stored = 0
        upload.usage_id = uuid4()
        upload.usage_created_at = timezone.now()
        upload.usage_outcome = outcome
        upload.usage_stored_bytes = stored
        upload.settlement_event_id = upload.settlement_event_id or event_id or uuid4()
        upload.save(
            update_fields=[
                "usage_id",
                "usage_created_at",
                "usage_outcome",
                "usage_stored_bytes",
                "settlement_event_id",
            ]
        )
    elif upload.settlement_event_id is None:
        upload.settlement_event_id = event_id or uuid4()
        upload.save(update_fields=["settlement_event_id"])
    audit(
        scope=upload.scope,
        actor_id=None,
        entity_id=upload.id,
        event="settlement",
        reason=upload.usage_outcome,
        event_id=upload.settlement_event_id,
    )
    return changed


@transaction.atomic
def consume_settlement(event_id):
    # Discovery is unlocked. Never take Outbox and then wait on Upload/deletion.
    ref = AttachmentOutbox.objects.get(pk=event_id, event="settled")
    if ref.upload_id is None:
        raise AttachmentUpload.DoesNotExist
    upload = AttachmentUpload.objects.select_for_update().get(
        pk=ref.upload_id, scope_id=ref.scope_id
    )
    attachment = Attachment.objects.select_for_update().get(
        pk=upload.attachment_id, scope_id=ref.scope_id
    )
    event = AttachmentOutbox.objects.select_for_update().get(
        pk=event_id, event="settled", upload_id=upload.id, scope_id=upload.scope_id
    )
    if upload.state not in TERMINAL and upload.usage_id is None:
        raise AttachmentError("unsettled_reservation", 409)
    receipt_locked(
        upload,
        attachment=attachment,
        outcome=upload.usage_outcome or upload.state,
        event_id=event.id,
    )
    if event.processed_at is None:
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
    return upload


@transaction.atomic
def consume_admission(event_id):
    ref = AttachmentOutbox.objects.filter(pk=event_id, event="admitted").first()
    if ref is None:
        return False
    upload = AttachmentUpload.objects.select_for_update().get(
        pk=ref.upload_id, scope_id=ref.scope_id
    )
    event = AttachmentOutbox.objects.select_for_update().get(
        pk=event_id, event="admitted", upload_id=upload.id, scope_id=upload.scope_id
    )
    if upload.admission_event_id not in (None, event.id):
        raise AttachmentError("idempotency_conflict", 409)
    if upload.admission_event_id is None:
        upload.admission_event_id = event.id
        upload.save(update_fields=["admission_event_id"])
    admit_locked(upload)
    changed = event.processed_at is None
    if changed:
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
    return changed


def expire_reservations(*, limit=100):
    from tracer.services.attachments.uploads import settle

    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    # Serve first-time expiry before any historical cleanup repair. A terminal
    # owner with pending cleanup must never occupy that first-time batch.
    due = AttachmentUpload.objects.filter(
        reservation_settled_at__isnull=True,
        reservation_expires_at__lte=timezone.now(),
    ).order_by("reservation_expires_at", "id")
    rows = list(due.filter(usage_id__isnull=True)[:limit])
    if len(rows) < limit:
        from django.db.models import Exists, OuterRef, Q

        pending = AttachmentOutbox.objects.filter(
            scope_id=OuterRef("scope_id"), processed_at__isnull=True
        ).filter(
            Q(upload_id=OuterRef("id"), event__in=["validate", "erase"])
            | Q(attachment_id=OuterRef("attachment_id"), event="erase")
        )
        # Keep the old recovery path for terminal rows that have NO cleanup
        # worker, e.g. a legacy failed upload with usage but no erase event.
        # settle never erases a READY original or releases without proof.
        repair = (
            due.filter(usage_id__isnull=False)
            .filter(~Exists(pending))
            .filter(
                ~Q(usage_outcome=AttachmentState.READY)
                | Q(cleanup_checked_at__isnull=False)
            )
        )
        rows.extend(repair[: limit - len(rows)])
    changed = 0
    for upload in rows:
        outcome = upload.usage_outcome or (
            upload.state if upload.state in TERMINAL else AttachmentState.EXPIRED
        )
        changed += bool(settle(upload.id, scope_id=upload.scope_id, outcome=outcome))
    return changed
