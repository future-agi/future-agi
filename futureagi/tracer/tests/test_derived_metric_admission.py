"""Fake-clock scheduling on real PG, including independent connection races.

These small correctness fixtures do not certify M2/M3/M11 fleet performance.
"""

import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.models.project import Project
from tracer.services.derived_metrics import admission
from tracer.services.derived_metrics.lease import ProjectLease

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def enabled(settings):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1


@pytest.fixture
def state_factory(workspace, observe_project):
    def create(**overrides):
        project = Project.objects.create(
            name=f"dispatch-{uuid4().hex}",
            workspace=workspace,
            organization=workspace.organization,
            model_type=observe_project.model_type,
            trace_type=observe_project.trace_type,
        )
        return MetricProjectRefresh.objects.create(
            project=project, created_at=NOW - timedelta(days=1), **overrides
        )

    return create


def quiet_state(state_factory, **overrides):
    return state_factory(
        incremental_last_success_at=NOW, last_success_at=NOW, **overrides
    )


def release_all(reservations, now=NOW):
    for reservation in reservations:
        admission.release(reservation, now=now)


def test_missing_or_off_flag_does_not_enroll_projects(settings):
    if hasattr(settings, "METRICS_ENABLED"):
        del settings.METRICS_ENABLED
    for value in (None, 0, "1"):
        if value is not None:
            settings.METRICS_ENABLED = value
        assert admission.reserve_projects(clock=lambda: NOW) == []
        assert admission.dispatch(Mock(), clock=lambda: NOW) == 0
        assert not admission.request_refresh(uuid4(), clock=lambda: NOW)


def test_backend_is_real_postgres():
    assert connection.vendor == "postgresql"


@pytest.mark.parametrize(
    "flag", ["pending_backfill", "pending_repair", "pending_expiry", "pending_erasure"]
)
def test_pending_bypasses_recent_success(enabled, state_factory, flag):
    state = quiet_state(state_factory, **{flag: True})
    reservations = admission.reserve_projects(clock=lambda: NOW)
    assert [r.grant.project_id for r in reservations] == [state.project_id]
    release_all(reservations)
    assert (
        len(admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=60))) == 1
    )


def test_incremental_staleness_is_independent_of_backfill_success(
    enabled, state_factory
):
    state = state_factory(
        last_success_at=NOW,
        incremental_last_success_at=NOW - timedelta(seconds=900),
    )
    assert (
        admission.reserve_projects(clock=lambda: NOW)[0].grant.project_id
        == state.project_id
    )


def test_fresh_idle_project_is_not_scheduled(enabled, state_factory):
    quiet_state(state_factory)
    assert admission.reserve_projects(clock=lambda: NOW) == []


def test_due_settle_ignores_recent_success(enabled, state_factory):
    state = quiet_state(state_factory, next_due_at=NOW - timedelta(days=5))
    assert (
        admission.reserve_projects(clock=lambda: NOW)[0].grant.project_id
        == state.project_id
    )


def test_recovery_without_pending_flags(enabled, state_factory):
    state = quiet_state(state_factory)
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="old", ttl=timedelta(minutes=1), now=NOW
    )
    MetricRefreshAttempt.prepare(
        state.project_id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        purpose="incremental",
        source_ceilings={},
        metric_versions=[],
        now=NOW,
    )
    assert admission.reserve_projects(clock=lambda: NOW) == []
    recovered = admission.reserve_projects(clock=lambda: NOW + timedelta(minutes=1))[0]
    assert recovered.grant.fencing_epoch > grant.fencing_epoch


def test_k1_fairness_for_pending_and_incremental_projects(enabled, state_factory):
    states = [
        quiet_state(state_factory, pending_backfill=True),
        state_factory(),
        quiet_state(state_factory, pending_repair=True),
    ]
    seen = []
    for tick in range(12):
        now = NOW + timedelta(seconds=60 * tick)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        seen.append(reservation.grant.project_id)
        # Simulate a recent successful chunk, NOT completion of the backlog.
        MetricProjectRefresh.objects.filter(
            project_id=reservation.grant.project_id
        ).update(last_success_at=now)
        release_all([reservation], now)
    for start in range(0, 12, 3):
        assert set(seen[start : start + 3]) == {s.project_id for s in states}


def _dispatch_turns(dispatches, start, *, fail=()):
    """Run ``dispatches`` one-slot dispatches, a second apart; return who ran.

    Every admitted pass "runs" for that second and is released. A project in
    ``fail`` fails each of its passes, as record_failure records it.
    """
    order = []
    for step in range(dispatches):
        now = start + timedelta(seconds=step)
        reservations = admission.reserve_projects(clock=lambda now=now: now)
        for reservation in reservations:
            order.append(reservation.grant.project_id)
            if reservation.grant.project_id in fail:
                admission.record_failure(
                    reservation, now=now, code="metric_refresh_failed"
                )
        release_all(reservations, now)
    return order


def test_a_once_failed_project_gets_a_turn_beside_always_busy_ones(
    enabled, state_factory
):
    """10M: with the one slot always busy, ordering healthy projects first
    starved a project that failed once: its retry was due, but always-busy
    projects took every turn. Among eligible projects the least recently
    admitted goes first, so it runs within as many dispatches as there are
    eligible projects once its retry is due."""
    busy = [
        quiet_state(
            state_factory,
            pending_repair=True,
            last_started_at=NOW - timedelta(seconds=30 - index),
        )
        for index in range(3)
    ]
    once_failed = state_factory(
        pending_repair=True,
        consecutive_failures=1,
        last_error_at=NOW - timedelta(seconds=30),
        last_started_at=NOW - timedelta(seconds=40),
    )
    # Not eligible before its retry is due: the busy projects share the slot.
    early = _dispatch_turns(6, NOW)
    assert once_failed.project_id not in early
    assert set(early) == {state.project_id for state in busy}
    # Retry due (base backoff 60 s after the failure): within 4 dispatches.
    due = NOW + timedelta(seconds=admission.BACKOFF_BASE_SECONDS)
    assert once_failed.project_id in _dispatch_turns(4, due)


