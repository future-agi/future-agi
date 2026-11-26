# ruff: noqa: F811
"""Real 028–030 capture, 90-day slicing, pinned refresh and physical erasure."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db.models import Q
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricErasureRequest,
    MetricGenerationPin,
    MetricStateConflict,
)
from tracer.services.derived_metrics import erasure, snapshots
from tracer.services.derived_metrics.deletion_protocol import _intent
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def capture_case(live_writer, workspace, observe_project, user, request, monkeypatch):
    from ee.usage.models.usage import PlanEntitlement
    from tfc.capabilities import retention as policy_adapter
    from tfc.licensing.types import DeploymentFlavor

    # This fixture captures 90 retained days, not an exemption from source policy.
    monkeypatch.setattr(
        policy_adapter, "get_deployment_flavor", lambda: DeploymentFlavor.CLOUD
    )
    PlanEntitlement.objects.update_or_create(
        organization=workspace.organization,
        plan="free",
        feature="retention_traces_days",
        defaults={"value_int": 900},
    )
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Capture",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=[
            {"alias": "y", "source": "span", "field": "duration_ms", "reducer": "sum"}
        ],
        selection_unit="session",
        weighting="none",
        definition_hash="b" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    end = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    from tracer.services.derived_metrics.refresh import payload_select

    attempt = live_writer.begin(
        ledger={
            "dirty_after": [
                {"bucket_start": (end - timedelta(days=age)).isoformat()}
                for age in [1, 31, 61, 89]
            ]
        }
    )
    payloads = []
    owners = getattr(request, "param", {})
    for i, age in enumerate([1, 31, 61, 89]):
        sql, params = field_query(
            owners.get("session", "unit-0") if i == 0 else f"unit-{i}",
            n=i * 2,
            start=end - timedelta(days=age),
            user=owners.get("user", "erase-me") if i == 0 else "keep-me",
            metric=metric.id,
        )
        payloads.extend(live_writer.freeze(attempt, FIELD_VALUES, sql, params))
    overlay, params = payload_select(FIELD_VALUES, payloads, prefix="capture_test")
    live_writer._freeze_buckets(
        attempt, None, definition, overlay, params, complete_fields=True
    )
    live_writer.seal(
        attempt, cursors={"watermark_ns": int(timezone.now().timestamp()) * 10**9}
    )
    live_writer.stage_and_publish(attempt)
    data = {
        "window_start": end - timedelta(days=90),
        "window_end": end,
        "idempotency_key": str(uuid4()),
    }
    with patch.object(snapshots, "enqueue_capture"):
        saved = snapshots.request_snapshot(metric, data, user)
    return metric, DerivedMetricRun.objects.get(pk=saved["id"]), data


def execute(run, store, policy=lambda _: 0):
    native = store.client.execute
    errors = []

    def query(*args, **kwargs):
        try:
            return native(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            raise

    with (
        patch.object(store.client, "execute", side_effect=query),
        patch(
            "tracer.services.derived_metrics.service._authorized_metric",
            return_value=True,
        ),
        patch(
            "tracer.services.derived_metrics.service._requester_can_run",
            return_value=True,
        ),
    ):
        snapshots.execute_snapshot(run.id, store=store, policy=policy)
    run.refresh_from_db()
    assert not errors, "\n".join(errors)


def overall(store, run):
    return store.execute(
        "SELECT value, unit_count FROM derived_metric_results WHERE run_id = %(run)s AND result_id = %(result)s AND rollup = 'overall'",
        {"run": str(run.id), "result": str(run.result_id)},
    )


def terminate_gate(gate):
    """Kill ONLY a gate backend and wait until it has exited.

    pg_terminate_backend only signals: the gate's transaction-level advisory
    lock is released when that backend exits, which can lag the call. A
    takeover attempted before then would legitimately find the gate held.
    """
    from time import monotonic, sleep

    from django.db import connection

    with gate.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        pid = cursor.fetchone()[0]
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
        assert cursor.fetchone()[0]
        deadline = monotonic() + 10
        while True:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", [pid]
            )
            if not cursor.fetchone()[0]:
                return
            assert monotonic() < deadline, "terminated gate backend did not exit"
            sleep(0.02)


@pytest.mark.parametrize("pause", ["retention_probe", "before_purge", "native_delete"])
def test_expired_capture_cannot_take_over_a_paused_destructive_setup(
    capture_case, live_writer, monkeypatch, pause
):
    """A run lease is not a DELETE fence; takeover must respect the run gate."""
    from tracer.tests.test_derived_metric_integration_fences import (
        assert_lifecycle_available,
    )

    _, run, _ = capture_case
    paused = []

    def attempt_takeover():
        if paused:
            return
        paused.append(True)
        assert_lifecycle_available(run.definition.metric.project_id)
        current = DerivedMetricRun.objects.get(pk=run.id)
        old_attempt = current.attempt_id
        DerivedMetricRun.objects.filter(pk=run.id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        # Without the independent gate B completes in this exact pause and A's
        # resumed purge destroys B's rows. With it B cannot claim the namespace,
        # even during a native DELETE, and durable recovery retries after exit.
        snapshots.execute_snapshot(run.id, store=live_writer.store, policy=lambda _: 0)
        current.refresh_from_db()
        assert current.status == "running"
        assert current.attempt_id == old_attempt
        assert MetricGenerationPin.objects.filter(pk=current.pin_id).exists()

    name = {
        "retention_probe": "_assert_retained",
        "before_purge": "purge_run",
        "native_delete": "delete_rows",
    }[pause]
    original = getattr(snapshots, name)

    def paused_setup(*args, **kwargs):
        if pause == "retention_probe":
            value = original(*args, **kwargs)
            attempt_takeover()
            return value
        attempt_takeover()
        return original(*args, **kwargs)

    if pause == "native_delete":
        native = live_writer.store.execute

        def paused_native(sql, *args, **kwargs):
            if sql.startswith("ALTER TABLE derived_metric_results DELETE"):
                attempt_takeover()
            return native(sql, *args, **kwargs)

        monkeypatch.setattr(live_writer.store, "execute", paused_native)
    else:
        monkeypatch.setattr(snapshots, name, paused_setup)
    execute(run, live_writer.store)
    assert paused and run.status == "complete", run.error
    assert overall(live_writer.store, run) == [(3.0, 4)]
    committed = (run.attempt_id, run.result_id)
    execute(run, live_writer.store)
    assert (run.attempt_id, run.result_id) == committed
    assert overall(live_writer.store, run) == [(3.0, 4)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]


@pytest.mark.parametrize("pause", ["before_purge", "native_delete"])
def test_disconnected_capture_gate_cannot_delete_successor_namespace(
    capture_case, live_writer, monkeypatch, pause
):
    """Terminate ONLY A's gate backend; B completes before A's DELETE resumes."""
    from django.db import connections

    _, run, _ = capture_case
    copy_database = connections["default"].copy
    gates, completed, old_namespaces = [], [], []

    def remember_gate(*args, **kwargs):
        database = copy_database(*args, **kwargs)
        if kwargs.get("alias") == "metric_snapshot_execution":
            gates.append(database)
        return database

    monkeypatch.setattr(connections["default"], "copy", remember_gate)

    def complete_successor():
        if old_namespaces:
            return
        old = DerivedMetricRun.objects.get(pk=run.id)
        old_namespaces.append((old.attempt_id, old.snapshot_id))
        terminate_gate(gates[0])
        # The ORM and native connections remain usable; only the exclusion gate
        # was lost. Its absence is NOT evidence that A stopped destructive work.
        DerivedMetricRun.objects.filter(pk=run.id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        snapshots.execute_snapshot(run.id, store=live_writer.store, policy=lambda _: 0)
        successor = DerivedMetricRun.objects.get(pk=run.id)
        assert successor.status == "complete", successor.error
        assert successor.attempt_id != old.attempt_id
        assert successor.snapshot_id != old.snapshot_id
        assert successor.result_id != old.attempt_id
        assert overall(live_writer.store, successor) == [(3.0, 4)]
        completed.append(
            (successor.attempt_id, successor.snapshot_id, successor.result_id)
        )

    purge, native = snapshots.purge_run, live_writer.store.execute

    def paused_purge(*args, **kwargs):
        complete_successor()
        return purge(*args, **kwargs)

    def paused_delete(sql, *args, **kwargs):
        if sql.startswith("ALTER TABLE derived_metric_results DELETE"):
            complete_successor()
        return native(sql, *args, **kwargs)

    if pause == "before_purge":
        monkeypatch.setattr(snapshots, "purge_run", paused_purge)
    else:
        monkeypatch.setattr(live_writer.store, "execute", paused_delete)
    execute(run, live_writer.store)
    assert len(completed) == 1, (run.status, run.error, len(gates))
    assert (run.attempt_id, run.snapshot_id, run.result_id) == completed[0]
    assert run.status == "complete"
    assert overall(live_writer.store, run) == [(3.0, 4)]
    # A's eventual failure obligation is ALSO attempt-qualified. It must be safe
    # to drain both the takeover's obligation and A's duplicate failure receipt.
    jobs = MetricErasureRequest.objects.filter(
        run_key=run.id, store_payload__protocol="metric_snapshot_attempt_cleanup"
    )
    assert jobs.count() == 1
    assert jobs.get().result_key == old_namespaces[0][0]
    assert jobs.get().snapshot_key == old_namespaces[0][1]
    for job in jobs:
        assert erasure.process_erasure(job.id, store=live_writer.store)
    assert overall(live_writer.store, run) == [(3.0, 4)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_commits WHERE run_id = %(r)s AND result_id = %(s)s",
        {"r": str(run.id), "s": str(run.result_id)},
    ) == [(1,)]


@pytest.mark.parametrize("table", ["derived_metric_results", "derived_metric_commits"])
def test_disconnected_redaction_gate_cannot_delete_successor_results(
    capture_case, live_writer, monkeypatch, table
):
    """A paused redactor loses ONLY its gate backend; a takeover completes.

    The resumed redactor must not delete the successor's rebuilt result or
    commit rows: its run-wide DELETEs are dispatched under its own attempt,
    which the takeover replaced, so they are rejected before native I/O.
    """
    from django.db import connections

    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete", run.error
    child = _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="run",
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        metric_key=metric.id,
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": "users",
            "subjects": ["erase-me"],
            "removed_units": 1,
        },
    )
    copy_database = connections["default"].copy
    gates, completed, takeovers = [], [], []

    def remember_gate(*args, **kwargs):
        database = copy_database(*args, **kwargs)
        if kwargs.get("alias") == "metric_snapshot_execution":
            gates.append(database)
        return database

    monkeypatch.setattr(connections["default"], "copy", remember_gate)
    fenced = erasure.fenced_delete_rows

    def paused(store_, run_, target, *args, **kwargs):
        if target == table and not takeovers:
            # Only A pauses; the takeover B below runs this same hook unpaused.
            takeovers.append(run_.attempt_id)
            paused_attempt = run_.attempt_id
            terminate_gate(gates[0])
            # A's request claim expires while it is paused; B takes over the
            # same redaction, acquires the released gate and completes it.
            MetricErasureRequest.objects.filter(pk=child.id).update(
                claim_expires_at=timezone.now() - timedelta(seconds=1)
            )
            assert erasure.process_erasure(child.id, store=store)
            successor = DerivedMetricRun.objects.get(pk=run.id)
            assert successor.status == "complete"
            assert successor.attempt_id != paused_attempt
            assert overall(store, successor) == [(4.0, 3)]
            completed.append((successor.attempt_id, successor.result_id))
        return fenced(store_, run_, target, *args, **kwargs)

    monkeypatch.setattr(erasure, "fenced_delete_rows", paused)
    # A resumes into its fenced DELETE with a replaced attempt: the dispatch CAS
    # rejects it before native I/O, and its stale claim cannot record anything.
    with pytest.raises(MetricStateConflict, match="claim is not held"):
        erasure.process_erasure(child.id, store=store)
    assert len(completed) == 1
    assert (
        not MetricErasureRequest.objects.filter(
            run_key=run.id,
            store_payload__protocol=snapshots.SUBMISSION_PROTOCOL,
            store_payload__phase="dispatching",
        )
        .exclude(status="done")
        .exists()
    )
    run.refresh_from_db()
    assert (run.attempt_id, run.result_id) == completed[0]
    assert run.status == "complete"
    assert overall(store, run) == [(4.0, 3)]
    assert store.execute(
        "SELECT count() FROM derived_metric_commits WHERE run_id = %(r)s AND result_id = %(s)s",
        {"r": str(run.id), "s": str(run.result_id)},
    ) == [(1,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
    ) == [(0,)]
    child.refresh_from_db()
    assert child.status == "done"


@pytest.mark.parametrize("successor", ["capture_sweep", "second_parent", "reassigned"])
def test_failed_redaction_child_keeps_its_completed_namespace(
    capture_case, live_writer, monkeypatch, successor
):
    """A redaction child fails AFTER reopening a completed capture.

    The child's flip (running, new attempt, 15-minute lease) commits before its
    drain. Once it fails, its gate is released and the lease expires. Neither
    the capture sweep nor another erasure parent may treat that reopened run as
    an abandoned capture: no fresh namespace, no attempt/failed-capture purge of
    its frozen inputs. The child's retry must rebuild the SAME namespace, and a
    namespace that changed anyway fails closed instead of committing empty.
    """
    from tracer.tasks.derived_metric import cleanup_derived_metric_snapshots

    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete", run.error
    snapshot, completed_at = run.snapshot_id, run.completed_at

    def frozen_inputs():
        return store.execute(
            "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
            {"s": str(snapshot)},
        )[0][0]

    def purge_intents():
        # Whole-run failure purges and attempt-namespace cleanups of this run.
        return (
            MetricErasureRequest.objects.filter(run_key=run.id)
            .filter(
                Q(deletion_key=f"failed-capture:{run.id}")
                | Q(store_payload__protocol="metric_snapshot_attempt_cleanup")
            )
            .count()
        )

    assert frozen_inputs() == 4 and purge_intents() == 0

    def unproven(*args, **kwargs):
        raise MetricStateConflict("snapshot write drainage is not yet proven")

    parent = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    with monkeypatch.context() as patched:
        patched.setattr(erasure, "drain_submissions", unproven)
        assert not erasure.process_erasure(parent.id, store=store, writer=live_writer)
    child = MetricErasureRequest.objects.get(
        deletion_key=f"redact:{parent.id}:{run.id}"
    )
    assert child.status == "failed"
    run.refresh_from_db()
    # Reopened (running, completed_at kept): snapshots.redaction_owned.
    assert run.status == "running" and run.completed_at == completed_at
    assert run.snapshot_id == snapshot and frozen_inputs() == 4
    # Its gate is gone (the child returned) and its 15-minute lease expired.
    DerivedMetricRun.objects.filter(pk=run.id).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    if successor == "capture_sweep":
        enqueued = []
        with (
            patch.object(erasure, "sweep", return_value=0),
            patch("tracer.tasks.derived_metric.metrics_enabled", return_value=True),
            patch.object(snapshots, "enqueue_capture", side_effect=enqueued.append),
        ):
            cleanup_derived_metric_snapshots._original_func()
        assert run.id not in enqueued
        # Not even a stale direct enqueue may claim the reopened namespace.
        execute(run, store)
        assert run.snapshot_id == snapshot and run.status == "running"
        assert purge_intents() == 0 and frozen_inputs() == 4
    elif successor == "second_parent":
        other = erasure.erase_metric_data(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope_kind="users",
            subject_ids=["nobody"],
        )
        with monkeypatch.context() as patched:
            # The first child keeps failing while the second parent runs.
            patched.setattr(erasure, "drain_submissions", unproven)
            assert not erasure.process_erasure(
                other.id, store=store, writer=live_writer
            )
        run.refresh_from_db()
        assert run.status == "running", (run.status, run.error)
        assert run.completed_at == completed_at
        assert run.snapshot_id == snapshot and purge_intents() == 0
        assert frozen_inputs() == 4
        # Once drainage is provable the second parent drives the first child,
        # then completes on a later pass over the completed namespace.
        for _ in range(3):
            if erasure.process_erasure(other.id, store=store, writer=live_writer):
                break
        other.refresh_from_db()
        assert other.status == "done"
    else:
        # A namespace reassigned anyway (e.g. by a pre-fix takeover) is never
        # redacted from S while committing a rebuild of the other namespace.
        DerivedMetricRun.objects.filter(pk=run.id).update(snapshot_id=uuid4())
        assert not erasure.process_erasure(child.id, store=store)
        run.refresh_from_db()
        assert run.status == "running" and run.result_id is None
        assert not store.execute(
            "SELECT count() FROM derived_metric_commits WHERE run_id = %(r)s "
            "AND result_id = %(a)s",
            {"r": str(run.id), "a": str(run.attempt_id)},
        )[0][0]
        assert frozen_inputs() == 4
        return
    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
    run.refresh_from_db()
    assert run.status == "complete" and run.snapshot_id == snapshot
    assert run.removed_unit_count == 1 and run.change_reason == "data_erasure"
    assert overall(store, run) == [(4.0, 3)]
    assert frozen_inputs() == 3 and purge_intents() == 0
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
    ) == [(0,)]


def capture_purges(run):
    """Whole-run failure purges and attempt-namespace cleanups of one run."""
    failed = Q(deletion_key=f"failed-capture:{run.id}")
    attempt = Q(store_payload__protocol="metric_snapshot_attempt_cleanup")
    return MetricErasureRequest.objects.filter(failed | attempt, run_key=run.id)


@pytest.mark.parametrize("decided", ["completes_after_cursor", "still_running"])
def test_parent_routes_a_capture_from_its_current_state_not_its_cursor(
    capture_case, live_writer, monkeypatch, decided
):
    """The parent's run cursor is a snapshot taken when it opened.

    A capture's commits INSERT passes its dispatch CAS before the erasure
    request exists, so capture_blocked cannot fence it, and its final
    transition does not recheck it. The parent's cursor reads the run as
    running. If the capture completes before the parent decides, the parent
    must redact the COMPLETED snapshot through a child, never fail-and-purge
    it (that left status complete with no rows and no data_erasure mark). If it
    is still running when the parent decides, the parent fails it and purges
    ONLY that attempt's namespace, once the attempt's sender has drained.
    """
    metric, run, _ = capture_case
    store = live_writer.store
    native = store.client.execute
    parents, cursor = [], []

    def open_parent_cursor(sql, *args, **kwargs):
        if (
            not parents
            and kwargs.get("query_id", "").startswith("metric-snapshot-")
            and sql.startswith("INSERT INTO derived_metric_commits")
        ):
            # The commit's dispatch CAS has committed; the INSERT is not sent.
            parent = erasure.erase_metric_data(
                organization_id=metric.organization_id,
                project_id=metric.project_id,
                scope_kind="users",
                subject_ids=["erase-me"],
            )
            parents.append(parent)
            cursor.extend(erasure._runs(parent))
            assert [(r.id, r.status) for r in cursor] == [(run.id, "running")]
            if decided == "still_running":
                # Decided before the capture's sender drained: the attempt
                # cleanup cannot prove drainage yet, so the parent waits.
                assert not erasure.process_erasure(
                    parent.id, store=store, writer=live_writer
                )
        return native(sql, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(store.client, "execute", open_parent_cursor)
        execute(run, store)
    (parent,) = parents
    real_runs = erasure._runs

    class StaleCursor:
        """The parent's server-side cursor: rows as of when it opened."""

        def __init__(self, queryset):
            self.queryset = queryset

        def iterator(self, chunk_size=None):
            return iter(cursor)

        def __getattr__(self, name):
            return getattr(self.queryset, name)

    def runs_for(request):
        if request.id == parent.id:
            return StaleCursor(real_runs(request))
        return real_runs(request)

    monkeypatch.setattr(erasure, "_runs", runs_for)
    snapshot, attempt = run.snapshot_id, run.attempt_id

    def frozen_inputs():
        return store.execute(
            "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
            {"s": str(snapshot)},
        )[0][0]

    if decided == "completes_after_cursor":
        assert run.status == "complete", run.error
        assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
        run.refresh_from_db()
        assert run.status == "complete" and run.snapshot_id == snapshot
        assert run.change_reason == "data_erasure" and run.removed_unit_count == 1
        assert overall(store, run) == [(4.0, 3)]
        assert frozen_inputs() == 3
        child = MetricErasureRequest.objects.get(
            deletion_key=f"redact:{parent.id}:{run.id}"
        )
        assert child.status == "done"
        # Neither a whole-run purge nor an attempt purge of a completed capture.
        assert not capture_purges(run).exists()
    else:
        # The parent failed the capture: its final transition was rejected.
        assert run.status == "failed" and run.result_id is None
        assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
        (job,) = capture_purges(run)
        assert job.deletion_key == f"capture-attempt:{run.id}:{attempt}"
        assert (job.snapshot_key, job.result_key) == (snapshot, attempt)
        assert job.status == "done"
        assert frozen_inputs() == 0
        for table in ("derived_metric_results", "derived_metric_commits"):
            assert store.execute(
                f"SELECT count() FROM {table} WHERE run_id = %(r)s",
                {"r": str(run.id)},
            ) == [(0,)]
    parent.refresh_from_db()
    assert parent.status == "done"
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
    ) == [(0,)]


