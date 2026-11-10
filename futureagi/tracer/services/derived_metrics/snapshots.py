"""Pinned captures in the 028–030/038 store; never recalculate raw telemetry.

Each native write has a parent-independent submission receipt before dispatch.
Unknown outcomes require a terminal query record or a proven sealed-socket restart.
The project lock fences dispatch against erasure, publication and parent deletion.
Completed captures have no TTL. Only explicit erasure changes their frozen inputs.
"""

from contextlib import contextmanager, nullcontext
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction
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
    project_dispatch_fence,
    serialized_project,
)
from tracer.services.derived_metrics.errors import MetricBusyError, MetricInputError
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    Scope,
    rollup_group_sql,
    rollup_insert,
)
from tracer.services.derived_metrics.retention import retention_cutoff
from tracer.services.derived_metrics.snapshot_sketches import (
    BUCKET_TABLE,
    INPUT_COLUMNS,
    UNIT_TABLE,
    assert_complete_capture,
    capture_buckets_select,
    capture_units_select,
    is_sketch,
    rollup_source,
)
from tracer.services.derived_metrics.spec import snapshot_support
from tracer.services.derived_metrics.storage import FIELD_VALUES, RETIRED_PARTS

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
        disable_reconnect=True,
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
    from tracer.services.derived_metrics.reads import (
        effective_window,
        require_calculated_window,
    )
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
        require_calculated_window(pin.definition_version, window)
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


def _submission_done(submission, proof):
    MetricErasureRequest.objects.filter(pk=submission.id).update(
        status="done", completed_at=timezone.now(), receipts={proof: {"drained": 1}}
    )


def _server_clock(store):
    # A read-only connection probe BEFORE permitting native submission. Server
    # time, not worker time, also supplies a conservative restart-recovery fence.
    server, now, uptime = store.execute(
        "SELECT toString(serverUUID()), toUnixTimestamp(now()), uptime()"
    )[0]
    return {"server": server, "time": int(now), "uptime": int(uptime)}


@contextmanager
def _no_reconnect(store):
    # The probe and write MUST use the same established native connection. A
    # server restart is a fence only if a paused writer cannot reconnect after it.
    native = store.client.connection
    previous = native.disable_reconnect
    native.disable_reconnect = True
    try:
        yield
    finally:
        native.disable_reconnect = previous


def _write(store, run, sql, params, *, capturing=False, private=False):
    """prepared -> dispatching -> drained, each phase durable before the next.

    The session fence uses the lifecycle lock's key. Drains can retire prepared
    intents (including a process killed immediately after insertion), and a late
    worker MUST re-read that retirement before dispatch. Once dispatching commits,
    neither a client exception nor an empty process list proves no write happened.
    """
    if connection.in_atomic_block:
        raise RuntimeError("snapshot dispatch requires independent committed phases")
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
            "phase": "prepared",
            "transport": "native_no_reconnect",
            "query_id": "metric-snapshot-" + uuid4().hex,
        },
    )
    with project_dispatch_fence(scope.project), _no_reconnect(store):
        submission.refresh_from_db()
        if submission.status == "done":
            raise MetricStateConflict("snapshot submission was fenced")
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
            clock = _server_clock(store)
        except Exception:
            _submission_done(submission, "not_submitted")
            raise
        submission.store_payload.update(phase="dispatching", server_clock=clock)
        MetricErasureRequest.objects.filter(pk=submission.id).update(
            store_payload=submission.store_payload
        )
        # Private key ranges disable query logging and use restart recovery for
        # ambiguous IO. Keep the marker even for failures before native submission.
        store.client.execute(
            sql,
            params,
            query_id=submission.store_payload["query_id"],
            settings={
                **QUERY_SETTINGS,
                "max_threads": 1,
                "enable_analyzer": 0,
                "log_queries": 0 if private else 1,
            },
        )
        _submission_done(submission, "synchronous_response")