def test_a_permanently_failing_project_waits_out_its_backoff(enabled, state_factory):
    """Dropping healthy-first does not let a failing project take more than its
    share: after each failure it is ineligible until its retry is due, and the
    backoff doubles, so it runs once per backoff while the others keep turns."""
    busy = [
        quiet_state(
            state_factory,
            pending_repair=True,
            last_started_at=NOW - timedelta(seconds=30 - index),
        )
        for index in range(3)
    ]
    failing = state_factory(
        pending_repair=True, last_started_at=NOW - timedelta(seconds=60)
    )
    order = _dispatch_turns(300, NOW, fail={failing.project_id})
    turns = [
        index for index, project in enumerate(order) if project == failing.project_id
    ]
    # 300 one-second dispatches: it runs first (oldest), then at most once per
    # backoff (60, 120, then 240 s), never in two consecutive dispatches.
    assert turns[0] == 0
    assert len(turns) <= 4, turns
    assert all(
        b - a >= admission.BACKOFF_BASE_SECONDS
        for a, b in zip(turns, turns[1:], strict=False)
    ), turns
    for state in busy:
        assert order.count(state.project_id) >= (300 - len(turns)) // 3 - 1


def test_a_too_large_project_runs_only_expiry_until_the_limit_admits_it(
    enabled, settings, state_factory
):
    from tracer.services.derived_metrics import size_cap

    settings.METRICS_MAX_CHECKPOINT_ROWS = 1_000
    paused = state_factory(pending_repair=True, pending_backfill=True)
    assert not size_cap.pause_if_too_large(paused.project_id, 1_000, now=NOW)
    assert size_cap.pause_if_too_large(paused.project_id, 1_001, now=NOW)
    paused.refresh_from_db()
    assert (paused.paused_reason, paused.paused_at, paused.paused_rows) == (
        "too_large",
        NOW,
        1_001,
    )
    # Paused with every lane due: only an expiry pass, once per expiry
    # interval (privacy work never pauses), and nothing new is calculated.
    MetricProjectRefresh.objects.filter(pk=paused.pk).update(
        last_started_at=NOW - timedelta(minutes=5)
    )
    assert admission.reserve_projects(clock=lambda: NOW) == []
    due = NOW + timedelta(days=1)
    assert admission.reserve_projects(clock=lambda: due) == []
    due += admission.PAUSED_EXPIRY_SLACK
    (expiry,) = admission.reserve_projects(clock=lambda: due)
    assert (expiry.grant.project_id, expiry.purpose) == (paused.project_id, "expiry")
    release_all([expiry], due)
    assert not MetricRefreshAttempt.objects.filter(project_id=paused.project_id)
    # A raise short of the measured rows keeps it paused.
    settings.METRICS_MAX_CHECKPOINT_ROWS = 1_000
    later = due + timedelta(minutes=1)
    assert admission.reserve_projects(clock=lambda: later) == []
    # Raising the limit to the measured rows resumes it at the next tick.
    settings.METRICS_MAX_CHECKPOINT_ROWS = 1_001
    (reservation,) = admission.reserve_projects(clock=lambda: later)
    assert reservation.grant.project_id == paused.project_id
    assert reservation.purpose != "expiry"
    paused.refresh_from_db()
    assert (paused.paused_reason, paused.paused_at, paused.paused_rows) == (
        "",
        None,
        None,
    )
    release_all([reservation], later)


def test_failed_expiry_on_a_headless_project_keeps_its_interval(
    enabled, settings, state_factory
):
    from tracer.services.derived_metrics import size_cap

    settings.METRICS_MAX_CHECKPOINT_ROWS = 1_000
    paused = state_factory()
    assert size_cap.pause_if_too_large(paused.project_id, 1_001, now=NOW)
    due = NOW + timedelta(minutes=1)
    failing = Mock(side_effect=RuntimeError("synthetic queue failure"))
    assert admission.dispatch(failing, clock=lambda: due) == 0
    failing.assert_called_once()
    paused.refresh_from_db()
    assert paused.consecutive_failures == 1 and not paused.pending_expiry
    # A headless project has no expiry debt to clear the flag: the failure must
    # not turn it into an expiry pass on every dispatch poll.
    retry = due + timedelta(seconds=admission.BACKOFF_BASE_SECONDS)
    assert admission.reserve_projects(clock=lambda: retry) == []