def detached_attempt(metric, run, store):
    """A claimed attempt A1 whose worker died after writing part of its namespace.

    Its frozen inputs, an overall result and a commit row are under its own
    snapshot and result namespaces, and one submission it prepared never
    reached its dispatch CAS. Its 15-minute lease has expired.
    """
    from tracer.services.derived_metrics.queries import QUERY_SETTINGS, rollup_insert

    a1, s1 = uuid4(), uuid4()
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running",
        attempt_id=a1,
        snapshot_id=s1,
        result_id=None,
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    run.refresh_from_db()
    settings = {**QUERY_SETTINGS, "max_threads": 1, "enable_analyzer": 0}
    sql, params = snapshots.capture_select(
        run, run.pin.manifest, run.window_start, run.window_end
    )
    store.client.execute(sql, params, settings=settings)
    sql, params = rollup_insert(run.definition, run, a1, "overall")
    store.client.execute(sql, params, settings=settings)
    store.client.execute(
        "INSERT INTO derived_metric_commits VALUES (%(org_id)s, %(workspace_id)s, "
        "%(project_id)s, %(metric_id)s, %(run_id)s, %(result_id)s, %(committed_at)s, 1)",
        {
            **snapshots.scope_for(run).parameters(),
            "metric_id": str(metric.id),
            "run_id": str(run.id),
            "result_id": str(a1),
            "committed_at": timezone.now(),
        },
    )
    leftover = _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="history",
        metric_key=metric.id,
        run_key=run.id,
        snapshot_key=s1,
        store_payload={
            "protocol": snapshots.SUBMISSION_PROTOCOL,
            "phase": "prepared",
            "transport": "native_no_reconnect",
            "query_id": "metric-snapshot-" + uuid4().hex,
        },
    )
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(s1)},
    ) == [(4,)]
    return a1, s1, leftover


