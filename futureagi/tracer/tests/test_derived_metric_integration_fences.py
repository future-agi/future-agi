# ruff: noqa: F811 -- shared isolated PostgreSQL fixtures
"""Dispatch must not monopolize lifecycle locks or strand pooled backend locks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic
from unittest.mock import Mock

import pytest
from django.db import close_old_connections, connection, transaction

from tracer.models.derived_metric import MetricErasureRequest, MetricProjectRefresh
from tracer.services.derived_metrics import admission, erasure, snapshots
from tracer.services.derived_metrics.deletion_protocol import (
    lifecycle_lock_budget,
    project_lock,
    serialized_project,
)
from tracer.services.derived_metrics.lease import MetricPassDeadline, ProjectLease
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.tests.test_derived_metric_admission import (  # noqa: F401
    NOW,
    enabled,
    state_factory,
)
from tracer.tests.test_derived_metric_refresh import Recorder
from tracer.tests.test_derived_metric_snapshots import (  # noqa: F401
    running_capture,
    snapshot_metric,
)

pytestmark = pytest.mark.django_db(transaction=True)


def on_other_connection(operation):
    def run():
        close_old_connections()
        try:
            return operation()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result(timeout=5)


def assert_lifecycle_available(project_id):
    assert not connection.in_atomic_block, "native I/O kept the ORM transaction open"

    def acquire():
        with transaction.atomic():
            return project_lock(project_id, blocking=False)

    assert on_other_connection(acquire), "native I/O retained the lifecycle key"


def test_snapshot_probe_insert_recovery_and_drain_release_lifecycle(
    snapshot_metric, user
):
    from tracer.models.derived_metric import MetricStateConflict

    run = running_capture(snapshot_metric, user)
    store = Mock()
    clock = ["server", 1000, 500]
    observations = []

    def native_observation(sql, params=None):
        assert_lifecycle_available(snapshot_metric.project_id)
        observations.append(sql)
        return [tuple(clock)] if "serverUUID" in sql else [(0,)]

    def ambiguous_insert(*args, **kwargs):
        assert_lifecycle_available(snapshot_metric.project_id)
        # The dispatching marker is committed on another connection BEFORE send.
        assert on_other_connection(
            lambda: MetricErasureRequest.objects.filter(
                run_key=run.id,
                store_payload__protocol=snapshots.SUBMISSION_PROTOCOL,
                store_payload__phase="dispatching",
            ).exists()
        )
        raise ConnectionError("unknown native outcome")

    store.execute.side_effect = native_observation
    store.client.execute.side_effect = ambiguous_insert
    with pytest.raises(ConnectionError):
        snapshots._write(store, run, "INSERT INTO unused VALUES (1)", {})
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    snapshots.arm_submission_recovery(job.id, store=store)
    with pytest.raises(MetricStateConflict, match="drainage"):
        snapshots.drain_submissions(
            store, organization_id=snapshot_metric.organization_id
        )
    job.refresh_from_db()
    assert job.status != "done" and job.receipts == {}
    clock[:] = ["server", 1200, 10]
    snapshots.drain_submissions(store, organization_id=snapshot_metric.organization_id)
    job.refresh_from_db()
    assert job.receipts == {"server_restart": {"drained": 1}}
    assert len(observations) == 8


@pytest.mark.parametrize("budget", ["activity", "lease"])
@pytest.mark.parametrize("blocking_locks", ["lifecycle", "row", "both"])
def test_refresh_lock_contention_yields_and_releases_fleet_slot(
    enabled, state_factory, budget, blocking_locks
):
    state = state_factory(pending_backfill=True, refresh_requested_at=NOW)
    other = state_factory(pending_backfill=True)
    # Select this state deterministically before the second project.
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        created_at=NOW - timedelta(days=2)
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert reservation.grant.project_id == state.project_id
    entered, finish = Event(), Event()

    def blocker():
        close_old_connections()
        try:
            with transaction.atomic():
                if blocking_locks in {"lifecycle", "both"}:
                    project_lock(state.project_id)
                if blocking_locks in {"row", "both"}:
                    MetricProjectRefresh.objects.select_for_update().get(pk=state.pk)
                entered.set()
                assert finish.wait(10)
        finally:
            close_old_connections()

    futures = []

    def run(lease, purpose):
        writer = RefreshWriter(lease, Recorder(), org_id=state.project.organization_id)
        attempt = writer.begin()
        futures.append(pool.submit(blocker))
        assert entered.wait(5)
        if budget == "activity":
            lease.deadline = monotonic() + 0.2
        else:
            lease.expires_at = lease.clock() + timedelta(seconds=0.2)
            # A real advancing clock is needed for the lease-only case.
            started = monotonic()
            lease.clock = lambda: NOW + timedelta(seconds=monotonic() - started)
        before = monotonic()
        try:
            if blocking_locks == "row":
                # Ledger append locks the attempt, not the refresh row. Exercise
                # a real refresh-row acquisition in the row-only interleaving.
                lease.renew()
            writer.append(attempt, "membership_preimage", [{"unit_id": "private"}])
            pytest.fail("contended append ignored the writer budget")
        finally:
            assert monotonic() - before < 1.5
            assert not finish.is_set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            before = monotonic()
            assert not admission.execute_reservation(
                reservation.payload(), clock=lambda: NOW, runner=run
            )
            # Include exception bookkeeping AND finally cleanup, not just append.
            assert monotonic() - before < 1.5
            state.refresh_from_db()
            if blocking_locks in {"row", "both"}:
                assert state.lease_token == reservation.grant.lease_token
            else:
                assert state.lease_token is None
            assert state.consecutive_failures == 0
            assert state.pending_backfill and state.refresh_requested_at == NOW
            assert entered.is_set() and not finish.is_set()
            # The SAME contended lifecycle key remains held. Another project can
            # nevertheless execute: the timed-out worker released the fleet slot.
            with admission.execution_slot(other.project_id) as available:
                assert available
            assert not state.project.metric_refresh_attempts.filter(
                ledger_chunks__isnull=False
            ).exists()
        finally:
            finish.set()
            for future in futures:
                future.result(timeout=5)


def test_queued_rejection_does_not_wait_on_refresh_row(enabled, state_factory):
    state = state_factory()
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    entered, finish = Event(), Event()

    def blocker():
        close_old_connections()
        try:
            with transaction.atomic():
                MetricProjectRefresh.objects.select_for_update().get(pk=state.pk)
                entered.set()
                assert finish.wait(10)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(blocker)
        try:
            assert entered.wait(5)
            before = monotonic()
            admission._release_queued(reservation)
            assert monotonic() - before < 1
            state.refresh_from_db()
            assert state.lease_token == reservation.grant.lease_token
            assert not finish.is_set()
        finally:
            finish.set()
            future.result(timeout=5)
    admission._release_queued(reservation)
    state.refresh_from_db()
    assert state.lease_token is None


def test_pinned_read_releases_lifecycle_and_rejects_midread_revocation(snapshot_metric):
    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import revoke_generations
    from tracer.services.derived_metrics.publication import pinned_read

    project_id = snapshot_metric.project_id
    with pytest.raises(MetricStateConflict, match="expired"):
        with pinned_read(
            Mock(assert_visible=Mock()), project_id, metric_id=snapshot_metric.id
        ):
            assert_lifecycle_available(project_id)
            # A real second PG connection commits erasure while IO is paused.
            on_other_connection(lambda: revoke_generations(project_id, reason="erased"))


@pytest.mark.parametrize("change", ["redaction", "deletion", "metric_deleted"])
def test_snapshot_read_revalidates_after_unlocked_native_io(
    snapshot_metric, user, change
):
    from uuid import uuid4

    from tracer.models.derived_metric import DerivedMetricRun
    from tracer.services.derived_metrics.errors import MetricInputError

    run = running_capture(snapshot_metric, user)
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="complete", result_id=uuid4()
    )
    with pytest.raises(MetricInputError, match="changed"):
        with snapshots.snapshot_read(run):
            assert_lifecycle_available(snapshot_metric.project_id)
            if change == "metric_deleted":
                on_other_connection(
                    lambda: (
                        type(snapshot_metric)
                        ._base_manager.filter(pk=snapshot_metric.id)
                        .update(deleted=True)
                    )
                )
            else:
                updates = (
                    {"attempt_id": uuid4()}
                    if change == "redaction"
                    else {"deleted_at": NOW, "cleanup_status": "pending"}
                )
                on_other_connection(
                    lambda: DerivedMetricRun.objects.filter(pk=run.id).update(**updates)
                )


@pytest.mark.parametrize("expired", [False, True])
def test_initial_empty_policy_proof_still_checks_private_preimages(
    state_factory, expired
):
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    state = state_factory()
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="empty-proof", ttl=timedelta(minutes=5), now=NOW
    )
    lease = ProjectLease(grant, clock=lambda: NOW)
    # Recorder deliberately has no execute(): no native scan is justified for a
    # parentless sealed proposal without any frozen rows. Private recovery still
    # needs the same final current-policy check as a nonempty proposal.
    store = Recorder()
    writer = RefreshWriter(
        lease, store, org_id=state.project.organization_id, retention_days=lambda: 90
    )
    attempt = writer.begin(
        versions=[{"level": "span"}],
        ledger={
            "membership_preimage": [
                {"unit_start": (NOW - timedelta(days=91 if expired else 1)).isoformat()}
            ]
        },
    )
    writer.seal(attempt)
    try:
        if expired:
            with pytest.raises(MetricRetentionChanged):
                writer.stage_and_publish(attempt)
            state.refresh_from_db()
            assert state.head_manifest_id is None
        else:
            assert writer.stage_and_publish(attempt)
        assert store.writes == []
    finally:
        lease.release()


def test_project_lease_budget_bounds_nested_model_locks_and_resets(state_factory):
    state = state_factory()
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="nested-budget", ttl=timedelta(minutes=5), now=NOW
    )
    lease = ProjectLease(grant, clock=lambda: NOW, deadline=monotonic() - 1)
    with lifecycle_lock_budget(lease), transaction.atomic():
        with pytest.raises(MetricPassDeadline):
            project_lock(state.project_id)
    # Context reset cannot leak a previous writer's expired budget into cleanup.
    with serialized_project(state.project_id):
        pass
    lease.release()


def test_erasure_mutex_is_transaction_owned_reentrant_and_exception_safe(
    workspace, monkeypatch
):
    from django.db.backends.utils import CursorWrapper

    execute, lock_sql = CursorWrapper.execute, []

    def record(cursor, sql, params=None):
        if "advisory" in sql:
            lock_sql.append(sql)
        return execute(cursor, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", record)
    organization_id = workspace.organization_id

    def another_worker():
        with erasure._erasure_mutex(organization_id) as held:
            return held

    with pytest.raises(RuntimeError, match="interrupted"):
        with erasure._erasure_mutex(organization_id) as held:
            assert held
            assert on_other_connection(another_worker) is False
            with erasure._erasure_mutex(organization_id) as child_held:
                assert child_held
            assert on_other_connection(another_worker) is False
            raise RuntimeError("interrupted")
    assert on_other_connection(another_worker) is True
    assert lock_sql and all("pg_try_advisory_xact_lock" in sql for sql in lock_sql)
    # No session-level advisory lock can remain on a transaction-pool backend.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND granted AND pid = pg_backend_pid()"
        )
        assert cursor.fetchone()[0] == 0


def redactable_run(metric, user, state):
    """A run in a state a redaction child can actually find.

    Children are created only for COMPLETED runs, so the old sender belongs to
    the completed attempt, or to an earlier redaction attempt of the same
    child that failed after its reopen (running, completed_at kept, lease
    expired, no result).
    """
    from django.utils import timezone

    from tracer.models.derived_metric import DerivedMetricRun

    run = running_capture(metric, user)
    if state == "completed":
        update = {"status": "complete", "completed_at": timezone.now()}
    else:
        update = {
            "completed_at": timezone.now() - timedelta(hours=1),
            "result_id": None,
            "lease_expires_at": timezone.now() - timedelta(seconds=1),
        }
    DerivedMetricRun.objects.filter(pk=run.id).update(**update)
    run.refresh_from_db()
    return run


@pytest.mark.parametrize("state", ["completed", "reopened_by_failed_redaction"])
def test_redaction_replaces_old_sender_before_unlocked_drain(
    snapshot_metric, user, monkeypatch, state
):
    from uuid import uuid4

    from tracer.models.derived_metric import DerivedMetricRun, MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent

    old_run = redactable_run(snapshot_metric, user, state)
    job = _intent(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope="run",
        run_key=old_run.id,
        snapshot_key=old_run.snapshot_id,
        store_payload={"protocol": "metric_snapshot_redaction"},
    )
    observed = []

    class DrainPaused(Exception):
        pass

    def observe_drain(*args, **kwargs):
        assert_lifecycle_available(snapshot_metric.project_id)
        attempt_id = on_other_connection(
            lambda: DerivedMetricRun.objects.get(pk=old_run.id).attempt_id
        )
        assert attempt_id != old_run.attempt_id
        stale_sender = Mock()
        with pytest.raises(MetricStateConflict, match="expired capture"):
            snapshots._write(stale_sender, old_run, "INSERT INTO unused VALUES (1)", {})
        stale_sender.execute.assert_not_called()
        stale_sender.client.execute.assert_not_called()
        observed.append(attempt_id)
        raise DrainPaused()

    monkeypatch.setattr(erasure, "drain_submissions", observe_drain)
    with pytest.raises(DrainPaused):
        erasure._redact(Mock(), job, uuid4())
    assert len(observed) == 1
    reopened = DerivedMetricRun.objects.get(pk=old_run.id)
    # The reopen keeps the durable ownership marker the capture paths respect.
    assert reopened.status == "running" and reopened.completed_at is not None
    assert snapshots.redaction_owned(reopened)


def test_redaction_never_reopens_a_live_capture_attempt(
    snapshot_metric, user, monkeypatch
):
    """A capture attempt's namespace is not a completed snapshot: fail closed."""
    from uuid import uuid4

    from tracer.models.derived_metric import DerivedMetricRun, MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent

    attempt = running_capture(snapshot_metric, user)
    assert attempt.completed_at is None
    assert not snapshots.redaction_owned(attempt)
    job = _intent(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope="run",
        run_key=attempt.id,
        snapshot_key=attempt.snapshot_id,
        store_payload={"protocol": "metric_snapshot_redaction"},
    )
    drained = []
    monkeypatch.setattr(erasure, "drain_submissions", lambda *a, **k: drained.append(a))
    store = Mock()
    with pytest.raises(MetricStateConflict, match="namespace changed"):
        erasure._redact(store, job, uuid4())
    assert drained == []
    store.execute.assert_not_called()
    store.client.execute.assert_not_called()
    current = DerivedMetricRun.objects.get(pk=attempt.id)
    assert (current.status, current.attempt_id, current.snapshot_id) == (
        "running",
        attempt.attempt_id,
        attempt.snapshot_id,
    )
    assert current.completed_at is None


