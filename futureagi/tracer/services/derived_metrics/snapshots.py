"""Pinned captures in the existing 028–030 store; never recalculate raw telemetry.

Each native write has a parent-independent submission receipt before dispatch.
Unknown outcomes block cleanup until a terminal query-log record proves drainage.
The project lock fences dispatch against erasure, publication and parent deletion.
Completed captures have no TTL. Only explicit erasure changes their frozen inputs.
"""

from contextlib import contextmanager, nullcontext
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricStateConflict,
)
from tracer.services.derived_metrics.deletion_protocol import (
    _intent,
    capture_pin,
    serialized_project,
)
from tracer.services.derived_metrics.errors import MetricBusyError, MetricInputError
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    Scope,
    rollup_insert,
)
from tracer.services.derived_metrics.retention import retention_cutoff
from tracer.services.derived_metrics.spec import snapshot_support
from tracer.services.derived_metrics.storage import FIELD_VALUES, RETIRED_PARTS
from tracer.services.derived_metrics.unit_reads import field_recipe

PIN_TTL = timedelta(hours=1)
SUBMISSION_PROTOCOL = "metric_snapshot_submission"
ROLLUPS = ("overall", "session", "user", "cohort", "day")


@contextmanager
def snapshot_store():
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config
    from tracer.services.derived_metrics.publication import ClickHousePublicationStore

    config = get_v2_config()
    client = Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=10,
        send_receive_timeout=660,
        # Erasure predicates contain private subjects. Only the namespace-only
        # writes in _write explicitly opt in to the query log for drainage proof.
        settings={"log_queries": 0, "log_query_threads": 0},
    )
    try:
        yield ClickHousePublicationStore(client)
    finally:
        client.disconnect()


def _policy(organization_id):
    provider = getattr(settings, "METRICS_RETENTION_DAYS_PROVIDER", "")
    if not provider:
        from tracer.services.derived_metrics.service import MetricUnavailableError

        raise MetricUnavailableError(
            "snapshot_unavailable", "The retention policy is unavailable."
        )
    return import_string(provider)(organization_id)


def enqueue_capture(run_id):
    from tracer.tasks.derived_metric import capture_derived_metric_snapshot

    try:
        capture_derived_metric_snapshot.apply_async(
            args=[str(run_id)], dispatch_timeout_seconds=10
        )
    except Exception:
        # The durable queued row is recovered by the bounded 15-minute sweep.
        pass


def request_snapshot(metric, data, user):
    from tracer.serializers.derived_metric import DerivedMetricRunSerializer
    from tracer.services.derived_metrics.reads import effective_window
    from tracer.services.derived_metrics.service import SnapshotNotSupportedError, _hash

    window = effective_window(data["window_start"], data["window_end"])
    fingerprint = _hash(
        {"snapshot_metric": str(metric.id), "key": data["idempotency_key"]}
    )
    with serialized_project(metric.project_id):
        metric = DerivedMetric.objects.select_related("active_version").get(
            pk=metric.id, deleted=False
        )
        previous = DerivedMetricRun.objects.filter(fingerprint=fingerprint).first()
        if previous:
            if (previous.window_start, previous.window_end) != (
                window["window_start"],
                window["window_end"],
            ):
                raise MetricInputError(
                    {
                        "idempotency_key": "This key identifies a different snapshot window."
                    }
                )
            return DerivedMetricRunSerializer(previous).data
        from tracer.services.derived_metrics.erasure import capture_blocked

        if capture_blocked(metric):
            raise MetricBusyError(
                "Data erasure is in progress. Retry after cleanup completes."
            )
        supported, reason = snapshot_support(metric.active_version)
        if not supported:
            raise SnapshotNotSupportedError(reason)
        # 028 deliberately stores numeric aliases only; do not silently coerce text.
        recipe = field_recipe(metric.active_version)
        if recipe.kind in {"unique_count", "percentile"}:
            raise SnapshotNotSupportedError(
                "Snapshots of distinct counts and percentiles are not available in the numeric snapshot store."
            )
        if (
            DerivedMetricRun.objects.filter(
                definition__metric__project_id=metric.project_id,
                kind="snapshot",
                status__in=["queued", "running"],
                deleted_at__isnull=True,
                pin__expires_at__gt=timezone.now(),
            ).count()
            >= 2
        ):
            raise MetricBusyError(
                "Two snapshots are already queued or capturing in this project."
            )
        run_id = uuid4()
        try:
            pin = capture_pin(
                metric.project_id,
                metric_id=metric.id,
                consumer_kind="snapshot",
                consumer_ref=str(run_id),
                ttl=PIN_TTL,
            )
        except MetricStateConflict as exc:
            from tracer.services.derived_metrics.service import MetricUnavailableError

            raise MetricUnavailableError(
                "snapshot_unavailable",
                "No usable committed result is available to capture.",
            ) from exc
        run = DerivedMetricRun.objects.create(
            id=run_id,
            definition=pin.definition_version,
            requested_by=user,
            kind="snapshot",
            supported_grain=pin.definition_version.level,
            window_start=window["window_start"],
            window_end=window["window_end"],
            as_of=pin.captured_at,
            captured_at=pin.captured_at,
            fingerprint=fingerprint,
            snapshot_id=uuid4(),
            pin=pin,
            pinned_manifest=pin.manifest,
            pinned_generation=pin.manifest.generation,
            lease_expires_at=pin.expires_at,
            source_manifest={
                "schema_version": 1,
                "compiler_version": 1,
                "evaluator_table": FIELD_VALUES.name,
                "definition_hash": pin.definition_version.definition_hash,
                "captured_at": pin.captured_at.isoformat(),
                "sampling_scope": "",
                "semantics": "Committed request-time inputs; retained until explicit deletion or erasure.",
            },
        )
        transaction.on_commit(lambda: enqueue_capture(run.id))
        return DerivedMetricRunSerializer(run).data


