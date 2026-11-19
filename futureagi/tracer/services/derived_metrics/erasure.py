"""Bounded, resumable erasure of frozen and live metrics using the B02R outbox.

Live retention uses the writer cleanup protocol; frozen-copy expiry uses this
outbox. Ordinary conversation deletion is not an explicit metric erasure.
Private subjects never appear in exceptions, logs or receipts. A receipt means
physical deletion, including historical result versions, not just PG deletion.
Once a request is DONE its subjects are scrubbed to a count and a keyed digest
(MetricErasureRequest.finish); sweep() backfills requests finished earlier.
"""

import hashlib
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import timedelta
from uuid import uuid4, uuid5

from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricRun,
    MetricErasureRequest,
    MetricStateConflict,
)
from tracer.services.derived_metrics.deletion_protocol import (
    _intent,
    serialized_project,
)
from tracer.services.derived_metrics.snapshot_sketches import (
    BUCKET_TABLE,
    UNIT_TABLE,
    is_sketch,
    rebuild_buckets,
)
from tracer.services.derived_metrics.snapshots import (
    ATTEMPT_CLEANUP_PROTOCOL,
    REDACTION_PROTOCOL,
    SUBMISSION_PROTOCOL,
    _capture_cleanup,
    commit_results,
    delete_rows,
    drain_submissions,
    fenced_delete_rows,
    purge_run,
    redaction_owned,
    snapshot_execution_gate,
    snapshot_store,
)
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)

WRITER_PROTOCOLS = (
    "metric_submission",
    "metric_checkpoint_layout",
    "metric_expiry",
    "metric_policy_replan",
    "metric_omission",
    SUBMISSION_PROTOCOL,
)