@pytest.mark.parametrize("child", [False, True])
def test_reopened_legacy_run_needs_a_redaction_child_to_be_protected(
    snapshot_metric, user, monkeypatch, child
):
    """Recompute also leaves a legacy raw run running with completed_at set.

    Without an unfinished redaction child that run is NOT redaction-owned: an
    erasure parent takes the existing failed-run path instead of waiting on it
    forever. With one (a redaction that failed after its reopen) the run is
    protected and the parent waits for that child.
    """
    from uuid import uuid4

    from django.utils import timezone

    from tracer.models.derived_metric import DerivedMetricRun, MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent

    snapshot = running_capture(snapshot_metric, user)
    # A legacy raw run that completed yesterday and is now being recomputed.
    legacy = DerivedMetricRun.objects.create(
        definition=snapshot.definition,
        kind="run",
        status="running",
        attempt_id=uuid4(),
        snapshot_id=uuid4(),
        window_start=snapshot.window_start,
        window_end=snapshot.window_end,
        as_of=snapshot.as_of,
        fingerprint=uuid4().hex + uuid4().hex,
        completed_at=timezone.now() - timedelta(days=1),
        lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    DerivedMetricRun.objects.filter(pk=snapshot.id).delete()
    if child:
        other = _intent(
            organization_id=snapshot_metric.organization_id,
            project_id=snapshot_metric.project_id,
            scope="run",
            run_key=legacy.id,
            snapshot_key=legacy.snapshot_id,
            deletion_key=f"redact:{uuid4()}:{legacy.id}",
            store_payload={"protocol": "metric_snapshot_redaction"},
        )
        MetricErasureRequest.objects.filter(pk=other.id).update(status="failed")
    assert snapshots.redaction_owned(legacy) is child
    parent = erasure.erase_metric_data(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    driven = []
    monkeypatch.setattr(
        erasure, "process_erasure", lambda request_id, **k: driven.append(request_id)
    )
    with pytest.raises(MetricStateConflict, match="pending"):
        erasure._redact_subjects(Mock(), parent)
    legacy.refresh_from_db()
    failed = MetricErasureRequest.objects.filter(
        deletion_key=f"failed-capture:{legacy.id}"
    )
    redactions = set(
        MetricErasureRequest.objects.filter(
            store_payload__protocol="metric_snapshot_redaction"
        ).values_list("id", flat=True)
    )
    if child:
        # Protected: never failed or purged; the parent drives the child.
        assert legacy.status == "running" and not failed.exists()
        assert driven == [other.id]
    else:
        # Not a redaction's reopen: the existing failed-run path, no waiting.
        assert legacy.status == "failed" and failed.exists()
        assert failed.get().id in driven
        assert not redactions & set(driven)


def test_parent_drives_at_most_ten_foreign_redaction_children_per_pass(
    snapshot_metric, user, monkeypatch
):
    """Reopened runs never fail or purge, and one pass stays bounded."""
    from uuid import uuid4

    from django.utils import timezone

    from tracer.models.derived_metric import DerivedMetricRun, MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent

    template = running_capture(snapshot_metric, user)
    runs, children = [], set()
    for _ in range(12):
        run = DerivedMetricRun.objects.create(
            definition=template.definition,
            kind="snapshot",
            supported_grain="conversation",
            status="running",
            attempt_id=uuid4(),
            snapshot_id=uuid4(),
            window_start=template.window_start,
            window_end=template.window_end,
            as_of=template.as_of,
            fingerprint=uuid4().hex + uuid4().hex,
            completed_at=timezone.now() - timedelta(hours=1),
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        child = _intent(
            organization_id=snapshot_metric.organization_id,
            project_id=snapshot_metric.project_id,
            scope="run",
            run_key=run.id,
            snapshot_key=run.snapshot_id,
            deletion_key=f"redact:{uuid4()}:{run.id}",
            store_payload={"protocol": "metric_snapshot_redaction"},
        )
        MetricErasureRequest.objects.filter(pk=child.id).update(status="failed")
        runs.append(run.id)
        children.add(child.id)
    DerivedMetricRun.objects.filter(pk=template.id).delete()
    parent = erasure.erase_metric_data(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    driven = []
    monkeypatch.setattr(
        erasure, "process_erasure", lambda request_id, **k: driven.append(request_id)
    )
    with pytest.raises(MetricStateConflict, match="redaction is pending"):
        erasure._redact_subjects(Mock(), parent)
    assert len(driven) == 10 and set(driven) <= children
    assert (
        not DerivedMetricRun.objects.filter(pk__in=runs)
        .exclude(status="running")
        .exists()
    )
    assert not MetricErasureRequest.objects.filter(
        deletion_key__startswith="failed-capture:"
    ).exists()
