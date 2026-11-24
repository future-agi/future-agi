"""Pinned captures in the 028–030/038 store; never recalculate raw telemetry.

Each native write has a parent-independent submission receipt before dispatch.
Unknown outcomes require a terminal query record or a proven sealed-socket restart.
The project lock fences dispatch against erasure, publication and parent deletion.
Completed captures follow source retention and explicit erasure.
"""

import hashlib
from contextlib import contextmanager, nullcontext, suppress
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import (
    InterfaceError,
    OperationalError,
    connection,
    connections,
    transaction,
)
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricProjectRefresh,
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
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)

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
    except Exception as exc:
        # The durable queued row is recovered by the bounded 15-minute sweep.
        private_logger.exception("metric_snapshot_dispatch_pending", exc)


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
        from tracer.services.derived_metrics.reads import (
            require_forward_definition_published,
        )

        if capture_blocked(metric):
            raise MetricBusyError(
                "Data erasure is in progress. Retry after cleanup completes."
            )
        # request_snapshot holds the project's serialization lock, so this is
        # the same head capture_pin would take below. Refuse a legacy forward
        # version whose head predates its calculation without creating a pin.
        head = MetricProjectRefresh.for_project(metric.project_id).head_manifest
        require_forward_definition_published(head, metric.active_version)
        if metric.active_version is not None:
            # Likewise refuse a window not wholly calculated yet: a durable
            # snapshot must never freeze a partial tail.
            require_calculated_window(metric.active_version, window, head, whole=True)
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
        require_calculated_window(
            pin.definition_version, window, pin.manifest, whole=True
        )
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
                "semantics": "Committed request-time inputs; retained only while source policy permits, or until deletion or erasure.",
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


@contextmanager
def snapshot_read(run):
    """Reject an assembled response if deletion/redaction changed its namespace."""
    from tracer.services.derived_metrics.source_retention import require_run

    require_run(run)
    project_id = run.definition.metric.project_id
    # A raw run may recompute while its LAST committed result remains readable.
    # Its active attempt is not that result's identity. Saved snapshots, however,
    # must remain closed during capture/redaction, even with an old result id.
    readable = Q(status="complete") | Q(
        kind="run",
        status__in=["queued", "running", "failed"],
        result_id__isnull=False,
        snapshot_id__isnull=False,
        completed_at__isnull=False,
    )
    with serialized_project(project_id):
        current = (
            DerivedMetricRun.objects.select_related("definition__metric")
            .filter(readable, pk=run.id, deleted_at__isnull=True)
            .first()
        )
        if current is None:
            raise MetricInputError("This snapshot is no longer available.")
        revision = {
            "kind": current.kind,
            "snapshot_id": current.snapshot_id,
            "result_id": current.result_id,
            "changed_at": current.changed_at,
        }
        if current.kind != "run":
            revision["attempt_id"] = current.attempt_id
    yield current
    with serialized_project(project_id):
        from tracer.services.derived_metrics.service import _authorized_metric

        final = (
            DerivedMetricRun.objects.select_related("definition__metric")
            .filter(readable, pk=run.id, deleted_at__isnull=True, **revision)
            .first()
        )
        if final is None or not _authorized_metric(final.definition.metric):
            raise MetricInputError("This snapshot changed while it was being read.")
    require_run(final)


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

    Drains can retire prepared intents, even while the native clock probe is
    blocked. A short transaction serializes that retirement with the committed
    dispatching decision. No lifecycle lock spans native I/O. Once dispatching
    commits, neither an exception nor an empty process list proves drainage.
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
    with _no_reconnect(store):
        try:
            # Validate before opening the native connection, then revalidate at
            # the dispatch CAS: deletion/revocation may finish during the probe.
            _current(run)
            if run.kind == "run":
                from tracer.services.derived_metrics.source_retention import require_run

                require_run(run)
            clock = _server_clock(store)
            with serialized_project(scope.project):
                submission.refresh_from_db()
                if submission.status == "done":
                    raise MetricStateConflict("snapshot submission was fenced")
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
                submission.store_payload.update(phase="dispatching", server_clock=clock)
                MetricErasureRequest.objects.filter(pk=submission.id).update(
                    store_payload=submission.store_payload
                )
        except Exception:
            with serialized_project(scope.project):
                submission.refresh_from_db()
                if (
                    submission.status != "done"
                    and submission.store_payload.get("phase") == "prepared"
                ):
                    _submission_done(submission, "not_submitted")
            raise
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
    if job.status == "done":
        return
    before = job.store_payload.get("server_clock")
    if not before or job.store_payload.get("transport") != "native_no_reconnect":
        raise MetricStateConflict("legacy snapshot sender drainage is not proven")
    after = _server_clock(store)
    with serialized_project(job.project_key):
        job.refresh_from_db()
        if job.status == "done":
            return
        if job.store_payload.get("server_clock") != before:
            raise MetricStateConflict("snapshot recovery observation changed")
        if after["server"] != before["server"]:
            raise MetricStateConflict("snapshot native server changed")
        job.store_payload["server_clock"] = after
        MetricErasureRequest.objects.filter(pk=job.id).update(
            store_payload=job.store_payload
        )