def assert_detached_attempt_purged(store, run, a1, s1, leftover):
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(s1)},
    ) == [(0,)]
    for table in ("derived_metric_results", "derived_metric_commits"):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE run_id = %(r)s AND result_id = %(a)s",
            {"r": str(run.id), "a": str(a1)},
        ) == [(0,)]
    leftover.refresh_from_db()
    assert leftover.status == "done"
    assert leftover.receipts == {"not_submitted": {"drained": 1}}


def assert_successor_intact(store, run, a1, s1):
    assert run.status == "complete", run.error
    assert run.attempt_id != a1 and run.snapshot_id != s1
    assert run.result_id == run.attempt_id
    assert overall(store, run) == [(3.0, 4)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_commits WHERE run_id = %(r)s AND result_id = %(a)s",
        {"r": str(run.id), "a": str(run.result_id)},
    ) == [(1,)]


@pytest.mark.parametrize("window", ["prepared", "dispatching"])
def test_detached_attempt_cleanup_never_fences_its_successor(
    capture_case, live_writer, monkeypatch, window
):
    """A takeover records A1's attempt cleanup while A2 starts capturing.

    The erasure sweep processes that cleanup concurrently with A2: nothing
    excludes the two. Its drain must address ONLY A1's namespace. A run-wide
    drain retired A2's prepared submission (A2's dispatch CAS then failed it
    for good) or found A2's dispatching one unproven and failed the cleanup.
    """
    metric, run, _ = capture_case
    store = live_writer.store
    a1, s1, leftover = detached_attempt(metric, run, store)
    cleaned = []

    def process_cleanup():
        cleaned.append(None)
        job = MetricErasureRequest.objects.get(
            deletion_key=f"capture-attempt:{run.id}:{a1}"
        )
        assert (job.snapshot_key, job.result_key) == (s1, a1)
        # A2's open submission (A1's leftover keeps its phase once retired).
        submission = (
            MetricErasureRequest.objects.filter(
                run_key=run.id, store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
            )
            .exclude(status="done")
            .get()
        )
        assert submission.store_payload["phase"] == window
        assert submission.snapshot_key != s1
        cleaned[-1] = erasure.process_erasure(job.id, store=store)

    if window == "prepared":
        clock = snapshots._server_clock

        def prepared(store_):
            # A2's submission is durable as prepared; its CAS has not run.
            if not cleaned:
                process_cleanup()
            return clock(store_)

        monkeypatch.setattr(snapshots, "_server_clock", prepared)
    else:
        native = store.client.execute

        def dispatching(sql, *args, **kwargs):
            # A2's dispatch CAS committed; its INSERT is not on the wire yet.
            if not cleaned and kwargs.get("query_id", "").startswith(
                "metric-snapshot-"
            ):
                process_cleanup()
            return native(sql, *args, **kwargs)

        monkeypatch.setattr(store.client, "execute", dispatching)
    execute(run, store)
    # The cleanup completed in A2's window, without waiting for A2.
    assert cleaned == [True]
    assert_successor_intact(store, run, a1, s1)
    assert_detached_attempt_purged(store, run, a1, s1, leftover)