class ErasureRecoveryRequired(MetricStateConflict):
    """A sanitized operator action, not permission to claim physical deletion."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ErasureProgressPending(MetricStateConflict):
    """A bounded pass completed children; more work is due, not a failed retry."""


# Failed requests remain outstanding privacy fences. Stop automatic retries,
# not the obligation: an operator can retry process_erasure after remediation.
MAX_AUTOMATIC_ATTEMPTS = 5
TERMINAL_ERRORS = (
    "source_rebuild_required",
    "ambiguous_organization_scope",
    "legacy_writer_drain_required",  # Preserve refusal of existing diagnostic rows.
    "legacy_writer_precondition_failed",
)


def pending_requests():
    return (
        MetricErasureRequest.objects.filter(
            Q(status__in=["queued", "failed"])
            | Q(status="running", claim_expires_at__lte=timezone.now()),
        )
        .filter(
            Q(store_payload__protocol__isnull=True)
            | ~Q(store_payload__protocol__in=WRITER_PROTOCOLS)
        )
        .exclude(status="failed", error_code__in=TERMINAL_ERRORS)
        .filter(
            ~Q(status="failed")
            | Q(store_payload__cleanup_failures__isnull=True)
            | Q(store_payload__cleanup_failures__lt=MAX_AUTOMATIC_ATTEMPTS)
        )
    )


def capture_blocked(metric):
    return (
        MetricErasureRequest.objects.filter(organization_key=metric.organization_id)
        .exclude(status="done")
        .filter(
            Q(scope_kind="organization")
            | Q(
                project_key=metric.project_id,
                scope_kind__in=["project", "users", "sessions"],
            )
            | Q(metric_key=metric.id, scope_kind__in=["metric", "version"])
        )
        .exists()
    )


def erase_metric_data(
    *,
    organization_id,
    scope_kind,
    project_id=None,
    subject_ids=(),
    requested_by_id=None,
):
    """Operator entry point. Subject erasures are explicitly project scoped.

    The caller must first erase the upstream source; this worker is a derived-store
    cleanup, not an authority to delete telemetry or install a permanent denylist.
    Organization-deletion pipeline wiring is outside this release.
    """
    if scope_kind not in {"organization", "project", "users", "sessions"}:
        raise ValueError("unsupported erasure scope")
    if scope_kind != "organization" and project_id is None:
        raise ValueError("project scope is required")
    if project_id:
        from tracer.models.project import Project

        if not Project._base_manager.filter(
            pk=project_id, organization_id=organization_id
        ).exists():
            raise ValueError("project is not in the organization")
    request = MetricErasureRequest.record(
        organization_id=organization_id,
        project_id=project_id,
        scope_kind=scope_kind,
        subject_ids=subject_ids,
        requested_by_id=requested_by_id,
    )
    from tracer.sql_workbench.metric_retention import invalidate_scope

    try:
        # A database error must not poison a caller-owned transaction and roll
        # back its outbox while the command reports that erasure was queued.
        with transaction.atomic():
            invalidate_scope(organization_id, project_id)
    except Exception as exc:
        # Preserve the outbox. Readers deny its namespace once committed; the
        # worker retries payload removal before it can record completion.
        private_logger.exception("metric_sql_cleanup_pending", exc)
    return request


def _runs(request):
    runs = DerivedMetricRun.objects.filter(
        definition__metric__organization_id=request.organization_key
    )
    if request.project_key:
        runs = runs.filter(definition__metric__project_id=request.project_key)
    if request.metric_key:
        runs = runs.filter(definition__metric_id=request.metric_key)
    if request.version_key:
        runs = runs.filter(definition_id=request.version_key)
    if request.run_key:
        runs = runs.filter(pk=request.run_key)
    return runs.select_related("definition__metric")


def _frozen_subjects(store, request):
    """Expand the retained 019 identity bridge, not the current survivor alone.

    Those maps have no TTL and retain every historical old ID (including owners
    that stopped being the survivor on consolidation). Traverse both directions
    to cover chained aliases. Missing/unreadable lineage or a bound overflow is
    an error, never a zero-match completion. No private IDs enter query logs.
    """
    from tracer.services.derived_metrics.queries import QUERY_SETTINGS

    table = (
        "end_user_id_remap"
        if request.scope_kind == "users"
        else "trace_session_id_remap"
    )
    subjects = set(request.subject_ids)
    for _ in range(32):
        if len(subjects) > 10000:
            break
        rows = store.client.execute(
            "SELECT groupArray((toString(old_id), toString(new_id))) FROM ("
            f"SELECT old_id, new_id FROM {table} FINAL "
            "WHERE toString(old_id) IN %(subjects)s OR toString(new_id) IN %(subjects)s "
            "LIMIT 10001)",
            {"subjects": tuple(sorted(subjects))},
            settings={**QUERY_SETTINGS, "log_queries": 0},
        )[0][0]
        if len(rows) > 10000:
            break
        expanded = subjects | {value for row in rows for value in row}
        if expanded == subjects:
            return sorted(subjects)
        subjects = expanded
    raise MetricStateConflict("snapshot identity lineage is not yet proven")


def _subject_predicate(kind):
    if kind in {"organization", "project"}:
        return "1"
    if kind == "users":
        # 028 has only a single owner slot. A blank slot cannot prove that an
        # anonymous/multi-user conversation excludes the erased user's inputs.
        # Remove unknown-owner contributions conservatively, never retain them
        # merely because newer live membership can no longer identify them.
        return "(user_id IN %(subjects)s OR user_id = '')"
    return "(session_id IN %(subjects)s OR unit_id IN %(subjects)s)"


def _redact(store, request, token):
    """One child owns a fixed baseline count so interruption never double counts."""
    with snapshot_execution_gate(request.run_key) as held:
        if not held:
            raise MetricStateConflict("snapshot execution is still in progress")
        return _redact_owned(store, request, token)


def _redact_owned(store, request, token):
    run = _runs(request).first()
    if run is None or run.deleted_at:
        purge_run(
            store,
            organization_id=request.organization_key,
            project_id=request.project_key,
            run_id=request.run_key,
            snapshot_id=request.snapshot_key,
        )
        return
    with serialized_project(request.project_key):
        run.refresh_from_db()
        # Fail closed unless this is still the namespace the child counted:
        # the frozen inputs we delete from and the results we rebuild must be
        # the same snapshot. Never delete from one namespace and commit an
        # empty rebuild of another. A saved snapshot must also still be the
        # COMPLETED capture (or this redaction's own reopen of it, see
        # snapshots.redaction_owned), never a live capture attempt. Legacy raw
        # runs keep their stable namespace across recompute and are redacted
        # in any state, as before.
        if (
            run.deleted_at
            or run.snapshot_id != request.snapshot_key
            or (
                run.kind == "snapshot"
                and run.status != "complete"
                and not redaction_owned(run)
            )
        ):
            raise MetricStateConflict("snapshot redaction namespace changed")
        DerivedMetricRun.objects.filter(pk=run.id).update(
            status="running",
            result_id=None,
            attempt_id=uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=15),
            # The durable reopen marker (snapshots.redaction_owned) must hold
            # even for a historical completed row that lacks completed_at.
            # commit_results stamps it again when the redaction completes.
            completed_at=run.completed_at or timezone.now(),
        )
        run.refresh_from_db()
    # Fence the old run attempt BEFORE observing drainage. Otherwise an old
    # sender could register between the observation and our attempt replacement.
    drain_submissions(
        store,
        organization_id=request.organization_key,
        project_id=request.project_key,
        run_id=run.id,
    )
    # The independent organization mutex serializes redactors/cleanup, while
    # _write revalidates this run attempt before every durable native dispatch.
    # Deletes, probes and sketch rebuilds must not monopolize the project
    # lifecycle key.
    params = {
        "org": str(request.organization_key),
        "project": str(request.project_key),
        "snapshot": str(request.snapshot_key),
        "run": str(run.id),
        "subjects": tuple(request.store_payload["subjects"]),
    }
    base = "org_id = toUUID(%(org)s) AND project_id = toUUID(%(project)s)"
    # Delete ALL result versions first. SQL workbench can bypass commit pointers.
    # A successor redaction reuses this run's result and sketch namespaces, so
    # these DELETEs are dispatched submissions of OUR attempt: losing the gate
    # connection while paused cannot let them reach a successor's rows.
    for table in ("derived_metric_results", "derived_metric_commits"):
        fenced_delete_rows(
            store, run, table, base + " AND run_id = toUUID(%(run)s)", params
        )
    # Opaque sketches cannot be subtracted. Remove every old state before
    # deleting typed identities, including retries after partial rebuilds.
    fenced_delete_rows(
        store,
        run,
        BUCKET_TABLE,
        base + " AND snapshot_id = toUUID(%(snapshot)s)",
        params,
    )
    # Typed identities are not rebuilt by a successor: re-deleting erased
    # subjects there is idempotent, so these private DELETEs stay unlogged.
    predicate = _subject_predicate(request.store_payload["subject_kind"])
    for table in (UNIT_TABLE, "derived_metric_inputs"):
        delete_rows(
            store,
            table,
            base + " AND snapshot_id = toUUID(%(snapshot)s) AND " + predicate,
            params,
        )
    if is_sketch(run.definition):
        # Every rebuilt INSERT goes through _write: its own short dispatch CAS
        # revalidates this redaction attempt; no lifecycle lock spans native I/O.
        rebuild_buckets(store, run)
    commit_results(store, run)
    # No per-run receipt is inferred from result values. Parent hard deletion can
    # race this phase; in that case the separately retained run intent owns purge.
    with serialized_project(request.project_key):
        if (
            run.kind == "snapshot"
            and DerivedMetricRun.objects.filter(
                pk=run.id, deleted_at__isnull=True, status="complete"
            ).exists()
        ):
            DerivedMetricRun.mark_snapshot_changed(
                run.id,
                reason="data_erasure",
                removed_units=request.store_payload["removed_units"],
            )
        for name in MetricErasureRequest.Store.values:
            MetricErasureRequest.record_receipt(
                request.id, token=token, store=name, counts={"remaining": 0}
            )
        MetricErasureRequest.finish(request.id, token=token)


_REOPENED = object()


def _settle_run(request, stale):
    """Route one run from its CURRENT state, read under its lifecycle lock.

    The parent's run cursor is a snapshot taken when it opened (a server-side
    cursor on PostgreSQL), so its rows can be stale for the whole loop. A
    capture whose commits INSERT passed its dispatch CAS before this request
    existed can still complete after the cursor read it as running: its final
    transition does not recheck capture_blocked. Failing and purging from the
    cursor's view would wipe that COMPLETED snapshot's inputs and results
    while it stays complete, with no data_erasure mark. Capture claims and
    completion serialize on the lifecycle lock and legacy raw-run transitions
    on the row lock, so under both this read is the run's current state.
    (Legacy raw runs keep their existing failed-run path; _purge_snapshots
    refuses to start while any of them is unfinished.)

    Returns the run when its completed namespace must be counted for a
    redaction child, _REOPENED when another request's redaction holds it open,
    and None once any purge obligation for it is durable.
    """
    with serialized_project(stale.definition.metric.project_id):
        run = (
            DerivedMetricRun.objects.select_for_update(of=("self",))
            .select_related("definition__metric")
            .filter(pk=stale.id)
            .first()
        )
        if run is None:
            # Hard-deleted: the run intent retained on deletion owns its purge.
            return None
        if not run.deleted_at:
            if redaction_owned(run):
                # ANOTHER request's redaction child reopened this completed
                # capture (and may have failed or lost its gate since). Its
                # frozen inputs are not an incomplete capture: never fail or
                # purge them. Drive that child, then count our own subjects on
                # a later pass, once the namespace is complete again.
                return _REOPENED
            if run.status == "complete":
                return run
        if run.status in {"queued", "running"}:
            # Revoked captures fail explicitly, rather than capture an
            # incomplete surviving population after erasure. A capture's next
            # dispatch CAS and its final transition both reject a failed run,
            # so a capture failed here can never complete afterwards.
            DerivedMetricRun.objects.filter(pk=run.id).update(
                status="failed", error="expired"
            )
        if run.kind == "snapshot" and run.attempt_id and not run.deleted_at:
            # Only THIS attempt's namespaces: the same obligation its own
            # worker records on failure (one deletion key, so they coincide).
            # Its drain is attempt-qualified; it never touches another
            # attempt's inputs, results or senders.
            _capture_cleanup(run)
        else:
            # Deleted runs, never-claimed captures and legacy raw runs.
            _intent(
                organization_id=request.organization_key,
                project_id=run.definition.metric.project_id,
                scope="run",
                run_key=run.id,
                snapshot_key=run.snapshot_id,
                metric_key=run.definition.metric_id,
                deletion_key=f"failed-capture:{run.id}",
            )
    return None


def _redact_subjects(store, request):
    reopened = []
    for stale in _runs(request).iterator(chunk_size=100):
        key = f"redact:{request.id}:{stale.id}"
        child = MetricErasureRequest.objects.filter(deletion_key=key).first()
        if child is not None and not stale.deleted_at:
            if request.scope_kind not in {"users", "sessions"}:
                continue
            if child.status != "done":
                # Finish the original child before counting any additional rows.
                if set(child.store_payload.get("subjects", [])) != set(request.subject_ids):
                    reopened.append(stale.id)
                continue
            scrub = child.store_payload.get("subject_scrub") or {}
            subjects = scrub.get("subjects") or {}
            if scrub.get("salt") and subjects.get("sha256") == MetricErasureRequest._subject_digest(
                request.subject_ids, scrub["salt"]
            ):
                continue
            # The original child only certifies its old membership. Recount
            # the remaining physical inputs, under a retry-stable child key.
            digest = MetricErasureRequest._subject_digest(request.subject_ids, str(request.id))
            key = f"redact:{request.id}:{uuid5(stale.id, digest)}"
            if MetricErasureRequest.objects.filter(deletion_key=key).exists():
                continue
        # Every fail/purge/count decision comes from the locked current row,
        # never from the cursor's possibly stale view of it.
        run = _settle_run(request, stale)
        if run is _REOPENED:
            reopened.append(stale.id)
            continue
        if run is None:
            continue
        # Counted outside the lifecycle lock: no native I/O holds it. A
        # completed namespace changes only by redaction (which keeps its
        # snapshot_id) or deletion; the child revalidates both under the lock.
        child = MetricErasureRequest.objects.filter(deletion_key=key).first()
        if child is None:
            params = {
                "org": str(request.organization_key),
                "project": str(run.definition.metric.project_id),
                "snapshot": str(run.snapshot_id),
                "subjects": tuple(request.subject_ids),
            }
            count = store.execute(
                "SELECT count() FROM derived_metric_inputs WHERE org_id = toUUID(%(org)s) "
                "AND project_id = toUUID(%(project)s) AND snapshot_id = toUUID(%(snapshot)s) AND "
                + _subject_predicate(request.scope_kind),
                params,
            )[0][0]
            if count:
                _intent(
                    organization_id=request.organization_key,
                    project_id=run.definition.metric.project_id,
                    scope="run",
                    run_key=run.id,
                    snapshot_key=run.snapshot_id,
                    metric_key=run.definition.metric_id,
                    deletion_key=key,
                    store_payload={
                        "protocol": "metric_snapshot_redaction",
                        "parent": str(request.id),
                        "subject_kind": request.scope_kind,
                        "subjects": request.subject_ids,
                        "removed_units": count,
                    },
                )
    children = MetricErasureRequest.objects.filter(
        store_payload__parent=str(request.id)
    )
    # At most ten snapshots per worker pass. Large erasures resume from children.
    progressed = False
    for child in children.exclude(status="done").order_by("requested_at")[:10]:
        progressed = process_erasure(child.id, store=store) or progressed
    if reopened:
        # Other requests' children that hold our runs open (a run with one of
        # OUR children never lands here): at most ten per pass in total, like
        # our own, never ten per reopened run.
        foreign = (
            MetricErasureRequest.objects.filter(
                run_key__in=reopened, store_payload__protocol=REDACTION_PROTOCOL
            )
            .exclude(status="done")
            .order_by("requested_at")[:10]
        )
        for other in foreign:
            progressed = process_erasure(other.id, store=store) or progressed
    if reopened or children.exclude(status="done").exists():
        if progressed:
            raise ErasureProgressPending("snapshot redaction is progressing")
        raise MetricStateConflict("snapshot redaction is pending")
    # Parent-independent obligations survive soft/hard deletion and EVERY capture
    # failure, not just expiration. A parent receipt must wait for their physical
    # purge receipts too. Redactions above are not whole-namespace purge jobs.
    cleanup = (
        MetricErasureRequest.objects.filter(
            organization_key=request.organization_key, scope_kind="run"
        )
        .exclude(status="done")
        .filter(
            Q(store_payload__protocol__isnull=True)
            | ~Q(store_payload__protocol="metric_snapshot_redaction")
        )
    )
    for field in ("project_key", "metric_key", "version_key", "run_key"):
        value = getattr(request, field)
        if value:
            cleanup = cleanup.filter(**{field: value})
    for job in cleanup.order_by("requested_at")[:10]:
        process_erasure(job.id, store=store)
    if cleanup.exists():
        raise MetricStateConflict("snapshot namespace cleanup is pending")


def _purge_snapshots(store, request):
    if request.scope_kind == "history":
        return
    # Enrolled legacy writers use the snapshot submission barrier. Deletion
    # fences every future dispatch; purge_run drains each registered native IO.
    # Production admission enrolls every raw run before enqueue. An unenrolled
    # fixture or inconsistent deployment violates that precondition; refuse it,
    # never invent an upgrade/drain receipt for a writer this feature never had.
    legacy = MetricErasureRequest.objects.filter(
        organization_key=request.organization_key,
        scope_kind="run",
        store_payload__kind="run",
    ).filter(
        Q(store_payload__status__isnull=True) | ~Q(store_payload__status="complete")
    )
    for field in ("project_key", "metric_key", "version_key", "run_key"):
        value = getattr(request, field)
        if value:
            legacy = legacy.filter(**{field: value})
    legacy = legacy.filter(
        Q(store_payload__write_protocol__isnull=True)
        | ~Q(store_payload__write_protocol=SUBMISSION_PROTOCOL)
    )
    runs = (
        _runs(request)
        .filter(kind="run")
        .exclude(status="complete")
        .filter(
            Q(source_manifest__write_protocol__isnull=True)
            | ~Q(source_manifest__write_protocol=SUBMISSION_PROTOCOL)
        )
    )
    if legacy.exists() or runs.exists():
        raise ErasureRecoveryRequired("legacy_writer_precondition_failed")
    if request.scope_kind in {"users", "sessions"} or (
        request.scope_kind in {"organization", "project"}
        and request.deletion_key is None
    ):
        return _redact_subjects(store, request)
    if request.scope_kind == "run":
        purge_run(
            store,
            organization_id=request.organization_key,
            project_id=request.project_key,
            run_id=request.run_key,
            snapshot_id=request.snapshot_key,
            result_id=request.result_key
            if request.store_payload.get("protocol") == ATTEMPT_CLEANUP_PROTOCOL
            else None,
        )
        if request.store_payload.get("protocol") != ATTEMPT_CLEANUP_PROTOCOL:
            DerivedMetricRun.objects.filter(
                pk=request.run_key, deleted_at__isnull=False
            ).update(cleanup_status="done")
        return
    # Each historical run's immutable snapshot key survives collector cascades.
    jobs = MetricErasureRequest.objects.filter(
        organization_key=request.organization_key, scope_kind="run"
    )
    if request.project_key:
        jobs = jobs.filter(project_key=request.project_key)
    if request.metric_key:
        jobs = jobs.filter(metric_key=request.metric_key)
    if request.version_key:
        jobs = jobs.filter(version_key=request.version_key)
    identities = {(j.run_key, j.snapshot_key) for j in jobs}
    identities.update(_runs(request).values_list("id", "snapshot_id"))
    for run_id, snapshot_id in identities:
        # Project is fixed for metric/version jobs. Organization/project deletion
        # can use the stronger all-rows predicate below, including orphan captures.
        if request.project_key:
            purge_run(
                store,
                organization_id=request.organization_key,
                project_id=request.project_key,
                run_id=run_id,
                snapshot_id=snapshot_id,
            )
    if request.scope_kind in {"organization", "project"}:
        drain_submissions(
            store,
            organization_id=request.organization_key,
            project_id=request.project_key,
        )
        params = {
            "org": str(request.organization_key),
            "project": str(request.project_key),
        }
        predicate = "org_id = toUUID(%(org)s)"
        if request.project_key:
            predicate += " AND project_id = toUUID(%(project)s)"
        for table in (
            "derived_metric_results",
            "derived_metric_commits",
            "derived_metric_inputs",
            BUCKET_TABLE,
            UNIT_TABLE,
        ):
            delete_rows(store, table, predicate, params)


_held_erasure_mutexes = ContextVar("metric_erasure_mutexes", default=frozenset())


@contextmanager
def _erasure_mutex(organization_id):
    # A separate transaction owns only this erasure namespace, never lifecycle
    # locks. ORM intent/phase receipts still commit independently. Transaction
    # pooling cannot strand a session lock on a returned backend.
    held = _held_erasure_mutexes.get()
    if organization_id in held:
        yield True  # a parent's synchronous, bounded child cleanup
        return
    key = int.from_bytes(
        hashlib.sha256(f"metric-erasure:{organization_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    database = connections["default"].copy(alias="metric_erasure_lock")
    token = None
    try:
        database.set_autocommit(False)
        with database.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
            acquired = cursor.fetchone()[0]
        if acquired:
            token = _held_erasure_mutexes.set(held | {organization_id})
        yield acquired
    finally:
        if token is not None:
            _held_erasure_mutexes.reset(token)
        try:
            database.rollback()
        finally:
            database.close()


def process_erasure(request_id, *, store=None, writer=None):
    organization_id = MetricErasureRequest.objects.values_list(
        "organization_key", flat=True
    ).get(pk=request_id)
    with _erasure_mutex(organization_id) as held:
        if not held:
            return False
        return _process_erasure(request_id, store=store, writer=writer)


def _process_erasure(request_id, *, store=None, writer=None):
    request = MetricErasureRequest.objects.get(pk=request_id)
    if request.store_payload.get("protocol") in WRITER_PROTOCOLS:
        return False
    token = MetricErasureRequest.claim(request.id, ttl=timedelta(minutes=15))
    if token is None:
        return request.status == "done"
    with nullcontext(store) if store else snapshot_store() as connection:
        phase = "saved_sql_cleanup"
        try:
            request.refresh_from_db()
            if request.scope_kind == "organization" and request.project_key is not None:
                # A pre-validation request is ambiguous. Never widen it or certify a partial purge.
                phase = "scope_validation"
                raise ErasureRecoveryRequired("ambiguous_organization_scope")
            from tracer.sql_workbench.metric_retention import (
                invalidate_scope,
                invalidate_snapshot,
            )

            if request.scope_kind == "run":
                invalidate_snapshot(request)
            if request.scope_kind in {
                "organization",
                "project",
                "users",
                "sessions",
                "metric",
                "version",
            } and not request.store_payload.get("protocol"):
                invalidate_scope(request.organization_key, request.project_key)
            phase = "snapshot_cleanup"
            if request.store_payload.get("protocol") == "metric_snapshot_redaction":
                _redact(connection, request, token)
                request.refresh_from_db()
                if request.status == "done":
                    return True
            else:
                if request.scope_kind in {"users", "sessions"}:
                    request.subject_ids = _frozen_subjects(connection, request)
                _purge_snapshots(connection, request)
                from tracer.services.derived_metrics.retention import purge_by_scope

                # A previous claim may have recorded the live receipt before failing.
                # Verify physical cleanup for this claim before recording DONE.
                phase = "live_checkpoint"
                if request.scope_kind in {"users", "sessions"} and writer is None:
                    from tracer.services.derived_metrics.lease import project_lease
                    from tracer.services.derived_metrics.refresh import (
                        RefreshWriter,
                    )

                    with project_lease(
                        request.project_key, holder="metric-erasure"
                    ) as lease:
                        purge_by_scope(
                            connection,
                            request,
                            writer=RefreshWriter(
                                lease, connection, org_id=request.organization_key
                            ),
                        )
                else:
                    purge_by_scope(connection, request, writer=writer)
            phase = "scope_fence"
            if request.scope_kind in {"project", "organization", "metric", "version"}:
                from tracer.models.derived_metric import MetricProjectRefresh

                states = MetricProjectRefresh.objects.filter(
                    project__organization_id=request.organization_key
                )
                if request.project_key:
                    states = states.filter(project_id=request.project_key)
                for project_id in states.values_list("project_id", flat=True):
                    with serialized_project(project_id):
                        # Fence discovery begun on a replacement lease DURING
                        # physical cleanup as well. After finish only post-purge
                        # leases can register the result of discovery.
                        MetricProjectRefresh.objects.filter(
                            project_id=project_id
                        ).update(
                            lease_token=None, lease_expires_at=None, lease_holder=""
                        )
            phase = "receipt_commit"
            for name in MetricErasureRequest.Store.values:
                MetricErasureRequest.record_receipt(
                    request.id, token=token, store=name, counts={"remaining": 0}
                )
            MetricErasureRequest.finish(request.id, token=token)
            return True
        except Exception as exc:
            # Never log exception text/SQL: it may contain erased subjects.
            # Stable action codes plus the class expose the failure, not data.
            from tracer.services.derived_metrics.refresh import (
                MetricAdmissionUnproven,
                MetricRetentionChanged,
            )

            code = (
                exc.code
                if isinstance(exc, ErasureRecoveryRequired)
                else "redaction_in_progress"
                if isinstance(exc, ErasureProgressPending)
                else "source_rebuild_required"
                if isinstance(exc, MetricAdmissionUnproven)
                else "retention_replan_required"
                if isinstance(exc, MetricRetentionChanged)
                else "native_or_state_cleanup_pending"
            )
            request.refresh_from_db()
            failures = (
                0
                if isinstance(exc, ErasureProgressPending)
                else int(request.store_payload.get("cleanup_failures", 0)) + 1
            )
            MetricErasureRequest.objects.filter(
                pk=request.id, claim_token=token
            ).update(
                store_payload={
                    **request.store_payload,
                    "cleanup_failures": failures,
                    "failure_phase": phase,
                    "failure_type": type(exc).__name__,
                    "failure_cause": code,
                }
            )
            reported_code = (
                "cleanup_retry_exhausted"
                if failures >= MAX_AUTOMATIC_ATTEMPTS and code not in TERMINAL_ERRORS
                else code
            )
            MetricErasureRequest.fail(request.id, token=token, error_code=reported_code)
            private_logger.exception(
                "metric_erasure_recovery_required",
                exc,
                erasure_request_id=str(request.id),
                error_code=code,
                failure_phase=phase,
                native_error_code=getattr(exc, "code", None)
                if type(getattr(exc, "code", None)) is int
                else None,
                automatic_retry=failures < MAX_AUTOMATIC_ATTEMPTS
                and code not in TERMINAL_ERRORS,
            )
            return False


def sweep(limit=10):
    """Bounded schedule; a disabled feature never disables privacy cleanup."""
    terminal = list(
        MetricErasureRequest.objects.filter(
            status="done", store_payload__protocol=SUBMISSION_PROTOCOL
        ).values_list("id", flat=True)[:1000]
    )
    MetricErasureRequest.objects.filter(id__in=terminal).delete()
    try:
        # Backfill: requests finished before completion scrubbed their subjects.
        # A failure here must never stop pending (authorized) erasure work.
        MetricErasureRequest.scrub_completed(limit=500)
    except Exception as exc:
        private_logger.exception("metric_erasure_scrub_pending", exc)
    ids = list(
        pending_requests()
        .order_by("updated_at", "requested_at")
        .values_list("id", flat=True)[:limit]
    )
    return sum(process_erasure(job_id) for job_id in ids)