def arm_submission_recovery(submission_id, *, store):
    """Re-arm restart observation for a sealed sender with a very short old uptime.

    Operator procedure: arm, restart the selected native server, retry cleanup
    before its uptime reaches this observation. Arming NEVER asserts completion.
    Old unsealed senders cannot use this path: they could reconnect after restart.
    """
    if connection.in_atomic_block:
        raise RuntimeError("recovery observation requires an independent commit")
    job = MetricErasureRequest.objects.get(
        pk=submission_id, store_payload__protocol=SUBMISSION_PROTOCOL
    )
    with project_dispatch_fence(job.project_key):
        job.refresh_from_db()
        if job.status == "done":
            return
        before = job.store_payload.get("server_clock")
        if not before or job.store_payload.get("transport") != "native_no_reconnect":
            raise MetricStateConflict("legacy snapshot sender drainage is not proven")
        after = _server_clock(store)
        if after["server"] != before["server"]:
            raise MetricStateConflict("snapshot native server changed")
        job.store_payload["server_clock"] = after
        MetricErasureRequest.objects.filter(pk=job.id).update(
            store_payload=job.store_payload
        )


def drain_submissions(store, *, organization_id, project_id=None, run_id=None):
    """Recover prepared work, or require terminal IO/restart proof for ambiguity.

    Operational recovery: if a dispatching query never reached the server (or
    its query log expired), restart the configured single native server, then
    retry cleanup. The observed server boot must postdate its pre-dispatch clock.
    This closes every old socket, and the sealed no-reconnect native transport
    cannot dispatch on a new socket afterward. Observe recovery before the new
    uptime exceeds the old uptime (a conservative additional monotonic proof).
    Use arm_submission_recovery first if that observation window is too short.
    Empty system.processes alone is NEVER sufficient. Legacy unsealed submissions
    still require a terminal record: do not manufacture a restart receipt for an
    old sender that might reconnect. This protocol must precede enabling captures.
    """
    jobs = MetricErasureRequest.objects.filter(
        organization_key=organization_id, store_payload__protocol=SUBMISSION_PROTOCOL
    ).exclude(status="done")
    if project_id:
        jobs = jobs.filter(project_key=project_id)
    if run_id:
        jobs = jobs.filter(run_key=run_id)
    for job in jobs:
        with serialized_project(job.project_key):
            job.refresh_from_db()
            if job.status == "done":
                continue
            if job.store_payload.get("phase") == "prepared":
                _submission_done(job, "not_submitted")
                continue
            query_id = job.store_payload["query_id"]
            active = store.execute(
                "SELECT count() FROM system.processes WHERE query_id = %(query_id)s",
                {"query_id": query_id},
            )[0][0]
            terminal = store.execute(
                "SELECT count() FROM system.query_log WHERE query_id = %(query_id)s AND type IN ('QueryFinish', 'ExceptionBeforeStart', 'ExceptionWhileProcessing')",
                {"query_id": query_id},
            )[0][0]
            if not active and terminal:
                _submission_done(job, "terminal_query")
                continue
            before = job.store_payload.get("server_clock")
            if (
                not active
                and before
                and job.store_payload.get("transport") == "native_no_reconnect"
            ):
                after = _server_clock(store)
                # Two seconds cover integer-clock rounding; false negatives wait
                # for a later operator restart, never turn absence into proof.
                if (
                    after["server"] == before["server"]
                    and after["uptime"] < before["uptime"]
                    and after["time"] - after["uptime"] > before["time"] + 2
                ):
                    _submission_done(job, "server_restart")
                    continue
            raise MetricStateConflict("snapshot write drainage is not yet proven")


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
        for table in (BUCKET_TABLE, UNIT_TABLE, "derived_metric_inputs"):
            delete_rows(
                store,
                table,
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
        f"""INSERT INTO derived_metric_inputs ({", ".join(INPUT_COLUMNS)})
        SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s,
            unit_id, session_id, user_id, group_key, toDate(unit_start, 'UTC'), inputs,
            sample_probability, invalid_probability, toUInt64(1), sampling_scope, sampling_stratum,
            sampling_design, population_size, unit_start, bucket_start, status,
            value_bool, value_number, value_text
        FROM ({sql}) WHERE is_deleted = 0 AND status != 'open' AND workspace_id = toUUID(%(workspace_id)s)
            AND field_id = toUUID(%(field_id)s) AND field_version = %(field_version)s AND level = %(level)s
            AND unit_start >= %(start)s AND unit_start < %(end)s""",
        params,
    )