def test_stale_attempt_setup_drain_never_fences_its_successor(
    capture_case, live_writer, monkeypatch
):
    """A1 pauses between its _current check and its setup drain.

    Its gate backend dies, A2 takes over, and A1's drain lands while A2's
    submission is prepared. A1's drain may address only attempts that are
    already detached, never its live successor's namespace.
    """
    from django.db import connections

    metric, run, _ = capture_case
    store = live_writer.store
    copy_database = connections["default"].copy
    gates, paused, replayed, stale = [], [], [], []

    def remember_gate(*args, **kwargs):
        database = copy_database(*args, **kwargs)
        if kwargs.get("alias") == "metric_snapshot_execution":
            gates.append(database)
        return database

    monkeypatch.setattr(connections["default"], "copy", remember_gate)
    drain, clock = snapshots.drain_submissions, snapshots._server_clock

    def a2_prepared(store_):
        if paused and not replayed:
            replayed.append(True)
            # A1's OWN setup drain call, exactly as A1 issued it.
            args, kwargs = paused[0]
            drain(*args, **kwargs)
        return clock(store_)

    def a1_setup_drain(*args, **kwargs):
        if paused:
            return drain(*args, **kwargs)
        paused.append((args, kwargs))
        a1 = DerivedMetricRun.objects.get(pk=run.id)
        assert a1.status == "running" and a1.attempt_id
        terminate_gate(gates[0])
        DerivedMetricRun.objects.filter(pk=run.id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        snapshots.execute_snapshot(run.id, store=store, policy=lambda _: 0)
        stale.append((a1.attempt_id, a1.snapshot_id))
        # A1's drain already ran, inside A2's prepared window.
        return None

    monkeypatch.setattr(snapshots, "drain_submissions", a1_setup_drain)
    monkeypatch.setattr(snapshots, "_server_clock", a2_prepared)
    execute(run, store)
    assert replayed and len(stale) == 1
    a1, s1 = stale[0]
    assert_successor_intact(store, run, a1, s1)
    (job,) = capture_purges(run)
    assert job.deletion_key == f"capture-attempt:{run.id}:{a1}"
    assert erasure.process_erasure(job.id, store=store)
    assert_successor_intact(store, run, a1, s1)


def test_90_day_capture_stays_pinned_across_uncommitted_and_later_refresh(
    capture_case, live_writer
):
    metric, run, data = capture_case
    from tracer.services.derived_metrics.reads import _aggregate

    pinned_kpi = _aggregate(
        live_writer.store, metric, metric.active_version, run.pin.manifest, data
    )
    assert (pinned_kpi["value"], pinned_kpi["unit_count"]) == (3.0, 4)
    draft = live_writer.begin()
    sql, params = field_query(
        "unit-0", n=999, start=data["window_end"] - timedelta(days=1), metric=metric.id
    )
    live_writer.freeze(draft, FIELD_VALUES, sql, params)
    live_writer.seal(draft)
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert overall(live_writer.store, run) == [(3.0, 4)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    live_writer.stage_and_publish(draft)
    assert overall(live_writer.store, run) == [(3.0, 4)]


@pytest.mark.parametrize("reason", ["pin", "retention"])
def test_expired_capture_is_failed_not_empty_success(capture_case, live_writer, reason):
    _, run, _ = capture_case
    if reason == "pin":
        MetricGenerationPin.objects.filter(pk=run.pin_id).update(
            captured_at=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() - timedelta(seconds=1),
        )
    execute(run, live_writer.store, policy=lambda _: 30 if reason == "retention" else 0)
    assert run.status == "failed" and run.error == "expired"
    assert run.result_id is None


def test_all_passing_typed_condition_keeps_numeric_indicator(
    live_writer, workspace, observe_project, user
):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Share capture",
    )
    descriptor = {
        "alias": "category",
        "source": "span",
        "field": "attribute_string",
        "attribute_key": "category",
        "reducer": "latest",
        "value_type": "text",
    }
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="share",
        level="conversation",
        formula="",
        inputs=[descriptor],
        field_spec={
            "conditions": [{"alias": "category", "operator": "eq", "value": "pass"}]
        },
        selection_unit="session",
        weighting="none",
        definition_hash="c" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    end = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    attempt = live_writer.begin()
    sql, params = field_query(
        "all-passing", n=1, start=end - timedelta(hours=1), metric=metric.id
    )
    sql = sql.replace("map('y',", "map('field_condition',")
    live_writer.freeze(attempt, FIELD_VALUES, sql, params)
    # The fixture's only input is settled inside the requested window. Publish
    # its consumed cut beyond end + 30m, as the real writer must before capture.
    cut = int((end + timedelta(hours=1)).timestamp()) * 10**9
    live_writer.seal(attempt, cursors={"watermark_ns": cut})
    live_writer.stage_and_publish(attempt)
    with patch.object(snapshots, "enqueue_capture"):
        saved = snapshots.request_snapshot(
            metric,
            {
                "window_start": end - timedelta(days=1),
                "window_end": end,
                "idempotency_key": str(uuid4()),
            },
            user,
        )
    run = DerivedMetricRun.objects.get(pk=saved["id"])
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert overall(live_writer.store, run) == [(1.0, 1)]
    assert live_writer.store.execute(
        "SELECT input_values FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [({"field_condition": 1.0},)]


def test_committed_dispatch_blocks_erasure_without_holding_lifecycle(
    capture_case, live_writer, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections, connection, transaction

    from tracer.services.derived_metrics.deletion_protocol import project_lock

    metric, run, _ = capture_case
    store = live_writer.store
    native = store.client.execute
    attempted = []

    def other_connection_can_lock():
        close_old_connections()
        try:
            with transaction.atomic():
                return project_lock(metric.project_id, blocking=False)
        finally:
            close_old_connections()

    def pause_before_send(sql, *args, **kwargs):
        if not attempted:
            assert not connection.in_atomic_block, "snapshot setup retained a PG lock"
        if kwargs.get("query_id", "").startswith("metric-snapshot-") and not attempted:
            assert not connection.in_atomic_block
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(other_connection_can_lock).result(timeout=5)
            receipt = MetricErasureRequest.objects.get(
                run_key=run.id,
                store_payload__protocol=snapshots.SUBMISSION_PROTOCOL,
            )
            assert receipt.store_payload["phase"] == "dispatching"
            job = erasure.erase_metric_data(
                organization_id=metric.organization_id,
                project_id=metric.project_id,
                scope_kind="project",
            )
            attempted.append(job.id)
            # No query is active yet. Absence is NOT proof: the paused sender
            # still owns a sealed socket and may submit immediately after this.
            assert not erasure.process_erasure(job.id, store=store)
            job.refresh_from_db()
            assert job.status != "done" and job.completed_at is None
        return native(sql, *args, **kwargs)

    monkeypatch.setattr(store.client, "execute", pause_before_send)
    execute(run, store)
    assert len(attempted) == 1
    # The first write can land, but revocation prevents capture publication.
    assert run.status != "complete" and run.result_id is None
    assert erasure.process_erasure(attempted[0], store=store)
    for table in ("derived_metric_inputs", "derived_metric_results"):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE project_id = %(project)s",
            {"project": str(metric.project_id)},
        ) == [(0,)]
    assert run.pin_id is None
    assert not MetricGenerationPin.objects.filter(project_id=metric.project_id).exists()


def test_delete_snapshot_purges_every_result_version(capture_case, live_writer, user):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    store = live_writer.store
    store.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE(generateUUIDv4() AS result_id) FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    )
    snapshots.delete_snapshot(metric, run.id, user)
    job = MetricErasureRequest.objects.get(deletion_key=f"run:{run.id}")
    assert erasure.process_erasure(job.id, store=store)
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    ) == [(0,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(r)s",
        {"r": str(run.snapshot_id)},
    ) == [(0,)]
    run.refresh_from_db()
    assert run.cleanup_status == "done"


def test_redaction_recomputes_and_resumes_after_interrupted_delete(
    capture_case, live_writer
):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    store = live_writer.store
    store.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE(generateUUIDv4() AS result_id) FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    )
    child = _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="run",
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        metric_key=metric.id,
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": "users",
            "subjects": ["erase-me"],
            "removed_units": 1,
        },
    )
    original = erasure.delete_rows
    calls = []

    def interrupt(*args):
        original(*args)
        calls.append(args[1])
        if args[1] == "derived_metric_inputs":
            raise RuntimeError("interrupted")

    with patch.object(erasure, "delete_rows", side_effect=interrupt):
        assert not erasure.process_erasure(child.id, store=store)
    assert "derived_metric_inputs" in calls
    assert erasure.process_erasure(child.id, store=store)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert run.removed_unit_count == 1 and run.changed_at
    assert overall(store, run) == [(4.0, 3)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
    ) == [(0,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(r)s AND (result_id != %(result)s OR group_key = 'erase-me')",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [(0,)]


def test_user_then_organization_erasure_across_snapshots(
    capture_case, live_writer, user
):
    metric, first, data = capture_case
    execute(first, live_writer.store)
    runs = [first]
    for _ in range(2):
        with patch.object(snapshots, "enqueue_capture"):
            saved = snapshots.request_snapshot(
                metric, {**data, "idempotency_key": str(uuid4())}, user
            )
        run = DerivedMetricRun.objects.get(pk=saved["id"])
        execute(run, live_writer.store)
        assert run.status == "complete", run.error
        runs.append(run)
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    assert erasure.process_erasure(
        request.id, store=live_writer.store, writer=live_writer
    )
    for run in runs:
        run.refresh_from_db()
        assert run.removed_unit_count == 1
        assert overall(live_writer.store, run) == [(4.0, 3)]
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id, scope_kind="organization"
    )
    assert erasure.process_erasure(request.id, store=live_writer.store)
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE org_id = %(org)s",
        {"org": str(metric.organization_id)},
    ) == [(0,)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_results WHERE org_id = %(org)s AND unit_count != 0",
        {"org": str(metric.organization_id)},
    ) == [(0,)]
    for run in runs:
        run.refresh_from_db()
        assert run.removed_unit_count == 4
        assert overall(live_writer.store, run) == [(None, 0)]
    request.refresh_from_db()
    assert request.status == "done" and len(request.receipts) == 3