def test_head_rows_count_every_receipt_the_head_reads(
    enabled, workspace, observe_project
):
    from tracer.models.derived_metric import (
        MetricAttemptBatch,
        MetricCompactionRange,
    )
    from tracer.services.derived_metrics import size_cap

    assert size_cap.head_rows(None) == 0
    allowed = [uuid4(), uuid4()]
    head = SimpleNamespace(allowed_attempts=lambda: [(1, allowed[0]), (2, allowed[1])])
    created = []
    for attempt_id, generation in zip(allowed + [uuid4()], (1, 2, 3), strict=True):
        created.append(
            MetricRefreshAttempt.objects.create(
                id=attempt_id,
                project=observe_project,
                generation=generation,
                fencing_epoch=1,
                lease_token=uuid4(),
                purpose="compaction" if generation == 1 else "incremental",
                prepared_sha256="0" * 64,
                created_at=NOW,
                expires_at=NOW + timedelta(days=7),
            )
        )
    checkpoint, delta, unread = created
    MetricCompactionRange.objects.bulk_create(
        MetricCompactionRange(
            attempt=checkpoint,
            table_kind="field_values",
            bucket=bucket,
            ordinal=0,
            source_sha256="0" * 64,
            schema_sha256="0" * 64,
            algorithm="rowbinary-sha256-merkle-v1",
            shard_identity=["s", "db"],
            row_count=rows,
            sequence_first=first,
            sequence_last=first + rows - 1,
        )
        for bucket, rows, first in ((0, 700, 1), (1, 250, 701))
    )
    for attempt, rows in ((delta, 40), (unread, 9_999)):
        MetricAttemptBatch.objects.create(
            attempt=attempt,
            table_name="field_values",
            batch_no=0,
            row_count=rows,
            payload_sha256="0" * 64,
        )
    # Only the head's allowlist counts: ranges (checkpoint) plus batches (delta).
    assert size_cap.head_rows(head) == 990


def test_max_checkpoint_rows_loads_bounded_from_the_environment(settings):
    from django.core.exceptions import ImproperlyConfigured

    from tfc.settings.runtime_setting_specs import (
        RUNTIME_NUMERIC_SETTING_SPECS,
        load_numeric_settings,
    )
    from tracer.services.derived_metrics import size_cap

    name = "METRICS_MAX_CHECKPOINT_ROWS"
    assert load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})[name] == (
        size_cap.DEFAULT_MAX_CHECKPOINT_ROWS
    )
    with pytest.raises(ValueError):
        load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={name: "0"})
    if hasattr(settings, name):
        delattr(settings, name)
    assert size_cap.max_checkpoint_rows() == size_cap.DEFAULT_MAX_CHECKPOINT_ROWS
    for invalid in (0, 10_000_000_001, "10"):
        setattr(settings, name, invalid)
        with pytest.raises(ImproperlyConfigured):
            size_cap.max_checkpoint_rows()


def test_a_tick_refills_a_freed_slot_instead_of_waiting_for_the_next(
    enabled, state_factory, monkeypatch
):
    """10M: dispatch reserved one project per 60 s tick, so the single slot was
    busy 24% of the time (a ~12 s pass, then a wait). One tick now refills the
    slot as soon as a pass frees it, within the same K, fair order and lock."""
    first = quiet_state(
        state_factory, pending_repair=True, last_started_at=NOW - timedelta(hours=2)
    )
    second = quiet_state(
        state_factory, pending_repair=True, last_started_at=NOW - timedelta(hours=1)
    )
    elapsed, enqueued = [0.0], []
    monkeypatch.setattr(admission, "monotonic", lambda: elapsed[0])

    def sleep(seconds):
        elapsed[0] += seconds
        if len(enqueued) == 1:
            # The first project's pass finishes during the tick.
            admission.release(admission.Reservation.from_payload(enqueued[0]), now=NOW)

    count = admission.dispatch_until(
        enqueued.append, clock=lambda: NOW, seconds=10, poll=2, sleep=sleep
    )
    assert count == 2
    assert [p["project_id"] for p in enqueued] == [
        str(first.project_id),
        str(second.project_id),
    ]


def test_ninety_chunks_continue_after_recent_success(enabled, state_factory):
    state = quiet_state(state_factory, pending_backfill=True)
    for tick in range(90):
        now = NOW + timedelta(seconds=60 * tick)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        assert reservation.grant.project_id == state.project_id
        MetricProjectRefresh.objects.filter(pk=state.pk).update(last_success_at=now)
        release_all([reservation], now)
    state.refresh_from_db()
    assert state.pending_backfill
    assert state.fencing_epoch == 90


def test_per_project_budgets_rotate(enabled, state_factory):
    state = state_factory(pending_backfill=True, pending_repair=True)
    purposes = []
    for tick in range(9):
        now = NOW + timedelta(seconds=tick * 60)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        purposes.append(reservation.purpose)
        release_all([reservation], now)
    assert purposes == ["incremental", "backfill", "repair"] * 3
    state.refresh_from_db()
    assert state.pending_backfill and state.pending_repair


def test_parallel_dispatch_obeys_k_and_excludes_duplicates(
    enabled, settings, state_factory
):
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 2
    for _ in range(4):
        state_factory()
    barrier = Barrier(2)

    def reserve():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return admission.reserve_projects(clock=lambda: NOW)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    reservations = [r for group in results for r in group]
    assert len(reservations) == 2
    assert len({r.grant.project_id for r in reservations}) == 2
    assert admission.reserve_projects(clock=lambda: NOW) == []


def test_run_now_throttle_and_pending_coalescing(enabled, state_factory):
    state = quiet_state(state_factory)
    assert admission.request_refresh(state.project_id, clock=lambda: NOW)
    assert not admission.request_refresh(
        state.project_id, clock=lambda: NOW + timedelta(minutes=2)
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=Mock()
    )
    assert not admission.request_refresh(
        state.project_id, clock=lambda: NOW + timedelta(seconds=59)
    )
    assert admission.request_refresh(
        state.project_id, clock=lambda: NOW + timedelta(seconds=60)
    )


def test_request_arriving_during_pass_is_not_acknowledged(enabled, state_factory):
    state = state_factory()
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)

    def run(lease, purpose):
        assert admission.request_refresh(
            state.project_id, clock=lambda: NOW + timedelta(seconds=61)
        )

    assert admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=run
    )
    state.refresh_from_db()
    assert state.refresh_requested_at == NOW + timedelta(seconds=61)


