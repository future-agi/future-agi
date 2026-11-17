"""Mandatory Postgres lifecycle protocol for B07, list readers and B18.

Lock order is transaction -> project advisory lock -> refresh row -> attempt.
Publication/activation and capture use the SAME lock. Locks outlive this helper
and end only with the enclosing transaction. Never hold a pin across external
purge without rechecking ``is_usable``; a revoked/removed pin fails as expired.

* Readers call capture_pin(metric_id=...) to freeze active version, head and the
  head's remap revision together. pin_head is a compatibility wrapper, not an
  alternate unsynchronized path. Raw pin inserts/updates are not supported.
* Erasure/retention call revoke_generations BEFORE physical deletion, under
  project_lock. Revocation permanently fences captures AND affected open writers,
  including compactions whose payload was frozen before erasure. A replacement
  checkpoint must be prepared afresh and rebuilt from the post-erasure source.
* B07 calls collect_history after checkpoint publication. It proves reachability
  from head, live pins and recoverable attempts; only expired unreachable history
  is detached. Frozen audit IDs/hashes are never changed. The returned outbox
  intent, not an in-memory list, drives B07's external-store GC after commit.
* The Collector integration refreshes its dependency graph under the project
  locks BEFORE pre_delete and holds them through commit. This includes rows
  committed between the original collection and deletion, for QuerySet, instance
  and parent-cascade hard deletes. pre_delete then enqueues parent-independent
  cleanup without navigation FKs. Run.definition is CASCADE, not PROTECT.
  BaseModel instance soft-delete behavior is unchanged.

This module does NOT claim physical erasure. Only B18's worker may finish an
intent after all stores have receipts. All hooks participate in delete rollback.
"""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps
from time import sleep

from django.apps import apps
from django.db import (
    DEFAULT_DB_ALIAS,
    IntegrityError,
    OperationalError,
    connections,
    transaction,
)
from django.db.models import OuterRef, Q, Subquery
from django.db.models.deletion import Collector
from django.db.models.signals import pre_delete
from django.utils import timezone

from tracer.models.derived_metric import (
    PIN_MAX_LIFETIME,
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)

_lock_budget = ContextVar("metric_lifecycle_lock_budget", default=None)


@contextmanager
def lifecycle_lock_budget(lease):
    """Carry the writer's live lease/activity budget through model lock calls."""
    token = _lock_budget.set(lease)
    try:
        with connections[DEFAULT_DB_ALIAS].execute_wrapper(_budgeted_row_lock):
            yield
    finally:
        _lock_budget.reset(token)


def _budgeted_row_lock(execute, sql, params, many, context):
    """Bound ORM row-lock waits too, including UPDATEs outside explicit atomics.

    The setting is transaction-local (safe through transaction-mode PgBouncer),
    refreshed for EACH statement, and restored before any native IO can begin.
    Advisory try-lock polling uses the same lease but needs no SQL timeout.
    """
    lease = _lock_budget.get()
    statement = sql.lstrip().upper()
    if lease is None or not (
        statement.startswith(("UPDATE ", "INSERT ", "DELETE "))
        or (statement.startswith("SELECT ") and "FOR UPDATE" in statement)
    ):
        return execute(sql, params, many, context)
    milliseconds = max(1, int(lease.remaining_seconds() * 1000))
    db = context["connection"]
    try:
        with transaction.atomic(using=db.alias):
            # Use the driver cursor: these control statements must not recurse
            # through the ORM execute wrapper or expose project/private values.
            with db.connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                previous = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)", [f"{milliseconds}ms"]
                )
                result = execute(sql, params, many, context)
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)", [previous]
                )
                return result
    except OperationalError as exc:
        if (
            getattr(exc.__cause__, "sqlstate", None)
            or getattr(exc.__cause__, "pgcode", None)
        ) != "55P03":
            raise
        lease.remaining_seconds()
        from tracer.services.derived_metrics.lease import MetricPassDeadline

        raise MetricPassDeadline() from None