def scope_for(run):
    metric = run.definition.metric
    return Scope(metric.organization_id, metric.workspace_id, metric.project_id)


def _current(run):
    current = (
        DerivedMetricRun.objects.select_related("pin")
        .filter(pk=run.id, deleted_at__isnull=True)
        .first()
    )
    if (
        current is None
        or current.attempt_id != run.attempt_id
        or current.status != "running"
    ):
        raise MetricStateConflict("expired capture")
    return current


def _write(store, run, sql, params, *, capturing=False):
    """Register outside the dispatch transaction: rollback must not lose late IO."""
    scope = scope_for(run)
    submission = _intent(
        organization_id=scope.organization,
        project_id=scope.project,
        scope="history",
        metric_key=run.definition.metric_id,
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        store_payload={
            "protocol": SUBMISSION_PROTOCOL,
            "query_id": "metric-snapshot-" + uuid4().hex,
        },
    )
    rejected = False
    with serialized_project(scope.project):
        try:
            current = _current(run)
            if capturing:
                from tracer.services.derived_metrics.erasure import capture_blocked
                from tracer.services.derived_metrics.service import (
                    _authorized_metric,
                    _requester_can_run,
                )

                if (
                    not current.pin
                    or not current.pin.is_usable()
                    or capture_blocked(current.definition.metric)
                    or not _authorized_metric(current.definition.metric)
                    or not _requester_can_run(current)
                ):
                    raise MetricStateConflict("expired capture")
        except MetricStateConflict:
            rejected = True
        if not rejected:
            # Only namespace IDs occur in these queries, never subject lists.
            # A native ID permits terminality proof after a lost acknowledgement.
            store.client.execute(
                sql,
                params,
                query_id=submission.store_payload["query_id"],
                settings={
                    **QUERY_SETTINGS,
                    "max_threads": 1,
                    "enable_analyzer": 0,
                    "log_queries": 1,
                },
            )
        MetricErasureRequest.objects.filter(pk=submission.id).update(
            status="done",
            completed_at=timezone.now(),
            receipts={
                "not_submitted" if rejected else "synchronous_response": {"drained": 1}
            },
        )
    # Reject AFTER the no-dispatch receipt commits; rollback would strand an
    # obligation for a query that can never have a terminal query-log record.
    if rejected:
        raise MetricStateConflict("expired capture")


def drain_submissions(store, *, organization_id, project_id=None, run_id=None):
    jobs = MetricErasureRequest.objects.filter(
        organization_key=organization_id, store_payload__protocol=SUBMISSION_PROTOCOL
    ).exclude(status="done")
    if project_id:
        jobs = jobs.filter(project_key=project_id)
    if run_id:
        jobs = jobs.filter(run_key=run_id)
    for job in jobs:
        query_id = job.store_payload["query_id"]
        active = store.execute(
            "SELECT count() FROM system.processes WHERE query_id = %(query_id)s",
            {"query_id": query_id},
        )[0][0]
        terminal = store.execute(
            "SELECT count() FROM system.query_log WHERE query_id = %(query_id)s AND type IN ('QueryFinish', 'ExceptionBeforeStart', 'ExceptionWhileProcessing')",
            {"query_id": query_id},
        )[0][0]
        if active or not terminal:
            raise MetricStateConflict("snapshot write drainage is not yet proven")
        MetricErasureRequest.objects.filter(pk=job.id).update(
            status="done",
            completed_at=timezone.now(),
            receipts={"terminal_query": {"drained": 1}},
        )