def test_worker_duplicate_and_expired_tokens_do_not_run(enabled, state_factory):
    state_factory()
    (old,) = admission.reserve_projects(clock=lambda: NOW)
    later = NOW + timedelta(minutes=5)
    (new,) = admission.reserve_projects(clock=lambda: later)
    runner = Mock()
    assert not admission.execute_reservation(
        old.payload(), clock=lambda: later, runner=runner
    )
    runner.assert_not_called()
    assert admission.execute_reservation(
        new.payload(), clock=lambda: later, runner=runner
    )
    assert not admission.execute_reservation(
        new.payload(), clock=lambda: later, runner=runner
    )
    assert runner.call_count == 1


def test_global_execution_slot_survives_lease_expiry(enabled, state_factory):
    a, b = state_factory(), state_factory()
    with admission.execution_slot(a.project_id) as held:
        assert held
        with admission.execution_slot(b.project_id) as blocked:
            assert not blocked
    with admission.execution_slot(b.project_id) as free:
        assert free


def test_same_project_excluded_even_with_two_global_slots(
    enabled, settings, state_factory
):
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 2
    state = state_factory()
    with admission.execution_slot(state.project_id) as held:
        assert held
        with admission.execution_slot(state.project_id) as blocked:
            assert not blocked


def test_expired_worker_cannot_overlap_replacement(enabled, settings, state_factory):
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 2
    state = state_factory()
    (old,) = admission.reserve_projects(clock=lambda: NOW)
    runner = Mock()
    with admission.execution_slot(state.project_id) as held:
        assert held
        (new,) = admission.reserve_projects(clock=lambda: NOW + timedelta(minutes=6))
        assert new.grant.fencing_epoch > old.grant.fencing_epoch
        assert not admission.execute_reservation(
            new.payload(), clock=lambda: NOW + timedelta(minutes=6), runner=runner
        )
        runner.assert_not_called()
    (retry,) = admission.reserve_projects(clock=lambda: NOW + timedelta(minutes=7))
    assert retry.grant.fencing_epoch > new.grant.fencing_epoch
    assert admission.execute_reservation(
        retry.payload(), clock=lambda: NOW + timedelta(minutes=7), runner=runner
    )


def test_worker_off_releases_reservation_without_work(enabled, settings, state_factory):
    state = state_factory(pending_backfill=True)
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    settings.METRICS_ENABLED = 0
    runner = Mock()
    assert not admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=runner
    )
    runner.assert_not_called()
    state.refresh_from_db()
    assert state.pending_backfill and state.lease_token is None


def test_retry_preserves_flags_cursors_and_request(enabled, state_factory):
    state = state_factory(
        pending_backfill=True,
        pending_repair=True,
        pending_expiry=True,
        refresh_requested_at=NOW,
        reconciliation_cursor={"phase": "spans"},
        watermark_ns=123,
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        admission.execute_reservation(
            reservation.payload(),
            clock=lambda: NOW,
            runner=Mock(side_effect=RuntimeError("synthetic failure")),
        )
    state.refresh_from_db()
    assert state.pending_backfill and state.pending_repair and state.pending_expiry
    assert state.reconciliation_cursor == {"phase": "spans"}
    assert state.watermark_ns == 123 and state.refresh_requested_at == NOW
    assert state.last_error_code == "metric_refresh_failed"
    assert state.consecutive_failures == 1 and state.lease_token is None
    assert admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=59)) == []
    assert (
        len(admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=60))) == 1
    )


def test_enqueue_failure_does_not_drop_pending_work(enabled, state_factory):
    state = state_factory(pending_backfill=True)
    assert (
        admission.dispatch(
            Mock(side_effect=RuntimeError("synthetic enqueue failure")),
            clock=lambda: NOW,
        )
        == 0
    )
    state.refresh_from_db()
    assert state.pending_backfill and state.lease_token is None
    assert state.last_error_code == "metric_dispatch_failed"


def test_ambiguous_enqueue_does_not_release_running_writer(enabled, state_factory):
    state = state_factory()

    def enqueue(payload):
        MetricProjectRefresh.objects.filter(pk=state.pk).update(
            lease_holder=f"metrics:running:{payload['purpose']}"
        )
        raise RuntimeError("synthetic lost response")

    assert admission.dispatch(enqueue, clock=lambda: NOW) == 0
    state.refresh_from_db()
    assert state.lease_token is not None and state.consecutive_failures == 0


@pytest.mark.parametrize("value", [0, -1, "2", True])
def test_invalid_k_fails_closed(enabled, settings, value):
    from django.core.exceptions import ImproperlyConfigured

    settings.METRICS_MAX_CONCURRENT_PROJECTS = value
    with pytest.raises(ImproperlyConfigured):
        admission.reserve_projects(clock=lambda: NOW)