def _assert_retained(store, run, manifest, policy):
    from tracer.services.derived_metrics.reads import require_calculated_window

    require_calculated_window(run.definition, {"window_start": run.window_start})
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


ROLLUP_PAGE_ROWS = 4096


def _rollup_ranges(store, run, rollup, *, unit_range=False):
    """Keyset pages without an unbounded DISTINCT/GROUP BY to discover keys.

    LIMIT applies to sorted input rows before min/max. The closed key range then
    includes ALL contributors for its last key, even when duplicates cross the
    page boundary. Thus a group is never split or estimated from subgroup scalars.
    Each aggregation has at most 4096 distinct output groups. Sorts may spill;
    native memory/read/group-by safety limits remain unchanged.
    """
    if rollup == "overall":
        yield None
        return
    group, keep = rollup_group_sql(rollup)
    table = "derived_metric_inputs"
    if is_sketch(run.definition):
        table, group = rollup_source(rollup)
    # Conversation sessions normally equal unit_id. A window-wide equality
    # check authorizes the sparse-key pushdown, avoiding O(pages * rows) scans.
    ordered = "unit_id" if unit_range else group
    params = {**scope_for(run).parameters(), "snapshot_id": str(run.snapshot_id)}
    after = None
    while True:
        predicate = "1" if after is None else f"{ordered} > %(after)s"
        rows, first, last = store.client.execute(
            f"SELECT count(), min(k), max(k) FROM (SELECT {group} AS k "
            f"FROM {table} WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s "
            f"AND {keep} AND {predicate} ORDER BY {ordered} LIMIT {ROLLUP_PAGE_ROWS})",
            {**params, "after": after},
            settings={
                **QUERY_SETTINGS,
                "max_bytes_before_external_sort": 67108864,
                "log_queries": 0,
            },
        )[0]
        if not rows:
            return
        yield (first, last)
        after = last


def commit_results(store, run, *, capturing=False):
    result_id = uuid4()
    mixed_scope, unaligned_sessions = store.client.execute(
        f"SELECT minIf(sampling_scope, notEmpty(sampling_scope)) != "
        f"maxIf(sampling_scope, notEmpty(sampling_scope)), "
        f"countIf(session_id != unit_id) FROM derived_metric_inputs "
        f"WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s",
        {**scope_for(run).parameters(), "snapshot_id": str(run.snapshot_id)},
        settings=QUERY_SETTINGS,
    )[0]
    for rollup in ROLLUPS:
        unit_range = rollup == "session" and not unaligned_sessions
        for group_range in _rollup_ranges(store, run, rollup, unit_range=unit_range):
            sql, params = rollup_insert(
                run.definition,
                run,
                result_id,
                rollup,
                group_range=group_range,
                mixed_scope=bool(mixed_scope),
                unit_range=unit_range,
            )
            _write(
                store,
                run,
                sql,
                params,
                capturing=capturing,
                private=group_range is not None,
            )
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
                MetricUnavailableError,
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
                if is_sketch(run.definition):
                    sql, params = capture_units_select(run, start, end)
                    _write(connection, run, sql, params, capturing=True)
                    sql, params = capture_buckets_select(
                        run, run.pin.manifest, start, end
                    )
                    _write(connection, run, sql, params, capturing=True)
                start = end
            with serialized_project(scope.project):
                _assert_retained(connection, run, run.pin.manifest, policy or _policy)
            if is_sketch(run.definition):
                assert_complete_capture(connection, run)
            commit_results(connection, run, capturing=True)
        except Exception as exc:
            # No exception text: native errors can include private unit IDs/maps.
            DerivedMetricRun.objects.filter(
                pk=run.id, attempt_id=run.attempt_id
            ).update(
                status="failed",
                error=(
                    "expired"
                    if isinstance(exc, MetricStateConflict)
                    else "window_not_calculated"
                    if isinstance(exc, MetricUnavailableError)
                    and exc.code == "window_not_calculated"
                    else "capture_failed"
                ),
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
