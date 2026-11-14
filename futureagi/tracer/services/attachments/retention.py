"""Deletion primitives, deletion hooks and replayable erase jobs.

Hooks: ``tombstone_traces`` (trace and trace-session deletion),
``fence_projects`` (project deletion) and ``fence_workspace`` (workspace
deletion) are called by the product's PostgreSQL deletion paths. Retention that
only removes ClickHouse rows (TTL, the enterprise retention activities) is
caught by ``lifecycle.sweep_orphans``, which tombstones bound occurrences whose
parent span stayed missing across two sweeps ``orphan_grace()`` apart. Fail
closed on unknown retention provenance.
"""

from datetime import timedelta
from uuid import UUID

import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentBlob,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.occurrences import get_occurrence
from tracer.services.attachments.uploads import (
    TRANSFER_SECONDS,
    lock_all_budgets,
    settle,
)

logger = structlog.get_logger(__name__)

# Attachment-keyed erase work (no upload job: an imported recording segment) is
# repeated once this long after the tombstone. A derivative being produced at
# the moment of deletion is promoted before its fenced publication fails, so a
# single erase could miss it; the producer erases it too, but may die first.
ATTACHMENT_ERASE_HORIZON = timedelta(minutes=15)


def run_deletion_hook(hook, **kwargs):
    """Call a deletion hook without ever failing the product's own deletion.

    The hook runs in a savepoint, so a failure leaves the caller's transaction
    usable. The sweeps are the backstop: a deleted trace's spans leave
    ClickHouse (``sweep_orphans``) and unbound occurrences expire.
    """
    try:
        with transaction.atomic():
            return hook(**kwargs)
    except Exception:  # noqa: BLE001 - deletion boundary; the lifecycle sweeps retry
        logger.exception("attachment_deletion_hook_failed", hook=hook.__name__)
        return 0


def tombstone_deleted_traces(traces):
    """Hook for trace / trace-session deletion: ``traces`` have ``project_id`` and ``id``."""
    by_project: dict[UUID, list[UUID]] = {}
    for trace in traces:
        by_project.setdefault(trace.project_id, []).append(trace.id)
    return sum(
        run_deletion_hook(tombstone_traces, project_id=project_id, trace_ids=ids)
        for project_id, ids in by_project.items()
    )


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
    # The creator's ``now + 24 h`` is the UNBOUND deadline: the window in which
    # ingestion must observe the parent (checked above). Once bound, the
    # occurrence lives exactly as long as its authoritative source (design
    # section 8). ``min(row.expires_at, ...)`` here kept the pending deadline,
    # so every bound attachment was tombstoned and erased one day after upload.
    # The source expiry is never extended later: a rebind with a different
    # ``source.expires_at`` is fenced above.
    row.expires_at = source.expires_at
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
    # A second occurrence of an already tombstoned attachment (two expired
    # ``link`` occurrences in one sweep batch) must not touch the attachment
    # again: its tombstone is immutable (0133) and rewriting it would roll the
    # whole batch back and wedge the sweep on the same rows forever.
    if not Attachment.objects.filter(
        pk=row.attachment_id, scope=scope, deleted_at__isnull=True
    ).exists() or not _unreferenced(scope, row.attachment_id):
        return True
    upload = (
        AttachmentUpload.objects.select_for_update()
        .filter(scope=scope, attachment_id=row.attachment_id)
        .first()
    )
    if upload is not None:
        # Release unsettled reservations before marking READY storage deleted.
        settle(upload.id, scope_id=scope.id, outcome=AttachmentState.DELETED)
    Attachment.objects.filter(
        pk=row.attachment_id, scope=scope, deleted_at__isnull=True
    ).update(state=AttachmentState.DELETED, deleted_at=timezone.now())
    if upload is None:
        # No upload job (an imported recording segment): queue erase work keyed
        # on the attachment; ``erase`` removes its object and derivatives.
        AttachmentOutbox.objects.update_or_create(
            scope=scope,
            upload=None,
            attachment_id=row.attachment_id,
            event="erase",
            generation=scope.deletion_generation,
            defaults={"processed_at": None, "available_at": timezone.now()},
        )
        return True
    AttachmentUpload.objects.filter(pk=upload.id, scope=scope).update(
        state=AttachmentState.DELETED
    )
    AttachmentOutbox.objects.update_or_create(
        scope=scope,
        upload=upload,
        event="erase",
        generation=upload.deletion_generation,
        defaults={"processed_at": None, "available_at": timezone.now()},
        create_defaults={
            "processed_at": None,
            "available_at": timezone.now(),
            "attachment_id": upload.attachment_id,
        },
    )
    return True