def test_temporal_schedule_and_existing_queues(settings, monkeypatch):
    from tfc.temporal.common import registry
    from tfc.temporal.schedules import tracer as schedules
    from tracer.tasks import derived_metric as tasks

    assert "tracer.tasks" in registry.TEMPORAL_ACTIVITY_MODULES
    try:
        for flag in (0, 1):
            settings.METRICS_ENABLED = flag
            entries = importlib.reload(schedules).TRACER_SCHEDULES
            metric = [s for s in entries if s.schedule_id == "dispatch-derived-metrics"]
            assert len(metric) == 1
            if flag:
                assert metric[0].interval_seconds == 60
                assert (
                    metric[0].queue
                    == tasks.dispatch_derived_metrics._metadata["queue"]
                    == "tasks_s"
                )
                assert (
                    metric[0].activity_name
                    == tasks.dispatch_derived_metrics._activity_name
                )
        assert tasks.refresh_derived_metric_project._metadata["queue"] == "tasks_l"
        assert tasks.refresh_derived_metric_project._metadata["max_retries"] == 0
        enqueue = Mock()
        monkeypatch.setattr(
            tasks.refresh_derived_metric_project, "apply_async", enqueue
        )
        tasks._enqueue_refresh({"project_id": "p", "epoch": 7})
        assert enqueue.call_args.kwargs["task_id"] == "metric-refresh-p-7"
        settings.METRICS_ENABLED = 0
        legacy = Mock()
        monkeypatch.setattr(tasks, "execute_run", legacy)
        tasks.compute_derived_metric._original_func("synthetic-run")
        legacy.assert_not_called()
    finally:
        settings.METRICS_ENABLED = 0
        importlib.reload(schedules)


def test_native_adapter_preserves_query_budgets():
    client = Mock()
    client.execute.return_value = ([(1,)], [("count", "UInt64")])
    client.last_query.progress = SimpleNamespace(rows=123, bytes=456)
    result = admission.NativeMetricAnalytics(client).execute_ch_query(
        "SELECT 1",
        {},
        timeout_ms=2000,
        settings={"max_memory_usage": 512 * 1024 * 1024, "max_rows_to_read": 999},
    )
    assert result.data == [{"count": 1}]
    assert (result.read_rows, result.read_bytes) == (123, 456)
    assert client.execute.call_args.kwargs["settings"] == {
        "max_memory_usage": 512 * 1024 * 1024,
        "max_rows_to_read": 999,
        "max_execution_time": 2,
    }


def test_incremental_merges_due_units_and_advances_only_its_clock(
    enabled, state_factory, monkeypatch
):
    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.queries import Scope

    state = state_factory(next_due_at=NOW - timedelta(days=10))
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    lease = ProjectLease(reservation.grant, clock=lambda: NOW)
    empty = changes.ChangeBatch((), (), (), (), (), {})
    monkeypatch.setattr(backfill, "working_definitions", lambda project: [])
    monkeypatch.setattr(backfill, "incremental_changes", lambda *a, **kw: empty)
    writer = Mock(lease=lease)
    writer.store.execute.side_effect = [[("conversation", "overdue")], [(None,)]]
    writer.refresh.return_value = None
    admission.run_incremental(
        writer,
        object(),
        Scope(
            state.project.organization_id, state.project.workspace_id, state.project_id
        ),
        state,
    )
    batch = writer.refresh.call_args.args[0]
    assert batch.units == (changes.UnitKey("conversation", "overdue"),)
    assert batch.cursor_updates == {"incremental_last_success_at": NOW}
    assert writer.refresh.call_args.kwargs["settle_minutes"] == state.settle_minutes
    state.refresh_from_db()
    assert state.next_due_at is None
    assert (
        state.incremental_last_success_at is None
    )  # writer publishes it, never the dispatcher


def test_dispatcher_advances_real_pg_backfill_publications(
    enabled, state_factory, workspace, monkeypatch
):
    """Real lease/ledger/activation with B09's bounded CH planner double."""
    from types import MethodType

    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_backfill import (
        EmptyPolicyRecorder,
        empty_refresh,
        install_empty_planners,
        make_version,
    )

    days = install_empty_planners(monkeypatch)
    states = [state_factory(), state_factory()]
    drafts = [make_version(state.project, workspace) for state in states]
    now = [NOW]
    seen = {state.project_id: 0 for state in states}

    def run(lease, purpose):
        seen[lease.project_id] += 1
        writer = RefreshWriter(
            lease,
            EmptyPolicyRecorder(),
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
            retention_days=lambda: 0,
        )
        writer.refresh = MethodType(empty_refresh, writer)
        scope = Scope(workspace.organization_id, workspace.id, lease.project_id)
        if purpose == "backfill":
            backfill.BackfillRunner(writer, object(), scope).run_pass(max_chunks=1)
        else:
            writer.refresh(
                changes.ChangeBatch(
                    (), (), (), (), (), {"incremental_last_success_at": now[0]}
                ),
                backfill.working_definitions(lease.project_id),
                scope,
                start=now[0] - timedelta(days=90),
                end=now[0],
                as_of=now[0],
                purpose="incremental",
                publication=None,
            )

    for tick in range(600):
        now[0] = NOW + timedelta(seconds=60 * tick)
        reservations = admission.reserve_projects(clock=lambda: now[0])
        assert len(reservations) <= 1
        for reservation in reservations:
            assert admission.execute_reservation(
                reservation.payload(), clock=lambda: now[0], runner=run
            )
        if not MetricProjectRefresh.objects.filter(pending_backfill=True).exists():
            break
    else:
        pytest.fail("unfinished chunks were deferred behind the idle cadence")
    assert all(count > 90 for count in seen.values())
    assert abs(seen[states[0].project_id] - seen[states[1].project_id]) <= 2
    for draft in drafts:
        draft.refresh_from_db()
        draft.metric.refresh_from_db()
        assert draft.backfill_days_done == draft.backfill_days_total == 90
        assert draft.metric.active_version_id == draft.id
        assert (
            len([day for project, day in days if project == draft.metric.project_id])
            == 90
        )


