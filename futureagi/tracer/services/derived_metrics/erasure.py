"""Bounded, resumable erasure of frozen and live metrics using the B02R outbox.

Retention and ordinary conversation deletion deliberately do not enter here.
Private subjects never appear in exceptions, logs or receipts. A receipt means
physical deletion, including historical result versions, not just PG deletion.
"""

import hashlib
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from uuid import uuid4

from django.db import connection
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
from tracer.services.derived_metrics.snapshots import (
    SUBMISSION_PROTOCOL,
    commit_results,
    delete_rows,
    drain_submissions,
    purge_run,
    snapshot_store,
)

WRITER_PROTOCOLS = (
    "metric_submission",
    "metric_checkpoint_layout",
    "metric_expiry",
    "metric_policy_replan",
    "metric_omission",
    SUBMISSION_PROTOCOL,
)


def pending_requests():
    return MetricErasureRequest.objects.filter(
        Q(status__in=["queued", "failed"])
        | Q(status="running", claim_expires_at__lte=timezone.now()),
    ).filter(
        Q(store_payload__protocol__isnull=True)
        | ~Q(store_payload__protocol__in=WRITER_PROTOCOLS)
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
    return MetricErasureRequest.record(
        organization_id=organization_id,
        project_id=project_id,
        scope_kind=scope_kind,
        subject_ids=subject_ids,
        requested_by_id=requested_by_id,
    )


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
        drain_submissions(
            store,
            organization_id=request.organization_key,
            project_id=request.project_key,
            run_id=run.id,
        )
        DerivedMetricRun.objects.filter(pk=run.id).update(
            status="running",
            result_id=None,
            attempt_id=uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=15),
        )
        run.refresh_from_db()
        params = {
            "org": str(request.organization_key),
            "project": str(request.project_key),
            "snapshot": str(request.snapshot_key),
            "run": str(run.id),
            "subjects": tuple(request.store_payload["subjects"]),
        }
        base = "org_id = toUUID(%(org)s) AND project_id = toUUID(%(project)s)"
        # Delete ALL result versions first. SQL workbench can bypass commit pointers.
        for table in ("derived_metric_results", "derived_metric_commits"):
            delete_rows(store, table, base + " AND run_id = toUUID(%(run)s)", params)
        predicate = _subject_predicate(request.store_payload["subject_kind"])
        delete_rows(
            store,
            "derived_metric_inputs",
            base + " AND snapshot_id = toUUID(%(snapshot)s) AND " + predicate,
            params,
        )
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


def _redact_subjects(store, request):
    for run in _runs(request).filter(deleted_at__isnull=True).iterator(chunk_size=100):
        key = f"redact:{request.id}:{run.id}"
        child = MetricErasureRequest.objects.filter(deletion_key=key).first()
        if child is not None:
            continue
        if run.status != "complete":
            # Revoked queued captures fail explicitly, rather than capture an
            # incomplete surviving population after erasure.
            DerivedMetricRun.objects.filter(
                pk=run.id, status__in=["queued", "running"]
            ).update(status="failed", error="expired")
            _intent(
                organization_id=request.organization_key,
                project_id=run.definition.metric.project_id,
                scope="run",
                run_key=run.id,
                snapshot_key=run.snapshot_id,
                metric_key=run.definition.metric_id,
                deletion_key=f"failed-capture:{run.id}",
            )
            continue
        key = f"redact:{request.id}:{run.id}"
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
    for child in children.exclude(status="done").order_by("requested_at")[:10]:
        process_erasure(child.id, store=store)
    if children.exclude(status="done").exists():
        raise MetricStateConflict("snapshot redaction is pending")
    # Interrupted captures also have independently durable purge work.
    for run in _runs(request).filter(status="failed", error="expired"):
        purge_run(
            store,
            organization_id=request.organization_key,
            project_id=run.definition.metric.project_id,
            run_id=run.id,
            snapshot_id=run.snapshot_id,
        )


def _purge_snapshots(store, request):
    if request.scope_kind == "history":
        return
    # The retired raw-run writer has no native submission receipts. Never call
    # its uncertain in-flight output erased merely because its PG row vanished.
    # Completed historical runs ARE safe to collect through their retained IDs.
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
    if (
        legacy.exists()
        or _runs(request).filter(kind="run").exclude(status="complete").exists()
    ):
        raise MetricStateConflict("legacy raw-run drainage is not proven")
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
        )
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
        ):
            delete_rows(store, table, predicate, params)


@contextmanager
def _erasure_mutex(organization_id):
    # Session lock, not a transaction: native write intent and phase receipts
    # must commit independently. Reentrant for a parent's bounded child work.
    key = int.from_bytes(
        hashlib.sha256(f"metric-erasure:{organization_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        held = cursor.fetchone()[0]
    try:
        yield held
    finally:
        if held:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


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
        try:
            request.refresh_from_db()
            if request.store_payload.get("protocol") == "metric_snapshot_redaction":
                _redact(connection, request, token)
                request.refresh_from_db()
                if request.status == "done":
                    return True
            else:
                _purge_snapshots(connection, request)
                from tracer.services.derived_metrics.retention import purge_by_scope

                if "live_metric_rows" not in request.receipts:
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
                    # Unknown live submissions are not completion receipts.
                    pending = MetricErasureRequest.objects.filter(
                        organization_key=request.organization_key,
                        store_payload__protocol="metric_submission",
                    ).exclude(status="done")
                    if request.project_key:
                        pending = pending.filter(project_key=request.project_key)
                    if pending.exists():
                        raise MetricStateConflict("live write drainage is pending")
            for name in MetricErasureRequest.Store.values:
                MetricErasureRequest.record_receipt(
                    request.id, token=token, store=name, counts={"remaining": 0}
                )
            MetricErasureRequest.finish(request.id, token=token)
            return True
        except Exception:
            MetricErasureRequest.fail(
                request.id, token=token, error_code="cleanup_pending"
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
    ids = list(
        pending_requests()
        .order_by("updated_at", "requested_at")
        .values_list("id", flat=True)[:limit]
    )
    return sum(process_erasure(job_id) for job_id in ids)
