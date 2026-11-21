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
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.attachment import (
    Attachment,
    AttachmentAuditEvent,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    CaptureSession,
)
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentRecording,
    AttachmentRecordingDerivative,
    AttachmentRecordingSegment,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import EraseResult, ObjectLocator
from tracer.services.attachments.occurrences import get_occurrence

logger = structlog.get_logger(__name__)

# Attachment-keyed erase work (no upload job: an imported recording segment) is
# repeated once this long after the tombstone. A derivative being produced at
# the moment of deletion is promoted before its fenced publication fails, so a
# single erase could miss it; the producer erases it too, but may die first.
ATTACHMENT_ERASE_HORIZON = timedelta(minutes=15, seconds=15)
TRANSFER_DRAIN = timedelta(seconds=75)


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
    snapshot = AttachmentOccurrence.objects.get(pk=occurrence_id, scope=scope)
    session = CaptureSession.objects.select_for_update().get(
        pk=snapshot.capture_session_id, scope=scope
    )
    attachment = Attachment.objects.select_for_update().get(
        pk=snapshot.attachment_id, scope=scope
    )
    row = AttachmentOccurrence.objects.select_for_update().get(
        pk=occurrence_id, scope=scope
    )
    now = timezone.now()
    if (
        not scope.enabled
        or session.revoked_at
        or attachment.deleted_at
        or attachment.state == AttachmentState.DELETED
        or attachment.deletion_generation != scope.deletion_generation
        or attachment.policy_version != scope.policy_version
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


def _survivors(scope, attachment_ids, now):
    """Potential survivors, not only parents that happen to answer right now.

    Unknown/missing CH authority, a remap outage, policy changes or a revoked
    viewer are NOT proof of physical unreachability. Only committed deletion
    generations, occurrence tombstones and the independent expiry clocks remove
    candidates here. Orphan misses are converted to tombstones by the spaced
    two-miss sweep, never by the eraser.
    """
    return AttachmentOccurrence.objects.filter(
        scope=scope,
        attachment_id__in=attachment_ids,
        deleted_at__isnull=True,
        expires_at__gt=now,
        deletion_generation__gte=scope.deletion_generation,
    ).filter(Q(source_expires_at__isnull=True) | Q(source_expires_at__gt=now))


def _unreferenced(scope, attachment_id):
    return not _survivors(scope, [attachment_id], timezone.now()).exists()


def _queue_asset_erase(scope, attachment, now):
    # Always asset-owned, including uploaded assets. Old upload-owned events
    # remain consumable by erase(); there are no new accounting-event hops.
    AttachmentOutbox.objects.update_or_create(
        scope=scope,
        upload=None,
        attachment=attachment,
        event="erase",
        generation=scope.deletion_generation,
        defaults={"processed_at": None, "available_at": now},
    )


def _tombstone_rows(scope, rows, *, actor_id=None, reason, eligible=None):
    """Scope is already locked. Lock assets before occurrences, once per batch.

    No capacity changes here: the terminal receipt is independent of cleanup,
    and capacity stays charged until its owner proves physical cleanup.
    """
    ids = [row.pk for row in rows]
    assets = list(
        Attachment.objects.select_for_update()
        .filter(scope=scope, pk__in={row.attachment_id for row in rows})
        .order_by("id")
    )
    rows = list(
        AttachmentOccurrence.objects.select_for_update()
        .filter(eligible or Q(), scope=scope, pk__in=ids, deleted_at__isnull=True)
        .order_by("id")
    )
    now = timezone.now()
    for row in rows:
        row.deleted_at = now
        row.save(update_fields=["deleted_at"])
    survivors = set(
        _survivors(scope, [asset.pk for asset in assets], now).values_list(
            "attachment_id", flat=True
        )
    )
    for asset in assets:
        if asset.pk in survivors:
            continue
        if asset.deleted_at is None:
            asset.deleted_at, asset.state = now, AttachmentState.DELETED
            asset.save(update_fields=["deleted_at", "state"])
        _queue_asset_erase(scope, asset, now)
    for row in rows:
        audit(
            scope=scope,
            actor_id=actor_id,
            entity_id=row.id,
            event="deletion",
            reason=reason,
        )
    return len(rows)


def _tombstone(row, scope, *, actor_id=None, reason):
    return bool(_tombstone_rows(scope, [row], actor_id=actor_id, reason=reason))


def delete_occurrence(viewer, authority, *, occurrence_id, parent):
    # Remote parent/session authorization happens before locks. The locked
    # source occurrence is checked again so a concurrent tombstone wins.
    row, scope, _ = get_occurrence(viewer, authority, occurrence_id, parent, write=True)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
        if (
            not scope.enabled
            or row.policy_version != scope.policy_version
            or row.deletion_generation != scope.deletion_generation
        ):
            raise AttachmentError("not_found", 404)
        return _tombstone(row, scope, actor_id=viewer.user_id, reason="requested")


BATCH = 100


def _tombstone_all(scope, rows_query, *, reason):
    """Lock the complete deletion set in order inside the caller's transaction.

    Fetch in bounded DB chunks, but do not start a second asset lock chain after
    the first batch's occurrence/outbox locks: an outer product transaction may
    retain all those locks even after a nested atomic savepoint exits. Scheduled
    sweeps pass at most BATCH rows; explicit parent deletion covers every link.
    """
    rows = list(
        rows_query.filter(deleted_at__isnull=True)
        .order_by("id")
        .iterator(chunk_size=BATCH)
    )
    return _tombstone_rows(scope, rows, reason=reason)


@transaction.atomic
def tombstone_parent(*, scope_id, parent, source_generation):
    """Internal authoritative deletion hook. Stale source versions cannot erase newer bindings."""
    if type(source_generation) is not int or source_generation < 0:
        raise AttachmentError("invalid_generation")
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
    with transaction.atomic():
        locked = AttachmentScope.objects.select_for_update().get(pk=scope.pk)
        rows = AttachmentOccurrence.objects.filter(scope=locked, trace_id__in=ids)
        return _tombstone_all(locked, rows, reason="requested")


@transaction.atomic
def tombstone_occurrences(*, scope_id, occurrence_ids, missing_before):
    """Orphan sweep: these bound occurrences lost their parent span in ClickHouse.

    Only rows whose span has been missing since ``missing_before`` or earlier
    are tombstoned, so a live read that cleared ``source_missing_since`` after
    the sweep's own read always wins.
    """
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = AttachmentOccurrence.objects.filter(
        scope=scope,
        pk__in=list(occurrence_ids),
        bound_at__isnull=False,
        source_missing_since__lte=missing_before,
    )
    return _tombstone_rows(
        scope,
        list(rows),
        reason="retention",
        eligible=Q(bound_at__isnull=False, source_missing_since__lte=missing_before),
    )


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
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(_fenced(scope).order_by("id")[:limit])
    return _tombstone_rows(scope, rows, reason="scope_deleted")


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
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    rows = list(_expired(scope).order_by("id")[:limit])
    return _tombstone_rows(scope, rows, reason="retention")


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


def _asset_allocations(scope, asset):
    # The scoped client-id join includes prepublication staging allocations whose
    # attachment FK is not assigned yet. It never selects a different scope.
    return AttachmentRecordingSegment.objects.filter(scope=scope).filter(
        Q(attachment_id=asset.pk)
        | Q(attachment_id__isnull=True, attachment_client_id=asset.client_id)
    )


def _lock_asset_chain(*, attachment_id, scope_id):
    """Scope -> jobs -> recordings -> uploads -> asset -> occurrences -> allocations.

    Caller owns transaction.atomic. No budgets are changed by this suffix; the
    separate settlement/derivative/import owners acquire capacity first when
    releasing their own reservations.
    """
    scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
    snapshot = Attachment.objects.get(pk=attachment_id, scope=scope)
    upload_ids = list(
        AttachmentUpload.objects.filter(attachment=snapshot, scope=scope).values_list(
            "id", flat=True
        )
    )
    staged = _asset_allocations(scope, snapshot)
    job_ids = set(staged.values_list("import_job_id", flat=True)) - {None}
    jobs = list(
        AttachmentImportJob.objects.select_for_update()
        .filter(Q(pk__in=job_ids) | Q(upload_id__in=upload_ids), scope=scope)
        .order_by("id")
    )
    recording_ids = set(staged.values_list("recording_id", flat=True))
    recording_ids.update(
        AttachmentUpload.objects.filter(pk__in=upload_ids).values_list(
            "recording_id", flat=True
        )
    )
    list(
        AttachmentRecording.objects.select_for_update()
        .filter(scope=scope, pk__in=recording_ids - {None})
        .order_by("id")
    )
    uploads = list(
        AttachmentUpload.objects.select_for_update()
        .filter(scope=scope, pk__in=upload_ids)
        .order_by("id")
    )
    asset = Attachment.objects.select_for_update().get(pk=attachment_id, scope=scope)
    list(
        AttachmentOccurrence.objects.select_for_update()
        .filter(scope=scope, attachment=asset)
        .order_by("id")
    )
    segments = list(_asset_allocations(scope, asset).select_for_update().order_by("id"))
    derivatives = list(
        AttachmentRecordingDerivative.objects.select_for_update()
        .filter(scope=scope, attachment=asset)
        .order_by("id")
    )
    return scope, asset, uploads, jobs, segments, derivatives


def _unpublished_failure(asset, uploads, segments, derivatives):
    """Terminal upload allocations, NOT media survivors or deletion authority.

    Failure metadata may remain bound/readable for the source's retention. It
    does not own published bytes. Under the ordered scope/upload/asset locks,
    terminal upload states fence future publication; the immutable verified
    tuple proves no original ever became readable. Any other allocation shape
    must use whole-asset survivor-protected cleanup instead.
    """
    terminal = {
        AttachmentState.FAILED,
        AttachmentState.REJECTED,
        AttachmentState.EXPIRED,
        AttachmentState.DELETED,
    }
    return (
        bool(uploads)
        and not segments
        and not derivatives
        and asset.state in terminal
        and asset.verified_blob_id is None
        and asset.verified_at is None
        and asset.verified_storage_key is None
        and asset.verified_storage_version is None
        and all(upload.state in terminal for upload in uploads)
    )


def _cleanup_plan(scope, asset, uploads, jobs, segments, derivatives):
    """Persisted locators only; no client UUID reconstruction or default binding."""
    if asset.storage_binding_id is None or not (uploads or segments):
        raise AttachmentError("storage_unverified", 503)
    locators = set()

    def add(binding_id, purpose, key):
        if binding_id != asset.storage_binding_id or not key:
            raise AttachmentError("storage_unverified", 503)
        locators.add(ObjectLocator(binding_id, purpose, key))

    if asset.verified_storage_key is not None:
        add(asset.storage_binding_id, "serving", asset.verified_storage_key)
    unpublished = _unpublished_failure(asset, uploads, segments, derivatives)
    if asset.deleted_at is None and not unpublished:
        raise AttachmentError("cleanup_fenced", 409)
    # A terminal unpublished allocation has its own persisted accepted-write
    # horizon. Do not tombstone failure metadata to manufacture an asset clock.
    horizons = [] if unpublished else [asset.deleted_at + ATTACHMENT_ERASE_HORIZON]
    signature: list[tuple[object, ...]] = [
        (
            scope.deletion_generation,
            asset.pk,
            unpublished,
            asset.state,
            asset.deleted_at,
            asset.storage_binding_id,
            asset.verified_storage_key,
            asset.verified_storage_version,
        )
    ]
    for upload in uploads:
        add(asset.storage_binding_id, "quarantine", upload.quarantine_key)
        add(asset.storage_binding_id, "serving", upload.serving_key)
        if upload.cleanup_not_before is None:
            raise AttachmentError("cleanup_unavailable", 503)
        horizons.extend([upload.cleanup_not_before, upload.expires_at + TRANSFER_DRAIN])
        if upload.write_deadline_at:
            horizons.append(upload.write_deadline_at + timedelta(seconds=15))
        signature.append(
            (
                upload.pk,
                upload.state,
                upload.allocation_epoch,
                upload.quarantine_key,
                upload.quarantine_version,
                upload.serving_key,
                upload.serving_version,
                upload.cleanup_not_before,
                upload.write_deadline_at,
                upload.quarantine_write_attempts,
                upload.serving_write_attempts,
            )
        )
    for job in jobs:
        if (
            job.storage_binding_id != asset.storage_binding_id
            or job.cleanup_not_before is None
        ):
            raise AttachmentError("storage_unverified", 503)
        horizons.append(job.cleanup_not_before)
        if job.lease_until:
            horizons.append(job.lease_until + timedelta(seconds=15))
        signature.append(
            (
                job.pk,
                job.storage_binding_id,
                job.lease_epoch,
                job.cleanup_not_before,
                job.lease_token,
                job.lease_until,
            )
        )
    for segment in segments:
        add(segment.storage_binding_id, "quarantine", segment.quarantine_key)
        add(segment.storage_binding_id, "serving", segment.storage_key)
        if segment.cleanup_not_before is None:
            raise AttachmentError("cleanup_unavailable", 503)
        horizons.append(segment.cleanup_not_before)
        if segment.write_deadline_at:
            horizons.append(segment.write_deadline_at + timedelta(seconds=15))
        signature.append(
            (
                segment.pk,
                segment.attachment_id,
                segment.import_job_id,
                segment.storage_binding_id,
                segment.allocation_epoch,
                segment.storage_key,
                segment.storage_version,
                segment.quarantine_key,
                segment.cleanup_not_before,
                segment.write_deadline_at,
                segment.write_attempts,
            )
        )
    for derivative in derivatives:
        # The derivative service performs its erasure, including unpublished
        # keys and capacity release. Its locators/epochs still fence this pass.
        if derivative.cleanup_not_before is None:
            raise AttachmentError("cleanup_unavailable", 503)
        horizons.append(derivative.cleanup_not_before)
        if derivative.write_deadline_at:
            horizons.append(derivative.write_deadline_at + timedelta(seconds=15))
        signature.append(
            (
                derivative.pk,
                derivative.lease_epoch,
                derivative.storage_key,
                derivative.storage_version,
                derivative.quarantine_key,
                derivative.cleanup_not_before,
                derivative.write_deadline_at,
                derivative.write_attempts,
            )
        )
    return (
        tuple(
            sorted(
                locators,
                key=lambda item: (str(item.binding_id), item.purpose, item.key),
            )
        ),
        max(horizons),
        tuple(signature),
    )


def _writers_stopped(uploads, jobs, derivatives):
    # A time comparison is not a process-termination receipt. Retired owner
    # phases AND cleared in-flight handles are required; producers/supervisors
    # clear them only after the accepted operation has stopped. A crashed or
    # ambiguous producer keeps cleanup pending for its own recovery sweep.
    if any(upload.transfer_started_at is not None for upload in uploads):
        return False
    if any(
        job.lease_phase != "terminal"
        or job.lease_token is not None
        or job.lease_until is not None
        for job in jobs
    ):
        return False
    if any(
        row.lease_token is not None or row.lease_until is not None
        for row in derivatives
    ):
        return False
    return not AttachmentOutbox.objects.filter(
        upload_id__in=[upload.pk for upload in uploads],
        event="validate",
        processed_at__isnull=True,
        attempts__gt=0,
    ).exists()


def _erase_originals(locators, *, storage, horizon):
    """All calls are outside DB locks. Partial/ambiguous/early emptiness is pending."""
    complete = True
    for locator in locators:
        result = storage.erase_all_versions(locator)
        if (
            not isinstance(result, EraseResult)
            or not result.empty
            or result.retryable
            or result.reason is not None
            or result.checked_at < horizon
            or result.checked_at > timezone.now()
        ):
            complete = False
    return complete


def _erase_asset(*, attachment_id, scope_id, now, storage=None):
    if connection.in_atomic_block:
        # An inner atomic exit does not commit an outer caller's tombstone.
        # Never do remote erasure until the irreversible fence is committed.
        raise AttachmentError("cleanup_fenced", 409)
    with transaction.atomic():
        chain = _lock_asset_chain(attachment_id=attachment_id, scope_id=scope_id)
        scope, asset, uploads, jobs, segments, derivatives = chain
        unpublished = _unpublished_failure(asset, uploads, segments, derivatives)
        if not unpublished and _survivors(scope, [asset.pk], now).exists():
            return False
        if not unpublished and asset.deleted_at is None:
            asset.deleted_at, asset.state = now, AttachmentState.DELETED
            asset.save(update_fields=["deleted_at", "state"])
        # Fence further upload publication without clearing active-operation
        # handles or pretending that cancellation stopped an in-flight PUT.
        for upload in uploads:
            if not unpublished and upload.state != AttachmentState.DELETED:
                upload.state = AttachmentState.DELETED
                upload.save(update_fields=["state"])
        locators, horizon, signature = _cleanup_plan(*chain)
    # The irreversible tombstone is committed before any network/storage call.
    from tracer.services.attachments import derivatives as derivative_service
    from tracer.services.attachments.readiness import resolve_historical

    if storage is None:
        storage = resolve_historical(asset.storage_binding_id, operation="erase")
    if storage.binding_id != asset.storage_binding_id:
        raise AttachmentError("storage_unverified", 503)
    storage.verify_identity(operation="erase")
    originals_empty = _erase_originals(locators, storage=storage, horizon=horizon)
    derivatives_empty = unpublished or derivative_service.erase_for_asset(
        attachment_id=attachment_id,
        scope_id=scope_id,
        now=now,
    )
    if not originals_empty or not derivatives_empty or now < horizon:
        return False
    with transaction.atomic():
        current = _lock_asset_chain(attachment_id=attachment_id, scope_id=scope_id)
        scope, asset, uploads, jobs, segments, derivatives = current
        current_locators, current_horizon, current_signature = _cleanup_plan(*current)
        if (
            current_signature != signature
            or current_locators != locators
            or current_horizon != horizon
            or timezone.now() < current_horizon
            or unpublished
            != _unpublished_failure(asset, uploads, segments, derivatives)
            or (
                not unpublished
                and (
                    asset.deleted_at is None
                    or asset.state != AttachmentState.DELETED
                    or _survivors(scope, [asset.pk], timezone.now()).exists()
                )
            )
            or not _writers_stopped(uploads, jobs, derivatives)
            or any(row.state != "erased" for row in derivatives)
        ):
            return False
        checked = timezone.now()
        for upload in uploads:
            upload.cleanup_checked_at = checked
            upload.unowned_cleanup_due_at = None
            upload.save(update_fields=["cleanup_checked_at", "unowned_cleanup_due_at"])
        for segment in segments:
            segment.state = "erased"
            segment.erased_at = segment.erased_at or checked
            segment.cleanup_checked_at = checked
            segment.unowned_cleanup_due_at = None
            segment.save(
                update_fields=[
                    "state",
                    "erased_at",
                    "cleanup_checked_at",
                    "unowned_cleanup_due_at",
                ]
            )
    # No locks are held across the owner's ordered capacity/receipt transaction.
    # ImportJob scratch and reservation remain the import sweep's responsibility.
    from tracer.services.attachments.uploads import settle

    for upload in uploads:
        settle(upload.pk, scope_id=scope_id, outcome=upload.state)
    return True


def erase_asset(*, attachment_id, scope_id, now=None):
    """Unified uploaded/imported original, staging and representation erasure."""
    return _erase_asset(
        attachment_id=attachment_id, scope_id=scope_id, now=now or timezone.now()
    )


def erase(event_id, *, storage=None):
    """Select event and owner before historical binding resolution; no default gate.

    True means terminal completion, not merely a successful attempt. Historical
    upload-keyed events and new asset-keyed events share the same cleanup path.
    """
    event = AttachmentOutbox.objects.get(pk=event_id, event="erase")
    now = timezone.now()
    if event.processed_at or event.available_at > now:
        return False
    attachment_id = event.attachment_id
    if event.upload_id:
        upload = AttachmentUpload.objects.get(
            pk=event.upload_id, scope_id=event.scope_id
        )
        if attachment_id is not None and upload.attachment_id != attachment_id:
            raise AttachmentError("cleanup_fenced", 409)
        attachment_id = upload.attachment_id
    if attachment_id is None:
        raise AttachmentError("cleanup_fenced", 409)
    generation = AttachmentScope.objects.get(pk=event.scope_id).deletion_generation
    finished = _erase_asset(
        attachment_id=attachment_id, scope_id=event.scope_id, now=now, storage=storage
    )
    with transaction.atomic():
        chain = _lock_asset_chain(attachment_id=attachment_id, scope_id=event.scope_id)
        scope, asset, uploads, jobs, segments, derivatives = chain
        current = AttachmentOutbox.objects.select_for_update().get(
            pk=event_id, event="erase"
        )
        if current.processed_at:
            return False
        if (
            current.scope_id,
            current.upload_id,
            current.attachment_id,
            current.generation,
        ) != (event.scope_id, event.upload_id, event.attachment_id, event.generation):
            raise AttachmentError("cleanup_fenced", 409)
        current.attempts = min(current.attempts + 1, 65535)
        if finished:
            _, horizon, _ = _cleanup_plan(*chain)
            finished = (
                scope.deletion_generation == generation
                and (
                    _unpublished_failure(asset, uploads, segments, derivatives)
                    or (
                        asset.state == AttachmentState.DELETED
                        and asset.deleted_at is not None
                        and not _survivors(scope, [asset.pk], timezone.now()).exists()
                    )
                )
                and timezone.now() >= horizon
                and _writers_stopped(uploads, jobs, derivatives)
                and all(row.state == "erased" for row in derivatives)
                and all(
                    row.cleanup_checked_at is not None
                    and row.cleanup_checked_at >= horizon
                    for row in uploads + segments
                )
            )
        if finished:
            current.processed_at = timezone.now()
        else:
            current.available_at = timezone.now() + timedelta(seconds=60)
        current.save(update_fields=["attempts", "processed_at", "available_at"])
    return finished