def test_worker_registry_loads_both_existing_generic_queues():
    from temporalio import activity

    from tfc.temporal.common.registry import (
        get_activities_for_queue,
        get_workflows_for_queue,
    )
    from tfc.temporal.drop_in import TaskRunnerWorkflow

    expected = {
        "tracer.dispatch_derived_metrics",
        "tracer.refresh_derived_metric_project",
    }
    for queue in ("tasks_s", "tasks_l"):
        registered = {
            activity._Definition.must_from_callable(a).name
            for a in get_activities_for_queue(queue)
        }
        assert expected <= registered
        assert TaskRunnerWorkflow in get_workflows_for_queue(queue)
    exact = {
        activity._Definition.must_from_callable(a).name
        for a in get_activities_for_queue("exact_aggregation")
    }
    assert not expected & exact


def test_running_duplicate_cannot_release_original_lease(enabled, state_factory):
    state_factory()
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)

    def run(lease, purpose):
        assert not admission.execute_reservation(
            reservation.payload(), clock=lambda: NOW, runner=Mock()
        )
        lease.check()

    assert admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=run
    )


def test_backoff_doubles_and_caps_without_mutating_cursor(enabled, state_factory):
    state = state_factory(pending_repair=True, watermark_ns=123)
    for failures, seconds in [
        (1, 60),
        (2, 120),
        (3, 240),
        (4, 480),
        (5, 900),
        (100, 900),
    ]:
        MetricProjectRefresh.objects.filter(pk=state.pk).update(
            last_error_at=NOW, consecutive_failures=failures
        )
        assert not admission.eligible_projects(
            NOW + timedelta(seconds=seconds - 1)
        ).exists()
        assert admission.eligible_projects(NOW + timedelta(seconds=seconds)).exists()
    state.refresh_from_db()
    assert state.watermark_ns == 123 and state.pending_repair


def test_missing_policy_provider_fails_closed_without_consuming_work(
    enabled, settings, state_factory
):
    from django.core.exceptions import ImproperlyConfigured

    settings.METRICS_RETENTION_DAYS_PROVIDER = ""
    state = state_factory(pending_backfill=True)
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    with pytest.raises(ImproperlyConfigured, match="retention provider"):
        admission.execute_reservation(reservation.payload(), clock=lambda: NOW)
    state.refresh_from_db()
    assert state.pending_backfill and state.watermark_ns == 0
    assert state.last_error_code == "metric_refresh_failed"
    assert state.lease_token is None


@pytest.mark.parametrize("purpose", ["incremental", "backfill", "repair"])
def test_scheduled_writer_binds_project_settle_policy(purpose, enabled, monkeypatch):
    from tracer.services.derived_metrics.refresh import RefreshWriter

    delegate = Mock(return_value="published")
    monkeypatch.setattr(RefreshWriter, "refresh", delegate)
    writer = admission._ScheduledRefreshWriter(
        Mock(),
        Mock(),
        org_id=uuid4(),
        workspace_id=uuid4(),
        retention_days=lambda: 30,
        settle_minutes=47,
    )
    assert writer.refresh("batch", [], "scope", purpose=purpose) == "published"
    assert delegate.call_args.kwargs["settle_minutes"] == 47
    assert delegate.call_args.kwargs["purpose"] == purpose


def test_failure_after_recent_publication_remains_runnable(enabled, state_factory):
    state = quiet_state(state_factory, consecutive_failures=1, last_error_at=NOW)
    assert admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=59)) == []
    (reservation,) = admission.reserve_projects(
        clock=lambda: NOW + timedelta(seconds=60)
    )
    assert reservation.grant.project_id == state.project_id
    state.refresh_from_db()
    assert state.last_success_at == NOW


def test_overflow_preserves_published_repair_checkpoint(
    enabled, state_factory, workspace, monkeypatch
):
    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_backfill import make_version

    started = NOW - timedelta(hours=1)
    checkpoint = changes.ReconciliationCheckpoint(
        started_at=started,
        phase="dependencies",
        after=("synthetic-trace", "synthetic-span"),
        scanned=1000,
    ).to_dict()
    state = state_factory()
    make_version(state.project, workspace, active=True)
    # Deliberately keep the caller's state stale; the durable flag wins.
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        pending_repair=True,
        reconciliation_cursor=checkpoint,
        reconciliation_started_at=started,
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert reservation.purpose == "incremental"
    writer = SimpleNamespace(lease=ProjectLease(reservation.grant, clock=lambda: NOW))
    scope = Scope(workspace.organization_id, workspace.id, state.project_id)
    monkeypatch.setattr(
        backfill,
        "incremental_changes",
        Mock(side_effect=changes.arrivals.ContinuousCandidateOverflow("overflow")),
    )
    runner = Mock()
    monkeypatch.setattr(backfill, "BackfillRunner", Mock(return_value=runner))
    admission.run_incremental(writer, object(), scope, state)
    # The burst's pages (run_repair_pages loops run_repair_page).
    runner.run_repair_pages.assert_called_once_with(skip_incremental=True)
    state.refresh_from_db()
    assert state.reconciliation_cursor == checkpoint
    assert state.reconciliation_started_at == started
    assert state.pending_repair and state.watermark_ns == 0


def completed_repair(*, covered, completed):
    from tracer.services.derived_metrics.changes import ReconciliationCheckpoint

    return ReconciliationCheckpoint(
        started_at=covered,
        completed_at=completed,
        phase="complete",
        completed_passes=1,
        covered_through=covered,
    ).to_dict()