@transaction.atomic
def delete_occurrence(viewer, authority, *, occurrence_id, parent):
    row, scope, _ = get_occurrence(viewer, authority, occurrence_id, parent, write=True)
    lock_all_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
    row, _, _ = get_occurrence(viewer, authority, occurrence_id, parent, write=True)
    return _tombstone(row, scope, actor_id=viewer.user_id, reason="requested")


BATCH = 100


def _tombstone_all(scope, rows_query, *, reason):
    """Tombstone every row of ``rows_query`` in batches of ``BATCH``.

    Each tombstoned row drops out of the query (``deleted_at`` is set), so the
    loop always terminates; one parent with many segments is never refused.
    """
    total = 0
    while True:
        rows = list(rows_query.filter(deleted_at__isnull=True).order_by("id")[:BATCH])
        total += sum(_tombstone(row, scope, reason=reason) for row in rows)
        if len(rows) < BATCH:
            return total


@transaction.atomic
def tombstone_parent(*, scope_id, parent, source_generation):
    """Internal authoritative deletion hook. Stale source versions cannot erase newer bindings."""
    if type(source_generation) is not int or source_generation < 0:
        raise AttachmentError("invalid_generation")
    scope = AttachmentScope.objects.get(pk=scope_id)
    lock_all_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = AttachmentOccurrence.objects.filter(
        scope=scope, trace_id=parent.trace_id, span_id=parent.span_id
    ).filter(
        Q(source_generation__isnull=True) | Q(source_generation__lte=source_generation)
    )
    return _tombstone_all(scope, rows, reason="retention")


def tombstone_traces(*, project_id, trace_ids):
    """Deletion hook: the product deleted these traces in PostgreSQL.

    That deletion is authoritative for every span of the trace, so no source
    generation guard applies (the guard protects newer bindings from a stale
    ClickHouse re-read, not from a user delete). One transaction per trace
    batch; a project without an attachment scope costs one indexed lookup.
    """
    scope = AttachmentScope.objects.filter(project_id=project_id).first()
    if scope is None or not trace_ids:
        return 0
    ids = sorted({UUID(str(value)) for value in trace_ids})
    total = 0
    for start in range(0, len(ids), BATCH):
        with transaction.atomic():
            lock_all_budgets(scope)
            locked = AttachmentScope.objects.select_for_update().get(pk=scope.pk)
            rows = AttachmentOccurrence.objects.filter(
                scope=locked, trace_id__in=ids[start : start + BATCH]
            )
            total += _tombstone_all(locked, rows, reason="requested")
    return total


@transaction.atomic
def tombstone_occurrences(*, scope_id, occurrence_ids, missing_before):
    """Orphan sweep: these bound occurrences lost their parent span in ClickHouse.

    Only rows whose span has been missing since ``missing_before`` or earlier
    are tombstoned, so a live read that cleared ``source_missing_since`` after
    the sweep's own read always wins.
    """
    scope = AttachmentScope.objects.get(pk=scope_id)
    lock_all_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = AttachmentOccurrence.objects.filter(
        scope=scope,
        pk__in=list(occurrence_ids),
        bound_at__isnull=False,
        source_missing_since__lte=missing_before,
    )
    return _tombstone_all(scope, rows, reason="retention")