def try_control_update(queryset, **updates):
    """Best-effort fenced bookkeeping; never wait behind a stalled reader.

    Skipped cleanup remains recoverable by lease expiry. Do not reset an epoch
    or acknowledge work when this returns zero. Call outside a failed atomic.
    """
    token = _lock_budget.set(None)
    try:
        with transaction.atomic(using=queryset.db):
            db = connections[queryset.db]
            with db.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                previous = cursor.fetchone()[0]
                cursor.execute("SELECT set_config('lock_timeout', '100ms', true)")
            ids = list(
                queryset.select_for_update(skip_locked=True).values_list(
                    "pk", flat=True
                )
            )
            moved = queryset.filter(pk__in=ids).update(**updates) if ids else 0
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)", [previous]
                )
            return moved
    except OperationalError as exc:
        if (
            getattr(exc.__cause__, "sqlstate", None)
            or getattr(exc.__cause__, "pgcode", None)
        ) != "55P03":
            raise
        return 0
    finally:
        _lock_budget.reset(token)


def project_lock(project_id, *, using=DEFAULT_DB_ALIAS, blocking=True):
    """Acquire the project serialization lock inside an existing transaction.

    Advisory rather than a parent FK lock: works before refresh-row creation and
    while a parent is being deleted. Hash collisions serialize extra projects,
    never weaken isolation. All IDs/subjects remain out of SQL diagnostic text.
    """
    db = connections[using]
    if not db.in_atomic_block:
        raise RuntimeError("project lock requires a transaction")
    key = _project_lock_key(project_id)
    with db.cursor() as cursor:
        if not blocking:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
            return cursor.fetchone()[0]
        lease = _lock_budget.get()
        if lease is None:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
            return True
        # Cooperative checks before SQL alone cannot interrupt a blocking lock.
        # Try-locks keep the transaction usable, and the shared lease object
        # accounts for renewals without ever extending the activity deadline.
        while True:
            lease.remaining_seconds()
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
            if cursor.fetchone()[0]:
                lease.remaining_seconds()
                return True
            sleep(min(0.05, lease.remaining_seconds()))