@pytest.mark.parametrize("completed_before", [False, True])
def test_initial_and_recurring_repair_are_durable_without_overflow(
    enabled, state_factory, workspace, completed_before
):
    from tracer.tests.test_derived_metric_backfill import make_version

    completed = NOW - timedelta(days=30) if completed_before else None
    checkpoint = (
        completed_repair(covered=completed - timedelta(hours=1), completed=completed)
        if completed_before
        else None
    )
    state = quiet_state(
        state_factory,
        reconciliation_completed_at=completed,
        reconciliation_cursor=checkpoint,
    )
    make_version(state.project, workspace, active=True)
    purposes = []
    for tick in range(3):
        now = NOW + timedelta(seconds=tick * 60)
        (reservation,) = admission.reserve_projects(clock=lambda now=now: now)
        purposes.append(reservation.purpose)
        # Neither incremental staleness nor an exceptional invalidation drives
        # this project. It stays eligible through the initial coverage gap.
        MetricProjectRefresh.objects.filter(pk=state.pk).update(
            incremental_last_success_at=now, last_success_at=now
        )
        admission.release(reservation, now=now)
    assert purposes == ["incremental", "repair", "repair"]
    state.refresh_from_db()
    assert state.pending_repair
    assert state.reconciliation_cursor == checkpoint
    assert state.reconciliation_completed_at == completed
    assert state.reconciliation_started_at is None  # dispatch never rewrites the CAS


@pytest.mark.parametrize("legacy", [False, True])
def test_repair_uses_configured_coverage_age_not_completion_time(
    enabled, settings, state_factory, workspace, legacy
):
    from tracer.tests.test_derived_metric_backfill import make_version

    settings.METRICS_RECONCILIATION_INTERVAL_SECONDS = 3600
    checkpoint = completed_repair(covered=NOW - timedelta(hours=1), completed=NOW)
    if legacy:
        checkpoint.pop("covered_through")
    state = quiet_state(
        state_factory,
        reconciliation_cursor=checkpoint,
        reconciliation_completed_at=NOW,
        # This row field is also a privacy invalidation token, not coverage.
        reconciliation_started_at=NOW,
        fencing_epoch=2,
    )
    make_version(state.project, workspace, active=True)
    assert not admission.eligible_projects(NOW - timedelta(microseconds=1)).exists()
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert reservation.purpose == "repair"
    state.refresh_from_db()
    assert state.pending_repair and state.reconciliation_cursor == checkpoint
    assert state.reconciliation_started_at == NOW


@pytest.mark.parametrize("invalid", [0, -1, "3600", True])
def test_invalid_repair_interval_fails_closed(enabled, settings, invalid):
    from django.core.exceptions import ImproperlyConfigured

    settings.METRICS_RECONCILIATION_INTERVAL_SECONDS = invalid
    with pytest.raises(ImproperlyConfigured, match="RECONCILIATION_INTERVAL"):
        admission.reserve_projects(clock=lambda: NOW)


@pytest.mark.parametrize("inactive", ["deleted", "revoked"])
def test_routine_repair_requires_a_live_definition(
    enabled, state_factory, workspace, inactive
):
    from tracer.tests.test_derived_metric_backfill import make_version

    state = quiet_state(state_factory)
    version = make_version(state.project, workspace, active=True)
    if inactive == "deleted":
        version.metric.deleted = True
        version.metric.save(update_fields=["deleted"])
    else:
        # Version.save rejects edits; lifecycle revocation uses a guarded update.
        version.metric.versions.filter(pk=version.pk).update(pins_revoked_at=NOW)
    assert admission.reserve_projects(clock=lambda: NOW) == []


def test_routine_repair_survives_enqueue_failure(enabled, state_factory, workspace):
    from tracer.tests.test_derived_metric_backfill import make_version

    state = quiet_state(state_factory, fencing_epoch=2)
    make_version(state.project, workspace, active=True)
    assert (
        admission.dispatch(Mock(side_effect=RuntimeError("enqueue")), clock=lambda: NOW)
        == 0
    )
    state.refresh_from_db()
    assert state.pending_repair and state.lease_token is None
    assert state.reconciliation_cursor is None and state.watermark_ns == 0
    assert state.last_error_code == "metric_dispatch_failed"
    assert admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=59)) == []
    assert (
        len(admission.reserve_projects(clock=lambda: NOW + timedelta(seconds=60))) == 1
    )