@pytest.mark.parametrize("parent", ["metric", "project", "workspace", "organization"])
def test_hard_delete_retains_and_completes_run_intent(
    capture_case, live_writer, parent
):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    target = {
        "metric": metric,
        "project": metric.project,
        "workspace": metric.workspace,
        "organization": metric.organization,
    }[parent]
    type(target)._base_manager.filter(pk=target.pk).delete()
    assert not DerivedMetricRun.objects.filter(pk=run.id).exists()
    job = MetricErasureRequest.objects.get(deletion_key=f"run:{run.id}")
    assert job.snapshot_key == run.snapshot_id
    assert erasure.process_erasure(job.id, store=live_writer.store)
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]


@pytest.mark.parametrize("failure", ["deleted", "capture_failed", "orphan"])
def test_parent_erasure_waits_for_all_partial_namespace_cleanup(
    capture_case, live_writer, user, failure
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete"
    if failure == "deleted":
        snapshots.delete_snapshot(metric, run.id, user)
    elif failure == "orphan":
        DerivedMetricRun.objects.filter(pk=run.id).delete()
    else:
        DerivedMetricRun.objects.filter(pk=run.id).update(
            status="failed", error="capture_failed"
        )
        _intent(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope="run",
            run_key=run.id,
            snapshot_key=run.snapshot_id,
            metric_key=metric.id,
            deletion_key=f"failed-capture:{run.id}",
        )
    parent = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    original = snapshots.delete_rows

    def interrupt(store, table, predicate, params):
        if table == "derived_metric_inputs":
            raise RuntimeError("interrupted physical purge")
        return original(store, table, predicate, params)

    with patch.object(snapshots, "delete_rows", side_effect=interrupt):
        assert not erasure.process_erasure(parent.id, store=store, writer=live_writer)
    parent.refresh_from_db()
    assert parent.status == "failed" and parent.receipts == {}
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
    parent.refresh_from_db()
    assert parent.status == "done" and len(parent.receipts) == 3
    for table, key, value in (
        ("derived_metric_inputs", "snapshot_id", run.snapshot_id),
        ("derived_metric_results", "run_id", run.id),
        ("derived_metric_commits", "run_id", run.id),
    ):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE {key} = %(key)s", {"key": str(value)}
        ) == [(0,)]