def delete_rows(store, table, predicate, params):
    # ALTER with sync=2 also removes rows visible to raw SQL-workbench queries.
    store.execute(
        f"ALTER TABLE {table} DELETE WHERE {predicate} SETTINGS mutations_sync = 2",
        params,
    )
    if store.execute(f"SELECT count() FROM {table} WHERE {predicate}", params) != [
        (0,)
    ]:
        raise MetricStateConflict("snapshot physical deletion is incomplete")


def purge_run(store, *, organization_id, project_id, run_id, snapshot_id):
    params = {
        "org": str(organization_id),
        "project": str(project_id),
        "run": str(run_id),
        "snapshot": str(snapshot_id),
    }
    base = "org_id = toUUID(%(org)s) AND project_id = toUUID(%(project)s)"
    drain_submissions(
        store, organization_id=organization_id, project_id=project_id, run_id=run_id
    )
    for table in ("derived_metric_results", "derived_metric_commits"):
        delete_rows(store, table, base + " AND run_id = toUUID(%(run)s)", params)
    if snapshot_id:
        delete_rows(
            store,
            "derived_metric_inputs",
            base + " AND snapshot_id = toUUID(%(snapshot)s)",
            params,
        )


def capture_select(run, manifest, start, end):
    scope = scope_for(run)
    sql, params = committed_rows_select(
        FIELD_VALUES,
        manifest,
        org_id=scope.organization,
        project_id=scope.project,
        key_in={
            "field_id": (run.definition.metric_id,),
            "field_version": (run.definition.version,),
            "level": (run.supported_grain,),
        },
    )
    params.update(
        scope.parameters(),
        snapshot_id=str(run.snapshot_id),
        field_id=str(run.definition.metric_id),
        field_version=run.definition.version,
        start=start,
        end=end,
        level=run.supported_grain,
    )
    return (
        f"""INSERT INTO derived_metric_inputs
        SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s,
            unit_id, session_id, user_id, group_key, toDate(unit_start), inputs,
            sample_probability, invalid_probability, toUInt64(1), sampling_scope, sampling_stratum,
            sampling_design, population_size
        FROM ({sql}) WHERE is_deleted = 0 AND status != 'open' AND workspace_id = toUUID(%(workspace_id)s)
            AND field_id = toUUID(%(field_id)s) AND field_version = %(field_version)s AND level = %(level)s
            AND unit_start >= %(start)s AND unit_start < %(end)s""",
        params,
    )


def _assert_retained(store, run, manifest, policy):
    if not run.pin or not run.pin.is_usable():
        raise MetricStateConflict("expired capture")
    cutoff = retention_cutoff(policy(run.definition.metric.organization_id))
    if cutoff and run.window_start < cutoff:
        raise MetricStateConflict("expired capture")
    # Anonymous retired buckets cannot be reconstructed as complete numeric rows.
    retired, params = committed_rows_select(
        RETIRED_PARTS,
        manifest,
        org_id=run.definition.metric.organization_id,
        project_id=run.definition.metric.project_id,
        key_in={
            "metric_id": (run.definition.metric_id,),
            "metric_version": (run.definition.version,),
        },
    )
    params.update(
        field_id=str(run.definition.metric_id),
        field_version=run.definition.version,
        start=run.window_start,
        end=run.window_end,
    )
    if store.execute(
        f"SELECT count() FROM ({retired}) WHERE is_deleted = 0 AND metric_id = toUUID(%(field_id)s) AND metric_version = %(field_version)s AND bucket_start >= %(start)s AND bucket_start < %(end)s",
        params,
    )[0][0]:
        raise MetricStateConflict("expired capture")