def fence_projects(*, project_ids):
    """Deletion hook: deny reads at once, then let the sweep erase the bytes."""
    scope_ids = list(
        AttachmentScope.objects.filter(project_id__in=list(project_ids)).values_list(
            "pk", flat=True
        )
    )
    for scope_id in scope_ids:
        fence_scope(scope_id=scope_id)
    return len(scope_ids)


def fence_workspace(*, workspace_id):
    """Deletion hook for a workspace: every attachment scope inside it."""
    scope_ids = list(
        AttachmentScope.objects.filter(workspace_id=workspace_id).values_list(
            "pk", flat=True
        )
    )
    for scope_id in scope_ids:
        fence_scope(scope_id=scope_id)
    return len(scope_ids)


def _fenced(scope):
    return AttachmentOccurrence.objects.filter(
        scope=scope,
        deleted_at__isnull=True,
        deletion_generation__lt=scope.deletion_generation,
    )


def _expired(scope):
    now = timezone.now()
    return AttachmentOccurrence.objects.filter(
        scope=scope, deleted_at__isnull=True
    ).filter(Q(expires_at__lte=now) | Q(source_expires_at__lte=now))


@transaction.atomic
def tombstone_fenced(*, scope_id, limit=BATCH):
    """Physical cleanup after ``fence_scope``: occurrences from older generations."""
    if type(limit) is not int or not 1 <= limit <= BATCH:
        raise AttachmentError("invalid_request")
    scope = AttachmentScope.objects.get(pk=scope_id)
    if not _fenced(scope).exists():
        return 0  # Nothing to clean: skip the budget locks (sweep runs per scope).
    lock_all_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(_fenced(scope).order_by("id")[:limit])
    return sum(_tombstone(row, scope, reason="scope_deleted") for row in rows)


def purge_audit(*, before, limit=1000):
    """Drop audit rows older than ``before``; bounded per call."""
    ids = list(
        AttachmentAuditEvent.objects.filter(created_at__lt=before)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
    return AttachmentAuditEvent.objects.filter(pk__in=ids).delete()[0]


@transaction.atomic
def expire_occurrences(*, scope_id, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    scope = AttachmentScope.objects.get(pk=scope_id)
    if not _expired(scope).exists():
        return 0  # Nothing due: skip the budget locks (sweep runs per scope).
    lock_all_budgets(scope)
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(_expired(scope).order_by("id")[:limit])
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
    if event.upload_id is None:
        return _erase_attachment(event, scope, storage, now)
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


def _erase_attachment(event, scope, storage, now):
    """Attachment-keyed erase: the attachment has no upload job.

    Only the layout the server itself writes for such attachments is erased
    (an imported segment's ``original/{attachment id}`` and its derivatives);
    anything else is refused and stays queued for an owner to look at.
    """
    attachment = Attachment.objects.filter(pk=event.attachment_id, scope=scope).first()
    if attachment is None:
        raise AttachmentError("cleanup_unsupported", 409)
    if not _unreferenced(scope, attachment.id):
        return False  # Never erase separately authorized survivors.
    if attachment.state != AttachmentState.DELETED or attachment.deleted_at is None:
        raise AttachmentError("cleanup_fenced", 409)
    blob = AttachmentBlob.objects.filter(attachment=attachment, scope=scope).first()
    if blob is not None and blob.storage_key != f"original/{attachment.client_id}":
        raise AttachmentError("cleanup_unsupported", 409)
    # Imported here so retention keeps no import-time dependency on recordings.
    from tracer.services.attachments import derivatives

    storage.erase_upload(attachment.client_id)
    derivatives.erase_for_attachment(
        scope_id=scope.id, attachment_client_id=attachment.client_id, storage=storage
    )
    event.attempts = min(event.attempts + 1, 65535)
    horizon = attachment.deleted_at + ATTACHMENT_ERASE_HORIZON
    if now >= horizon:
        event.processed_at = now
    else:
        event.available_at = horizon
    event.save(update_fields=["attempts", "processed_at", "available_at"])
    return True