OLD_OWNER = "20000000-0000-0000-0000-000000000001"
NEW_OWNER = "10000000-0000-0000-0000-000000000001"
SHARED_OWNER = "30000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    "capture_case,kind,table",
    [
        ({"user": OLD_OWNER}, "users", "end_user_id_remap"),
        ({"session": OLD_OWNER}, "sessions", "trace_session_id_remap"),
    ],
    indirect=["capture_case"],
)
def test_erasure_finds_owners_captured_before_consolidation(
    capture_case, live_writer, kind, table
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert overall(store, run) == [(3.0, 4)]
    store.execute(
        f"INSERT INTO {table} (old_id, new_id) VALUES "
        "(toUUID(%(old)s), toUUID(%(shared)s)), (toUUID(%(new)s), toUUID(%(shared)s))",
        {"old": OLD_OWNER, "new": NEW_OWNER, "shared": SHARED_OWNER},
    )
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind=kind,
        subject_ids=[NEW_OWNER],
    )
    assert erasure.process_erasure(request.id, store=store, writer=live_writer)
    run.refresh_from_db()
    assert run.removed_unit_count == 1
    assert overall(store, run) == [(4.0, 3)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s "
        "AND (user_id = %(old)s OR session_id = %(old)s)",
        {"s": str(run.snapshot_id), "old": OLD_OWNER},
    ) == [(0,)]