def _project_lock_key(project_id):
    return int.from_bytes(
        hashlib.sha256(f"metric-lifecycle:{project_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )


@contextmanager
def serialized_project(project_id, *, using=DEFAULT_DB_ALIAS):
    """Public transaction boundary for publication, purge planning and capture."""
    with transaction.atomic(using=using):
        project_lock(project_id, using=using)
        yield


def capture_pin(
    project_id,
    *,
    consumer_kind,
    ttl: timedelta,
    consumer_ref="",
    metric_id=None,
    definition_version_id=None,
    now=None,
):
    """Atomically capture a consistent active definition/head/remap, or expire.

    An explicit version is an expected-active CAS, not permission to pin a draft
    or retired version. Callers that need the active version pass metric_id.
    """
    if not timedelta(0) < ttl <= PIN_MAX_LIFETIME:
        raise ValueError("pin ttl must be positive and at most 24 hours")
    if consumer_kind not in MetricGenerationPin.Consumer.values:
        raise ValueError("unknown pin consumer")
    with serialized_project(project_id):
        # Take time after waiting for the lock, unless a test supplies a clock.
        now = now or timezone.now()
        refresh = (
            MetricProjectRefresh.objects.select_for_update()
            .filter(project_id=project_id)
            .first()
        )
        if refresh is None or refresh.head_manifest_id is None:
            raise MetricStateConflict("no committed refresh to pin yet")
        head = MetricReadManifest.objects.get(id=refresh.head_manifest_id)
        if head.revoked_at is not None:
            raise MetricStateConflict("expired generation")
        if metric_id is not None or definition_version_id is not None:
            metrics = DerivedMetric._base_manager.filter(
                project_id=project_id,
                deleted=False,
                active_version__pins_revoked_at__isnull=True,
                active_version__isnull=False,
            )
            if metric_id is not None:
                metrics = metrics.filter(id=metric_id)
            if definition_version_id is not None:
                metrics = metrics.filter(active_version_id=definition_version_id)
            active_id = metrics.values_list("active_version_id", flat=True).first()
            if active_id is None:
                raise MetricStateConflict("expired or inactive definition")
            definition_version_id = active_id
        return MetricGenerationPin.objects.create(
            project_id=project_id,
            manifest=head,
            definition_version_id=definition_version_id,
            remap_revision=head.remap_revision,
            consumer_kind=consumer_kind,
            consumer_ref=consumer_ref,
            captured_at=now,
            expires_at=now + ttl,
        )


def revoke_generations(
    project_id,
    *,
    reason,
    manifest_ids=None,
    definition_version_ids=None,
    now=None,
    using=DEFAULT_DB_ALIAS,
):
    """Durably fence captures, even if there were no pins at revocation time.

    Manifest and definition selectors intersect for existing pins. A definition
    selector permanently retires those versions, not unrelated project versions.
    No selectors means every existing project generation. Affected open writers
    are aborted in the same transaction: an old verified compaction is not a safe
    replacement checkpoint. Definition revocation conservatively aborts all open
    project writers, since their shared field/bucket payload may include it.
    A new complete checkpoint may be prepared after revocation; its worker must
    rebuild from current source data, never copy a revoked base. Never un-revoke
    an old row.
    """
    if reason not in MetricGenerationPin.Invalidation.values or not reason:
        raise ValueError("unknown invalidation reason")
    with serialized_project(project_id, using=using):
        now = now or timezone.now()
        pins = MetricGenerationPin.objects.using(using).filter(
            project_id=project_id,
            invalidated_at__isnull=True,
        )
        if manifest_ids is not None:
            manifest_ids = list(manifest_ids)
            pins = pins.filter(manifest_id__in=manifest_ids)
        writers = MetricRefreshAttempt.objects.using(using).filter(
            project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
        )
        if definition_version_ids is not None:
            definition_version_ids = list(definition_version_ids)
            versions = DerivedMetricVersion.objects.using(using).filter(
                metric__project_id=project_id, id__in=definition_version_ids
            )
            if not versions.exists():
                writers = writers.none()
            versions.filter(pins_revoked_at__isnull=True).update(pins_revoked_at=now)
            pins = pins.filter(definition_version_id__in=definition_version_ids)
        else:
            manifests = MetricReadManifest.objects.using(using).filter(
                project_id=project_id,
                revoked_at__isnull=True,
            )
            if manifest_ids is not None:
                manifests = manifests.filter(id__in=manifest_ids)
                writers = writers.filter(parent_manifest_id__in=manifest_ids)
            manifests.update(revoked_at=now, revocation_reason=reason)
        if (
            reason == "erased"
            or definition_version_ids is not None
            or manifest_ids is None
        ):
            from tracer.services.derived_metrics.backfill import scrub_private_progress

            scrub_private_progress(
                project_id,
                now=now,
                versions=definition_version_ids,
                using=using,
            )
        writers.update(
            state=MetricRefreshAttempt.State.ABORTED,
            aborted_at=now,
            abort_reason="generation_revoked",
        )
        return pins.update(invalidated_at=now, invalidation_reason=reason)


def commit_logical_expiry(attempt, manifest, *, now):
    """Part of publish's transaction, NEVER a post-publication callback.

    Cleanup is generation-scoped and independent of collected private ledgers.
    Only project/attempt namespaces survive, not subjects or matching hashes.
    """
    if not connections[DEFAULT_DB_ALIAS].in_atomic_block:
        raise RuntimeError("logical expiry requires the publication transaction")
    old = list(
        MetricReadManifest.objects.filter(project_id=attempt.project_id)
        .exclude(id=manifest.id)
        .values_list("id", flat=True)
    )
    revoke_generations(
        attempt.project_id, reason="retention_expired", manifest_ids=old, now=now
    )
    from tracer.services.derived_metrics.backfill import (
        expiry_floor,
        scrub_expired_progress,
        scrub_private_progress,
    )

    # Selective: drop only private keysets this horizon expired. Progress stays,
    # or steady traffic (every pass an expiry) would block backfill forever.
    floor = expiry_floor(attempt)
    if floor is None:
        scrub_private_progress(attempt.project_id, now=now)
    else:
        scrub_expired_progress(attempt.project_id, cutoff=floor, now=now)
    # Include writers allocated AFTER this checkpoint was prepared as well as
    # writers with no parent manifest. Allocation order is not publication order.
    MetricRefreshAttempt.objects.filter(
        project_id=attempt.project_id,
        state__in=MetricRefreshAttempt.OPEN_STATES,
    ).update(state="aborted", aborted_at=now, abort_reason="generation_revoked")
    through = MetricProjectRefresh.objects.get(
        project_id=attempt.project_id
    ).generation_high_water
    project = apps.get_model("tracer", "Project")._base_manager.get(
        id=attempt.project_id
    )
    return _intent(
        organization_id=project.organization_id,
        project_id=attempt.project_id,
        scope="history",
        deletion_key=f"expiry:{attempt.id}",
        store_payload={
            "protocol": "metric_expiry",
            "through_generation": through,
            "checkpoint_attempt": str(attempt.id),
            "shard": manifest.visibility_receipts.get("shard"),
        },
    )


def _intent(
    *,
    organization_id,
    project_id=None,
    scope,
    deletion_key=None,
    using=DEFAULT_DB_ALIAS,
    **payload,
):
    """No navigation FKs, including for new rows created during pre_delete."""
    if "store_payload" in payload:
        payload["store_payload"] = json.loads(
            json.dumps(
                payload["store_payload"],
                default=str,
            )
        )
    values = dict(
        organization_key=organization_id,
        project_key=project_id,
        scope_kind=scope,
        **payload,
    )
    manager = MetricErasureRequest.objects.using(using)
    if deletion_key is not None:
        existing = manager.filter(deletion_key=deletion_key).first()
        if existing is not None:
            return existing
    request = MetricErasureRequest(deletion_key=deletion_key, **values)
    # This dedicated outbox insert intentionally bypasses save signals: the
    # global post_save receiver assigns an ambient organization to normal rows
    # with an empty navigation FK. Clearing it afterward would still introduce
    # the disappearing FK and can fail before it is cleared. Bulk insertion
    # applies field defaults but emits no pre_save/post_save model signals.
    try:
        with transaction.atomic(using=using):
            manager.bulk_create([request])
    except IntegrityError:
        # Preserve get_or_create's idempotency under concurrent org deletions,
        # without suppressing unrelated constraint failures.
        if deletion_key is not None:
            existing = manager.filter(deletion_key=deletion_key).first()
            if existing is not None:
                return existing
        raise
    return request


def collect_history(project_id, *, now=None, limit=1000):
    """Detach/collect expired unreachable PG history and persist external GC work.

    A checkpoint is mandatory; resetting the delta list alone is not proof. A
    nonexpired open attempt retains its parent AND that parent's read attempts.
    Pins retain only their read manifest/allowlist, not informational ancestors.
    Expiry aborts abandoned open attempts before detachment. At most ``limit``
    pins, manifests and attempts each are removed per call. Pin collection also
    runs when there is no collectible history; it never creates external-store
    work. Remaining stale pins conservatively retain their manifest until the
    next bounded pin batch. Repeat calls to drain large backlogs.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("collection limit must be between 1 and 1000")
    with serialized_project(project_id):
        now = now or timezone.now()
        # Completed synchronous submissions can never land later. Their absence
        # after collection is safe; unresolved submissions are independent roots
        # and survive even when their private attempt/manifest has been removed.
        MetricErasureRequest.objects.filter(
            Q(receipts__synchronous_response__drained=1)
            | Q(receipts__terminal_query__drained=1),
            project_key=project_id,
            store_payload__protocol="metric_submission",
            status="done",
        ).delete()
        MetricErasureRequest.objects.filter(
            project_key=project_id,
            store_payload__protocol="metric_checkpoint_layout",
            status="done",
            receipts__layout__drained=1,
        ).delete()
        refresh = MetricProjectRefresh.objects.select_for_update().get(
            project_id=project_id
        )
        if not refresh.head_manifest_id:
            raise MetricStateConflict("collection requires a committed checkpoint")
        head = MetricReadManifest.objects.get(id=refresh.head_manifest_id)
        if not head.checkpoint_attempt_id or head.revoked_at is not None:
            raise MetricStateConflict("collection requires a usable checkpoint")
        attempts = MetricRefreshAttempt.objects.filter(project_id=project_id)
        recoverable = attempts.filter(
            state__in=MetricRefreshAttempt.OPEN_STATES,
            expires_at__gt=now,
        )
        pins = MetricGenerationPin.objects.filter(project_id=project_id)
        stale_pins = list(
            pins.filter(
                Q(expires_at__lte=now)
                | Q(invalidated_at__isnull=False)
                | Q(manifest__revoked_at__isnull=False)
                | Q(definition_version__pins_revoked_at__isnull=False)
            )
            .order_by("expires_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        pins.filter(id__in=stale_pins).delete()
        # Even an unusable pin left behind by the bounded batch has a RESTRICT
        # FK. Keep its manifest until a later call removes that pin as well.
        roots = {head.id}
        roots.update(pins.values_list("manifest_id", flat=True))
        roots.update(
            recoverable.exclude(parent_manifest_id=None).values_list(
                "parent_manifest_id",
                flat=True,
            )
        )
        manifests = MetricReadManifest.objects.filter(project_id=project_id)
        candidates = list(
            manifests.exclude(id__in=roots)
            .filter(
                published_by__expires_at__lte=now,
            )
            .order_by("generation")
            .values_list("id", flat=True)[:limit]
        )
        retained = list(manifests.exclude(id__in=candidates))
        keep_attempts = set(recoverable.values_list("id", flat=True))
        for manifest in retained:
            keep_attempts.add(manifest.published_by_id)
            keep_attempts.update(a for _, a in manifest.allowed_attempts())
        expired_attempts = list(
            attempts.exclude(id__in=keep_attempts)
            .filter(
                expires_at__lte=now,
            )
            .order_by("generation")
            .values_list("id", flat=True)[:limit]
        )
        # If a candidate's publishing attempt cannot be removed in this batch,
        # it is safe to retain that attempt without its published manifest.
        if not candidates and not expired_attempts:
            return None
        revoke_generations(
            project_id, reason="retention_expired", manifest_ids=candidates, now=now
        )
        attempts.filter(
            state__in=MetricRefreshAttempt.OPEN_STATES,
            expires_at__lte=now,
        ).update(state="aborted", aborted_at=now, abort_reason="attempt_expired")
        # ONLY read-retaining links change. Audit keys and hashes remain frozen.
        attempts.exclude(state__in=MetricRefreshAttempt.OPEN_STATES).filter(
            parent_manifest_id__in=candidates
        ).update(parent_manifest_id=None)
        manifests.filter(parent_id__in=candidates).update(parent_id=None)
        project = apps.get_model("tracer", "Project")._base_manager.get(id=project_id)
        intent = _intent(
            organization_id=project.organization_id,
            project_id=project_id,
            scope="history",
            store_payload={
                "manifests": list(
                    manifests.filter(id__in=candidates).values(
                        "id",
                        "generation",
                        "manifest_sha256",
                    )
                ),
                "attempts": list(
                    attempts.filter(id__in=expired_attempts).values(
                        "id",
                        "generation",
                        "prepared_sha256",
                        "ledger_sha256",
                    )
                ),
            },
        )
        manifests.filter(id__in=candidates).delete()
        attempts.filter(id__in=expired_attempts).delete()
        return intent


def unreachable_closed_attempts(project_id, *, now, grace, limit=100):
    """Closed attempts whose ClickHouse rows no reader can reach, past ``grace``.

    A reader reaches metric rows only through a manifest's allowlist: the
    committed head, a pin's manifest (stale pins included, conservatively) or
    an open attempt's parent. A committed or aborted attempt outside all of them
    can never become reachable again, since a pin captures only the head and the
    head never moves back. Its PG row, ledger and audit hashes stay for
    collect_history's ATTEMPT_LEDGER_RETENTION; only its ClickHouse rows are
    collected, once, after ``grace`` (longer than a lease, a request pin and a
    statement). Like collect_history, this needs a committed checkpoint.

    The grace runs from when the head stopped reading the attempt, not from
    when it committed: a committed attempt left every head allowlist when the
    first later checkpoint (purpose compaction, which restarts the allowlist)
    was published. An aborted attempt was never in one.
    """
    with serialized_project(project_id):
        refresh = MetricProjectRefresh.objects.filter(project_id=project_id).first()
        if refresh is None or refresh.head_manifest_id is None:
            return []
        head = MetricReadManifest.objects.get(id=refresh.head_manifest_id)
        if head.checkpoint_attempt_id is None:
            return []
        attempts = MetricRefreshAttempt.objects.filter(project_id=project_id)
        open_attempts = attempts.filter(state__in=MetricRefreshAttempt.OPEN_STATES)
        roots = {head.id}
        roots.update(
            MetricGenerationPin.objects.filter(project_id=project_id).values_list(
                "manifest_id", flat=True
            )
        )
        roots.update(
            open_attempts.exclude(parent_manifest_id=None).values_list(
                "parent_manifest_id", flat=True
            )
        )
        keep = set(open_attempts.values_list("id", flat=True))
        for manifest in MetricReadManifest.objects.filter(id__in=roots):
            keep.add(manifest.published_by_id)
            keep.update(attempt for _, attempt in manifest.allowed_attempts())
        cutoff = now - grace
        superseded = Subquery(
            attempts.filter(
                purpose=MetricRefreshAttempt.Purpose.COMPACTION,
                state=MetricRefreshAttempt.State.COMMITTED,
                generation__gt=OuterRef("generation"),
            )
            .order_by("generation")
            .values("committed_at")[:1]
        )
        return list(
            attempts.exclude(id__in=keep)
            .filter(rows_collected_at__isnull=True)
            .annotate(superseded_at=superseded)
            .filter(
                Q(
                    state=MetricRefreshAttempt.State.COMMITTED,
                    superseded_at__lte=cutoff,
                )
                | Q(state=MetricRefreshAttempt.State.ABORTED, aborted_at__lte=cutoff)
            )
            .order_by("generation")
            .values_list("id", flat=True)[:limit]
        )


def _before_delete(sender, instance, using, **kwargs):
    """Collector-safe signal; model instances may already have deferred fields."""
    if sender is DerivedMetricRun:
        version = DerivedMetricVersion.objects.using(using).get(
            id=instance.definition_id
        )
        metric = DerivedMetric._base_manager.using(using).get(id=version.metric_id)
        scope = "run"
        payload = {
            "metric_key": metric.id,
            "version_key": version.id,
            "run_key": instance.id,
            "snapshot_key": instance.snapshot_id,
            "result_key": instance.result_id,
            "store_payload": {
                "attempt_id": str(instance.attempt_id) if instance.attempt_id else None,
                "source_manifest": instance.source_manifest,
                "kind": instance.kind,
                "status": instance.status,
                "write_protocol": (instance.source_manifest or {}).get(
                    "write_protocol"
                ),
                "sql_source_window": {
                    "start": instance.window_start.isoformat(),
                    "end": instance.window_end.isoformat(),
                },
            },
        }
    elif sender is DerivedMetricVersion:
        metric = DerivedMetric._base_manager.using(using).get(id=instance.metric_id)
        scope = "version"
        payload = {
            "metric_key": metric.id,
            "version_key": instance.id,
            "store_payload": {"version": instance.version},
        }
    elif sender is DerivedMetric:
        metric = instance
        scope = "metric"
        payload = {"metric_key": instance.id}
    elif sender._meta.label_lower == "tracer.project":
        metric = instance
        scope = "project"
        payload = {}
    else:
        # Project cascades supply the per-project locks and durable jobs; this
        # extra org intent also covers an organization with no remaining project.
        _intent(
            organization_id=instance.id,
            scope="organization",
            using=using,
            deletion_key=f"organization:{instance.id}",
        )
        return
    project_id = metric.id if scope == "project" else metric.project_id
    with serialized_project(project_id, using=using):
        request = _intent(
            organization_id=metric.organization_id,
            project_id=project_id,
            scope=scope,
            deletion_key=f"{scope}:{instance.id}",
            using=using,
            **payload,
        )
        if scope == "run":
            from tracer.sql_workbench.metric_retention import invalidate_snapshot

            invalidate_snapshot(request)
        # A single run deletion need not revoke unrelated readers. A version or
        # metric deletion does; project deletion permanently fences all heads.
        if scope == "project":
            revoke_generations(project_id, reason="erased", using=using)
        elif scope in ("version", "metric"):
            ids = (
                [instance.id]
                if scope == "version"
                else list(
                    DerivedMetricVersion.objects.using(using)
                    .filter(
                        metric_id=metric.id,
                    )
                    .values_list("id", flat=True)
                )
            )
            revoke_generations(
                project_id, reason="erased", definition_version_ids=ids, using=using
            )


def _collector_project_ids(collector):
    """Projects whose metric dependencies this collector is going to remove."""
    Project = apps.get_model("tracer", "Project")
    project_ids = {row.pk for row in collector.data.get(Project, ())}
    for Model, lookup in (
        (DerivedMetric, "project_id"),
        (DerivedMetricVersion, "metric__project_id"),
        (DerivedMetricRun, "definition__metric__project_id"),
    ):
        ids = [row.pk for row in collector.data.get(Model, ())]
        if ids:
            project_ids.update(
                Model._base_manager.using(collector.using)
                .filter(pk__in=ids)
                .values_list(lookup, flat=True)
            )
    return project_ids


def _register_collector_protocol():
    """Close Django's collect-then-delete gap for metric-bearing cascades.

    Django has no public before-collection signal. pre_delete alone cannot add a
    newly committed pin/run/refresh dependency to a frozen cascade graph. Record
    top-level collection roots, then rebuild that graph under the same locks as
    all metric writers before dispatching any deletion signal. This narrowly
    scoped Collector adapter covers QuerySet, Model and direct Collector calls;
    unrelated collectors and historical migration models use Django unchanged.
    Root identities and keep_parents/collection options are preserved, rather
    than re-running an arbitrary original filter that could select other roots.
    """
    if getattr(Collector.delete, "_metric_lifecycle", False):
        return
    original_collect, original_delete = Collector.collect, Collector.delete

    @wraps(original_collect)
    def remember_roots(collector, objs, *args, **kwargs):
        depth = getattr(collector, "_metric_collect_depth", 0)
        # Preserve lazy QuerySets for Django's normal fast-delete decisions.
        # Materialize other iterables once so generators can also be replayed.
        if depth == 0 and not hasattr(objs, "model"):
            objs = list(objs)
        collector._metric_collect_depth = depth + 1
        try:
            result = original_collect(collector, objs, *args, **kwargs)
        finally:
            collector._metric_collect_depth = depth
        if depth == 0:
            if not hasattr(collector, "_metric_delete_roots"):
                collector._metric_delete_roots = []
            collector._metric_delete_roots.append((objs, args, kwargs))
        return result

    @wraps(original_delete)
    def delete_with_current_graph(collector):
        relevant = (
            DerivedMetric,
            DerivedMetricVersion,
            DerivedMetricRun,
            apps.get_model("tracer", "Project"),
            apps.get_model("accounts", "Organization"),
        )
        if not any(collector.data.get(Model) for Model in relevant):
            return original_delete(collector)
        roots = getattr(collector, "_metric_delete_roots", None)
        if not roots:
            raise MetricStateConflict("metric deletion requires collected roots")
        with transaction.atomic(using=collector.using):
            locked = set()
            pending = _collector_project_ids(collector)
            while True:
                for project_id in sorted(pending, key=str):
                    project_lock(project_id, using=collector.using)
                locked.update(pending)
                current = Collector(using=collector.using, origin=collector.origin)
                for objs, args, kwargs in roots:
                    # The first collection evaluated these root instances. Keep
                    # that identity set, but reload fields/dependencies now.
                    rows = list(objs)
                    if not rows:
                        continue
                    Model = rows[0].__class__
                    current.collect(
                        Model._base_manager.using(collector.using).filter(
                            pk__in=[row.pk for row in rows]
                        ),
                        *args,
                        **kwargs,
                    )
                pending = _collector_project_ids(current) - locked
                if not pending:
                    break
                # A parent cascade may discover an additional project. Acquire
                # its lock and rebuild again before freezing the final graph.
            result = original_delete(current)
            # Match Model.delete's in-memory PK clearing for original instances,
            # not only for the fresh copies used by the second collector.
            for Model, instances in collector.data.items():
                for instance in instances:
                    setattr(instance, Model._meta.pk.attname, None)
            return result

    delete_with_current_graph._metric_lifecycle = True
    Collector.collect = remember_roots
    Collector.delete = delete_with_current_graph


def register_deletion_hooks():
    """Called once from AppConfig.ready; repeated ready calls are idempotent."""
    _register_collector_protocol()
    for label in (
        "tracer.DerivedMetricRun",
        "tracer.DerivedMetricVersion",
        "tracer.DerivedMetric",
        "tracer.Project",
        "accounts.Organization",
    ):
        pre_delete.connect(
            _before_delete,
            sender=apps.get_model(label),
            weak=False,
            dispatch_uid=f"metric-lifecycle:{label}",
        )