@pytest.mark.parametrize("overflow", [False, True])
def test_scheduler_completes_and_restarts_multi_page_repair(
    enabled, settings, state_factory, workspace, monkeypatch, overflow
):
    """Real PG leases, ledgers and CAS; only CH payload/page planning is fake."""
    from types import MethodType

    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_backfill import (
        EmptyPolicyRecorder,
        empty_refresh,
        make_version,
    )

    settings.METRICS_RECONCILIATION_INTERVAL_SECONDS = 3600
    state = quiet_state(state_factory)
    make_version(state.project, workspace, active=True)
    now = [NOW]
    seen = []
    purposes = []
    overflow_calls = []
    pages = 8  # More pages than fit between incremental turns.

    def page(analytics, *, now, checkpoint, page_size, **kwargs):
        assert page_size == backfill.PAGE_SIZE  # Keep the existing page cap.
        previous = checkpoint if checkpoint and checkpoint.phase != "complete" else None
        scanned = (previous.scanned if previous else 0) + page_size
        complete = scanned == pages * page_size
        started = previous.started_at if previous else now
        seen.append((started, scanned))
        cp = changes.ReconciliationCheckpoint(
            started_at=started,
            completed_at=now
            if complete
            else (checkpoint.completed_at if checkpoint else None),
            phase="complete" if complete else "dependencies",
            scanned=scanned,
            completed_passes=(checkpoint.completed_passes if checkpoint else 0)
            + int(complete),
            covered_through=started
            if complete
            else (checkpoint.covered_through if checkpoint else None),
        )
        return changes.ReconciliationPage(
            changes.ChangeBatch(
                (),
                (),
                (),
                (),
                (),
                {
                    "reconciliation_cursor": cp.to_dict(),
                    "reconciliation_started_at": cp.started_at,
                    "reconciliation_completed_at": cp.completed_at,
                    "pending_repair": not complete,
                },
            ),
            cp,
        )

    def incremental(*args, **kwargs):
        current = MetricProjectRefresh.objects.get(pk=state.pk)
        if overflow and current.watermark_ns == 0:
            overflow_calls.append(now[0])
            raise changes.arrivals.ContinuousCandidateOverflow("synthetic overflow")
        return changes.ChangeBatch((), (), (), (), (), {})

    monkeypatch.setattr(changes, "reconcile_page", page)
    monkeypatch.setattr(backfill, "incremental_changes", incremental)

    def run(lease, purpose):
        purposes.append(purpose)
        writer = RefreshWriter(
            lease,
            EmptyPolicyRecorder(),
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
            retention_days=lambda: 0,
        )
        writer.refresh = MethodType(empty_refresh, writer)
        scope = Scope(workspace.organization_id, workspace.id, state.project_id)
        if purpose == "repair":
            backfill.BackfillRunner(writer, object(), scope).run_repair_page()
        elif (
            overflow and MetricProjectRefresh.objects.get(pk=state.pk).watermark_ns == 0
        ):
            admission.run_incremental(writer, object(), scope, state)
        else:
            writer.refresh(
                changes.ChangeBatch(
                    (), (), (), (), (), {"incremental_last_success_at": now[0]}
                ),
                backfill.working_definitions(lease.project_id),
                scope,
                purpose="incremental",
                publication=None,
            )

    for tick in range(30):
        now[0] = NOW + timedelta(seconds=60 * tick)
        (reservation,) = admission.reserve_projects(clock=lambda: now[0])
        assert admission.execute_reservation(
            reservation.payload(), clock=lambda: now[0], runner=run
        )
        state.refresh_from_db()
        if state.reconciliation_completed_at:
            break
        assert state.watermark_ns == 0  # Never consume an unfinished sweep.
    else:
        pytest.fail("repair did not finish across repeated scheduler turns")
    assert [count for _, count in seen] == [1000 * n for n in range(1, pages + 1)]
    started = seen[0][0]
    assert {start for start, _ in seen} == {started}
    assert {"incremental", "repair"} <= set(purposes)
    assert not state.pending_repair
    assert state.reconciliation_cursor["covered_through"] == started.isoformat()
    if overflow:
        assert len(overflow_calls) >= pages
        for field in MetricProjectRefresh.MONOTONE_FIELDS:
            assert getattr(state, field) == changes.ns(started)
    else:
        assert not overflow_calls
    # Fresh incremental publication must not suppress the next safety sweep.
    now[0] = started + timedelta(hours=1)
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        incremental_last_success_at=now[0]
    )
    for _ in range(3):
        (reservation,) = admission.reserve_projects(clock=lambda: now[0])
        assert admission.execute_reservation(
            reservation.payload(), clock=lambda: now[0], runner=run
        )
        if reservation.purpose == "repair":
            break
    else:
        pytest.fail("completed repair never restarted")
    state.refresh_from_db()
    assert state.pending_repair
    assert state.reconciliation_cursor["scanned"] == backfill.PAGE_SIZE
    assert state.reconciliation_cursor["completed_passes"] == 1
    assert state.reconciliation_cursor["covered_through"] == started.isoformat()


def test_routine_reservation_reaches_native_repair_runner(
    enabled, settings, state_factory, workspace, monkeypatch
):
    from clickhouse_driver import Client

    from tracer.services.clickhouse import v2
    from tracer.services.derived_metrics import backfill, publication
    from tracer.tests.test_derived_metric_backfill import make_version

    settings.METRICS_RETENTION_DAYS_PROVIDER = "synthetic.policy"
    state = quiet_state(state_factory, fencing_epoch=2)
    make_version(state.project, workspace, active=True)
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    assert reservation.purpose == "repair"
    client = Mock(spec=Client)

    def count_sources(sql, params, **kwargs):
        # This dispatch-only fixture has no native source rows. Admission must
        # still perform the no-head size check, using its typed native result.
        assert "SELECT count() AS spans" in sql
        assert kwargs["with_column_types"] is True
        return [(0,)], [("spans", "UInt64")]

    client.execute.side_effect = count_sources
    client.last_query = SimpleNamespace(progress=SimpleNamespace(rows=0, bytes=0))
    monkeypatch.setattr("clickhouse_driver.Client", Mock(return_value=client))
    monkeypatch.setattr(
        v2,
        "get_v2_config",
        lambda: {
            "host": "unused",
            "tcp_port": 9000,
            "user": "unused",
            "password": "",
            "database": "unused",
        },
    )
    monkeypatch.setattr(admission, "import_string", lambda path: lambda org: 30)
    monkeypatch.setattr(publication, "ClickHousePublicationStore", Mock())
    writer = Mock()
    monkeypatch.setattr(admission, "_ScheduledRefreshWriter", Mock(return_value=writer))
    runner = Mock()
    monkeypatch.setattr(backfill, "BackfillRunner", Mock(return_value=runner))
    incremental = Mock()
    monkeypatch.setattr(admission, "run_incremental", incremental)
    assert admission.execute_reservation(reservation.payload(), clock=lambda: NOW)
    runner.run_repair_pages.assert_called_once_with()
    runner.run_pass.assert_not_called()
    incremental.assert_not_called()
    client.disconnect.assert_called_once_with()
    state.refresh_from_db()
    assert state.pending_repair  # Only the real page publication may clear it.