def test_missing_identity_lineage_blocks_zero_match_completion(
    capture_case, live_writer
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=[NEW_OWNER],
    )
    store.execute("DROP TABLE end_user_id_remap")
    assert not erasure.process_erasure(request.id, store=store, writer=live_writer)
    request.refresh_from_db()
    assert request.status == "failed" and request.receipts == {}
    assert overall(store, run) == [(3.0, 4)]


def test_worker_rechecks_pinned_backfill_coverage(capture_case, live_writer):
    _, run, _ = capture_case
    DerivedMetricVersion.objects.filter(pk=run.definition_id).update(
        backfill_cursor={
            "start": (run.window_start + timedelta(minutes=15)).isoformat()
        }
    )
    execute(run, live_writer.store)
    assert run.status == "failed" and run.error == "window_not_calculated"
    assert run.result_id is None
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]


def test_lost_native_ack_drains_from_terminal_log_before_purge(
    capture_case, live_writer
):
    from tracer.models.derived_metric import MetricStateConflict

    metric, run, _ = capture_case
    store = live_writer.store
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    sql, params = snapshots.capture_select(
        run, run.pin.manifest, run.window_start, run.window_end
    )
    native = store.client.execute

    def lose_ack(*args, **kwargs):
        result = native(*args, **kwargs)
        if kwargs.get("query_id", "").startswith("metric-snapshot-"):
            assert store.client.connection.disable_reconnect is True
            raise ConnectionError("lost native acknowledgement")
        return result

    with patch.object(store.client, "execute", side_effect=lose_ack):
        with pytest.raises(ConnectionError):
            snapshots._write(store, run, sql, params)
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert job.status != "done" and job.receipts == {}
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    store.execute("SYSTEM FLUSH LOGS")
    snapshots.drain_submissions(
        store, organization_id=metric.organization_id, run_id=run.id
    )
    job.refresh_from_db()
    assert job.receipts == {"terminal_query": {"drained": 1}}
    snapshots.purge_run(
        store,
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        run_id=run.id,
        snapshot_id=run.snapshot_id,
    )
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]
    # A legacy no-terminal query on the same empty process list stays pending.
    _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="history",
        store_payload={
            "protocol": snapshots.SUBMISSION_PROTOCOL,
            "query_id": "never-dispatched",
        },
    )
    with pytest.raises(MetricStateConflict, match="drainage"):
        snapshots.drain_submissions(store, organization_id=metric.organization_id)