def drain_submissions(
    store, *, organization_id, project_id=None, run_id=None, snapshot_ids=None
):
    """Recover prepared work, or require terminal IO/restart proof for ambiguity.

    snapshot_ids (an iterable or a lazily evaluated queryset of snapshot keys)
    narrows a run's drain to those attempt namespaces. Retiring a prepared
    submission fences its sender for good, so cleanup of one capture attempt
    must never drain a live successor attempt of the same run: every capture
    attempt writes under its own unique snapshot_id, which _write stamps on
    each submission. Whole-run deletion, erasure and redaction drain run-wide.

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
    if snapshot_ids is not None:
        jobs = jobs.filter(snapshot_key__in=snapshot_ids)
    for job in jobs:
        with serialized_project(job.project_key):
            job.refresh_from_db()
            if job.status == "done":
                continue
            if job.store_payload.get("phase") == "prepared":
                _submission_done(job, "not_submitted")
                continue
            observed_payload = job.store_payload
        # Native observations may stall. The durable dispatching receipt, not
        # lock ownership, prevents physical erasure from certifying completion.
        query_id = observed_payload["query_id"]
        active = store.execute(
            "SELECT count() FROM system.processes WHERE query_id = %(query_id)s",
            {"query_id": query_id},
        )[0][0]
        terminal = store.execute(
            "SELECT count() FROM system.query_log WHERE query_id = %(query_id)s AND type IN ('QueryFinish', 'ExceptionBeforeStart', 'ExceptionWhileProcessing')",
            {"query_id": query_id},
        )[0][0]
        proof = "terminal_query" if not active and terminal else None
        before = observed_payload.get("server_clock")
        if (
            proof is None
            and not active
            and before
            and observed_payload.get("transport") == "native_no_reconnect"
        ):
            after = _server_clock(store)
            # Two seconds cover integer-clock rounding; false negatives wait
            # for a later operator restart, never turn absence into proof.
            if (
                after["server"] == before["server"]
                and after["uptime"] < before["uptime"]
                and after["time"] - after["uptime"] > before["time"] + 2
            ):
                proof = "server_restart"
        with serialized_project(job.project_key):
            job.refresh_from_db()
            if job.status == "done":
                continue
            if proof and job.store_payload == observed_payload:
                _submission_done(job, proof)
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


def fenced_delete_rows(store, run, table, predicate, params):
    """A DELETE that could reach a successor attempt's rows is a submission.

    The per-run execution gate is only best-effort exclusion: its connection
    can drop while this worker is paused. A namespace a successor REUSES (run
    results, a redaction's shared sketch states) is therefore deleted through
    the durable dispatch protocol of THIS run attempt: its committed dispatch
    CAS rejects an attempt replaced during the pause before any native I/O,
    and a takeover's drain must observe this DELETE's terminal record before it
    writes. The SQL carries no subject identities, so it stays query-logged.
    """
    names = {name for name in params if f"%({name})s" in predicate}
    bound = {name: params[name] for name in names}
    _write(
        store,
        run,
        f"ALTER TABLE {table} DELETE WHERE {predicate} SETTINGS mutations_sync = 2",
        bound,
    )
    if store.execute(f"SELECT count() FROM {table} WHERE {predicate}", bound) != [(0,)]:
        raise MetricStateConflict("snapshot physical deletion is incomplete")


def purge_run(
    store, *, organization_id, project_id, run_id, snapshot_id, result_id=None
):
    """Whole-run erasure, or ONLY an attempt's immutable physical namespace.

    Capture setup/failure must pass result_id. A stale worker can continue after
    losing its PostgreSQL gate, so neither its DELETE nor its cleanup outbox may
    address the shared run namespace. Explicit deletion still collects ALL runs.

    An attempt-only purge also drains ONLY that attempt's submissions. The
    takeover records the detached attempt's cleanup while its successor starts
    capturing, and the erasure sweep runs that cleanup concurrently: a run-wide
    drain would retire the successor's prepared submissions (its dispatch CAS
    then fails it for good) or block on its dispatching ones.
    """
    params = {
        "org": str(organization_id),
        "project": str(project_id),
        "run": str(run_id),
        "snapshot": str(snapshot_id),
        "result": str(result_id),
    }
    base = "org_id = toUUID(%(org)s) AND project_id = toUUID(%(project)s)"
    if result_id is not None and snapshot_id is None:
        # Never widen an attempt cleanup to the run: fail closed instead.
        raise MetricStateConflict("snapshot attempt namespace is unknown")
    drain_submissions(
        store,
        organization_id=organization_id,
        project_id=project_id,
        run_id=run_id,
        snapshot_ids=None if result_id is None else (snapshot_id,),
    )
    result_predicate = " AND run_id = toUUID(%(run)s)"
    if result_id is not None:
        result_predicate += " AND result_id = toUUID(%(result)s)"
    for table in ("derived_metric_results", "derived_metric_commits"):
        delete_rows(store, table, base + result_predicate, params)
    snapshots = {snapshot_id} if snapshot_id else set()
    if result_id is None:
        # Whole-run deletion also owns abandoned attempts. Their immutable input
        # keys survive both takeover and PG parent cascades in the outbox.
        snapshots.update(
            MetricErasureRequest.objects.filter(
                organization_key=organization_id,
                project_key=project_id,
                run_key=run_id,
                snapshot_key__isnull=False,
            ).values_list("snapshot_key", flat=True)
        )
    for key in snapshots:
        # Sketch states and typed units share the attempt's snapshot namespace.
        for table in (BUCKET_TABLE, UNIT_TABLE, "derived_metric_inputs"):
            delete_rows(
                store,
                table,
                base + " AND snapshot_id = toUUID(%(snapshot)s)",
                {**params, "snapshot": str(key)},
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

    # At capture the units store is at hand: a durable snapshot must also hold
    # no unit refresh still keeps open (read_cut), never a partial tail.
    require_calculated_window(
        run.definition,
        {"window_start": run.window_start, "window_end": run.window_end},
        manifest,
        whole=True,
        store=store,
        metric=run.definition.metric,
    )
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
    from tracer.services.derived_metrics.source_retention import require_run

    require_run(run)
    # Allocated durably BEFORE native IO. Setup/failure cleanup can therefore
    # target this attempt even if its result pointer was never published.
    result_id = run.attempt_id
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
    require_run(run)
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


@contextmanager
def snapshot_execution_gate(run_id):
    """Best-effort execution exclusion, NOT a physical deletion fence.

    The independent transaction works through transaction-mode pooling and avoids
    duplicate work while alive. Connection loss releases it without stopping the
    native worker. Attempt-qualified namespaces below provide the durable safety
    boundary even when takeover occurs before that worker notices the loss.
    """
    database = connections["default"].copy(alias="metric_snapshot_execution")
    key = int.from_bytes(
        hashlib.sha256(f"metric-snapshot-execution:{run_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    try:
        database.set_autocommit(False)
        with database.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
            yield cursor.fetchone()[0]
    finally:
        try:
            # A disconnected gate is expected to permit takeover. Closing the
            # connection also rolls back; never mask the worker's fenced result.
            with suppress(InterfaceError, OperationalError):
                database.rollback()
        finally:
            database.close()


REDACTION_PROTOCOL = "metric_snapshot_redaction"


def redaction_children(run_id):
    """Redaction children of one run that have not finished (failed included)."""
    return MetricErasureRequest.objects.filter(
        run_key=run_id, store_payload__protocol=REDACTION_PROTOCOL
    ).exclude(status="done")


def redaction_owned(run):
    """A COMPLETED run whose namespace a redaction child has reopened.

    Redaction flips a completed run back to running (a fresh attempt fences
    its earlier senders) and keeps completed_at set: it stamps one at the flip
    if a historical row lacks it, and commit_results stamps it again on
    completion. Such a run's frozen inputs and results are a live namespace,
    never an abandoned capture: no claim may detach it and no erasure parent
    may fail or purge it, even after the redaction child failed or lost its
    gate connection.

    For a saved snapshot the two fields are proof on their own: only a
    redaction reopens a completed snapshot (a capture claim never touches one,
    and a capture attempt has no completed_at). A legacy raw run is ALSO
    reopened by recompute (queued, then running, with its old completed_at
    still set), so for it only an unfinished redaction child of that run
    proves ownership. Recompute never creates one, and a failed child stays
    unfinished until its retry completes.
    """
    if run.status != "running" or run.completed_at is None:
        return False
    if run.kind == "snapshot":
        return True
    return redaction_children(run.id).exists()


def recoverable_captures(limit=10):
    """Queued captures and expired capture attempts, never redaction-owned runs."""
    return (
        DerivedMetricRun.objects.filter(
            Q(status="queued")
            | Q(status="running", lease_expires_at__lte=timezone.now()),
            kind="snapshot",
            deleted_at__isnull=True,
            completed_at__isnull=True,
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )


ATTEMPT_CLEANUP_PROTOCOL = "metric_snapshot_attempt_cleanup"


def _capture_cleanup(run):
    """The durable purge obligation of ONE capture attempt's namespaces.

    Recorded only once that attempt is detached for good: atomically with the
    takeover that replaces it, by an erasure parent that fails it under the
    lifecycle lock, or by its own worker after its failure. An attempt never
    becomes live again, so the set of namespaces these obligations name only
    grows and never includes a live attempt (see _detached_namespaces).
    """
    return _intent(
        organization_id=run.definition.metric.organization_id,
        project_id=run.definition.metric.project_id,
        scope="run",
        metric_key=run.definition.metric_id,
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        result_key=run.attempt_id,
        deletion_key=f"capture-attempt:{run.id}:{run.attempt_id}",
        store_payload={"protocol": ATTEMPT_CLEANUP_PROTOCOL},
    )


def _detached_namespaces(run_id):
    """Snapshot keys of this run's detached capture attempts, evaluated lazily.

    A capture's setup drain must wait for every earlier attempt's senders
    (an old commit could otherwise land after ours), but it must never retire
    a LIVE attempt's prepared submission. A stale worker can reach its setup
    drain after a successor took over, so "every attempt but mine" is wrong:
    the stale worker's own view of "mine" is the dead attempt. Detachment is
    durable and monotone instead. A successor's claim records its predecessor
    here in the same transaction that replaces it, so a live successor sees
    every earlier attempt, and a stale worker never sees the live one.
    """
    return MetricErasureRequest.objects.filter(
        run_key=run_id,
        store_payload__protocol=ATTEMPT_CLEANUP_PROTOCOL,
        snapshot_key__isnull=False,
    ).values("snapshot_key")


def execute_snapshot(run_id, *, store=None, policy=None):
    with snapshot_execution_gate(run_id) as held:
        if held:
            return _execute_snapshot(run_id, store=store, policy=policy)


def _execute_snapshot(run_id, *, store=None, policy=None):
    run = (
        DerivedMetricRun.objects.select_related("definition__metric", "pin__manifest")
        .filter(pk=run_id, kind="snapshot")
        .first()
    )
    if (
        run is None
        or run.deleted_at
        or run.status in {"complete", "failed"}
        or run.completed_at is not None
    ):
        return
    scope = scope_for(run)
    with serialized_project(scope.project):
        run.refresh_from_db()
        # A running capture always holds a lease; keep failing loudly if not.
        if run.status == "running" and run.lease_expires_at > timezone.now():  # type: ignore[operator]
            return
        if (
            run.status not in {"queued", "running"}
            or run.deleted_at
            # A redaction reopened this COMPLETED namespace. Even with its
            # gate gone and its lease expired, it is not an abandoned capture:
            # detaching it would purge the frozen inputs and let the redaction
            # retry commit an empty snapshot from a fresh namespace.
            or run.completed_at is not None
        ):
            return
        if run.attempt_id:
            # Persist the old namespace BEFORE detaching it, including attempts
            # whose worker died before it could record a failure obligation.
            _capture_cleanup(run)
        run.status, run.attempt_id = "running", uuid4()
        run.snapshot_id, run.result_id = uuid4(), None
        run.lease_expires_at = timezone.now() + timedelta(minutes=15)
        run.save(
            update_fields=[
                "status",
                "attempt_id",
                "snapshot_id",
                "result_id",
                "lease_expires_at",
            ]
        )
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
            # Every claim receives fresh input/result namespaces. Even if our
            # gate backend disconnects and a successor completes while paused
            # here (or in a native DELETE), cleanup can touch only OUR attempt.
            # Dispatch receipts independently fence INSERTs against erasure.
            # Drain only DETACHED attempts: if we are stale by now, a run-wide
            # drain would retire our live successor's prepared submissions.
            drain_submissions(
                connection,
                organization_id=scope.organization,
                project_id=scope.project,
                run_id=run.id,
                snapshot_ids=_detached_namespaces(run.id),
            )
            _assert_retained(
                connection,
                run,
                run.pin.manifest if run.pin else None,
                policy or _policy,
            )
            if run.pin is None:  # Already rejected by _assert_retained.
                raise MetricStateConflict("expired capture")
            connection.assert_visible(run.pin.manifest)
            purge_run(
                connection,
                organization_id=scope.organization,
                project_id=scope.project,
                run_id=run.id,
                snapshot_id=run.snapshot_id,
                result_id=run.attempt_id,
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
            _assert_retained(connection, run, run.pin.manifest, policy or _policy)
            if is_sketch(run.definition):
                # Native completeness probe, deliberately outside the lifecycle
                # lock; commit_results revalidates the attempt before publishing.
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
                    else exc.code
                    if isinstance(exc, MetricUnavailableError)
                    and exc.code
                    in {"window_not_calculated", "window_not_calculated_yet"}
                    else "capture_failed"
                ),
            )
            # Even partial writes belong to a durable attempt-only cleanup.
            _capture_cleanup(run)
            private_logger.exception("metric_snapshot_capture_failed", exc)
        finally:
            with serialized_project(scope.project):
                if (
                    run.pin_id is not None
                    and DerivedMetricRun.objects.filter(
                        pk=run.id, attempt_id=run.attempt_id
                    ).exists()
                ):
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
        if run.pin_id is not None:
            MetricGenerationPin.objects.filter(pk=run.pin_id).update(
                invalidated_at=timezone.now(), invalidation_reason="erased"
            )
        request = _intent(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope="run",
            metric_key=metric.id,
            version_key=run.definition_id,
            run_key=run.id,
            snapshot_key=run.snapshot_id,
            result_key=run.result_id,
            deletion_key=f"run:{run.id}",
            store_payload={
                "sql_source_window": {
                    "start": run.window_start.isoformat(),
                    "end": run.window_end.isoformat(),
                }
            },
        )
        from tracer.sql_workbench.metric_retention import invalidate_snapshot

        # The tombstone, outbox and saved-result CAS fence commit together.
        # A late native SQL response cannot republish after this cancellation.
        invalidate_snapshot(request)
    return {"status": "cleanup_pending"}