def commit_results(store, run, *, capturing=False):
    result_id = uuid4()
    for rollup in ROLLUPS:
        sql, params = rollup_insert(run.definition, run, result_id, rollup)
        _write(store, run, sql, params, capturing=capturing)
    params = {
        **scope_for(run).parameters(),
        "metric_id": str(run.definition.metric_id),
        "run_id": str(run.id),
        "result_id": str(result_id),
        "committed_at": timezone.now(),
    }
    rows = store.execute(
        f"SELECT count() FROM derived_metric_results WHERE {SCOPE_SQL} AND run_id = %(run_id)s AND result_id = %(result_id)s",
        params,
    )[0][0]
    previous = store.execute(
        f"SELECT max(version) FROM derived_metric_commits WHERE {SCOPE_SQL} AND run_id = %(run_id)s",
        params,
    )[0][0]
    params["version"] = int(previous or 0) + 1
    _write(
        store,
        run,
        "INSERT INTO derived_metric_commits VALUES (%(org_id)s, %(workspace_id)s, %(project_id)s, %(metric_id)s, %(run_id)s, %(result_id)s, %(committed_at)s, %(version)s)",
        params,
        capturing=capturing,
    )
    with serialized_project(run.definition.metric.project_id):
        current = _current(run)
        if capturing and (not current.pin or not current.pin.is_usable()):
            raise MetricStateConflict("expired capture")
        DerivedMetricRun.objects.filter(pk=run.id).update(
            status="complete",
            result_id=result_id,
            row_count=rows,
            completed_at=params["committed_at"],
            error="",
        )


def execute_snapshot(run_id, *, store=None, policy=None):
    run = (
        DerivedMetricRun.objects.select_related("definition__metric", "pin__manifest")
        .filter(pk=run_id, kind="snapshot")
        .first()
    )
    if run is None or run.deleted_at or run.status in {"complete", "failed"}:
        return
    scope = scope_for(run)
    with serialized_project(scope.project):
        run.refresh_from_db()
        if run.status == "running" and run.lease_expires_at > timezone.now():
            return
        if run.status not in {"queued", "running"} or run.deleted_at:
            return
        run.status, run.attempt_id = "running", uuid4()
        run.lease_expires_at = timezone.now() + timedelta(minutes=15)
        run.save(update_fields=["status", "attempt_id", "lease_expires_at"])
    with nullcontext(store) if store else snapshot_store() as connection:
        try:
            from tracer.services.derived_metrics.service import (
                _authorized_metric,
                _requester_can_run,
            )

            if not _authorized_metric(run.definition.metric) or not _requester_can_run(
                run
            ):
                raise MetricStateConflict("expired capture")
            with serialized_project(scope.project):
                _current(run)
                drain_submissions(
                    connection,
                    organization_id=scope.organization,
                    project_id=scope.project,
                    run_id=run.id,
                )
                _assert_retained(
                    connection,
                    run,
                    run.pin.manifest if run.pin else None,
                    policy or _policy,
                )
                connection.assert_visible(run.pin.manifest)
                purge_run(
                    connection,
                    organization_id=scope.organization,
                    project_id=scope.project,
                    run_id=run.id,
                    snapshot_id=run.snapshot_id,
                )
            start = run.window_start
            while start < run.window_end:
                end = min(start + timedelta(days=30), run.window_end)
                sql, params = capture_select(run, run.pin.manifest, start, end)
                _write(connection, run, sql, params, capturing=True)
                start = end
            with serialized_project(scope.project):
                _assert_retained(connection, run, run.pin.manifest, policy or _policy)
            commit_results(connection, run, capturing=True)
        except Exception as exc:
            # No exception text: native errors can include private unit IDs/maps.
            DerivedMetricRun.objects.filter(
                pk=run.id, attempt_id=run.attempt_id
            ).update(
                status="failed",
                error="expired"
                if isinstance(exc, MetricStateConflict)
                else "capture_failed",
            )
            # Even partial writes belong to a durable parent-independent cleanup.
            _intent(
                organization_id=scope.organization,
                project_id=scope.project,
                scope="run",
                metric_key=run.definition.metric_id,
                run_key=run.id,
                snapshot_key=run.snapshot_id,
                deletion_key=f"failed-capture:{run.id}",
            )
        finally:
            MetricGenerationPin.objects.filter(pk=run.pin_id).delete()


def delete_snapshot(metric, snapshot_id, user):
    from django.http import Http404

    with serialized_project(metric.project_id):
        run = DerivedMetricRun.objects.filter(
            pk=snapshot_id, definition__metric=metric, kind="snapshot"
        ).first()
        if run is None:
            raise Http404
        if run.deleted_at:
            return {
                "status": "deleted"
                if run.cleanup_status == "done"
                else "cleanup_pending"
            }
        DerivedMetricRun.mark_snapshot_deleted(run.id, user_id=user.id)
        MetricGenerationPin.objects.filter(pk=run.pin_id).update(
            invalidated_at=timezone.now(), invalidation_reason="erased"
        )
        _intent(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope="run",
            metric_key=metric.id,
            version_key=run.definition_id,
            run_key=run.id,
            snapshot_key=run.snapshot_id,
            result_key=run.result_id,
            deletion_key=f"run:{run.id}",
        )
    return {"status": "cleanup_pending"}