def test_rollup_pages_keep_duplicate_groups_whole(
    capture_case, live_writer, monkeypatch
):
    _, run, _ = capture_case
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1)
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert live_writer.store.execute(
        "SELECT group_key, value, unit_count FROM derived_metric_results "
        "WHERE run_id = %(r)s AND result_id = %(result)s AND rollup = 'user' ORDER BY group_key",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [("erase-me", 0.0, 1), ("keep-me", 4.0, 3)]


def test_rollup_pages_preserve_window_wide_scope_rejection(
    capture_case, live_writer, monkeypatch
):
    _, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    store.execute(
        "ALTER TABLE derived_metric_inputs UPDATE sampling_scope = user_id "
        "WHERE snapshot_id = %(s)s SETTINGS mutations_sync=2",
        {"s": str(run.snapshot_id)},
    )
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1)
    snapshots.commit_results(store, run)
    run.refresh_from_db()
    assert store.execute(
        "SELECT group_key, status, value FROM derived_metric_results "
        "WHERE run_id = %(r)s AND result_id = %(result)s AND rollup = 'user' ORDER BY group_key",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [
        ("erase-me", "invalid_probability", None),
        ("keep-me", "invalid_probability", None),
    ]


def test_rollups_and_erasure_exceed_one_million_sessions(capture_case, live_writer):
    """Reduced-width >1M cardinality gate; the 10M VM qualification is separate."""
    from time import monotonic

    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete"
    store.execute(
        "ALTER TABLE derived_metric_inputs DELETE WHERE snapshot_id = %(s)s SETTINGS mutations_sync=2",
        {"s": str(run.snapshot_id)},
    )
    count = 1000001
    store.execute(
        "INSERT INTO derived_metric_inputs "
        "(org_id, workspace_id, project_id, snapshot_id, unit_id, session_id, user_id, "
        "cohort, day, input_values, probability, invalid_probability, source_rows, sampling_design) "
        "SELECT toUUID(%(org)s), toUUID(%(workspace)s), toUUID(%(project)s), toUUID(%(snapshot)s), "
        "toString(number), toString(number), if(number=0, 'erase-me', 'keep-me'), '', "
        "toDate('2026-01-01'), map('y', toNullable(toFloat64(number %% 2))), 1., 0, 1, 'live' "
        "FROM numbers(%(count)s)",
        {
            "org": str(metric.organization_id),
            "workspace": str(metric.workspace_id),
            "project": str(metric.project_id),
            "snapshot": str(run.snapshot_id),
            "count": count,
        },
    )
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    started = monotonic()
    snapshots.commit_results(store, run)
    run.refresh_from_db()
    assert overall(store, run) == [(500000 / count, count)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s "
        "AND result_id = %(result)s AND rollup = 'session'",
        {"run": str(run.id), "result": str(run.result_id)},
    ) == [(count,)]
    child = _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="run",
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        metric_key=metric.id,
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": "users",
            "subjects": ["erase-me"],
            "removed_units": 1,
        },
    )
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert overall(store, run) == [(0.5, 1000000)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s "
        "AND result_id = %(result)s AND rollup = 'session'",
        {"run": str(run.id), "result": str(run.result_id)},
    ) == [(1000000,)]
    print(
        f"B18 cardinality: {count} session rollups materialized; 1000000 after erasure; {monotonic() - started:.2f}s"
    )
