"""Native PG admission, fencing and visibility races; no broker/CH send claims."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from simulate.models.agent_definition import AgentDefinition
from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.models.project import Project
from tracer.services.derived_metrics import admission
from tracer.tasks import derived_metric as tasks

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def agent(project):
    return AgentDefinition.objects.create(
        agent_name="Admission transition",
        description="Synthetic visibility race",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )


@pytest.fixture
def enabled(settings):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1


def change_visibility(project, agent, kind):
    values = (
        {"simulation_agent_id": agent.pk} if kind == "hidden" else {"deleted": True}
    )
    Project.no_workspace_objects.filter(pk=project.pk).update(**values)


def queued(project):
    now = timezone.now()
    state = MetricProjectRefresh.objects.create(
        project=project,
        pending_backfill=True,
        pending_repair=True,
        pending_expiry=True,
        pending_erasure=True,
        refresh_requested_at=now,
        watermark_ns=123,
        reconciliation_cursor={"phase": "spans"},
    )
    (reservation,) = admission.reserve_projects(clock=lambda: now)
    assert reservation.grant.project_id == project.pk
    return state, reservation


@pytest.mark.parametrize("flag", [0, 1])
@pytest.mark.parametrize("kind", ["hidden", "deleted", "missing"])
def test_run_now_rejects_without_creating_state(project, agent, settings, flag, kind):
    settings.METRICS_ENABLED = flag
    target = uuid4() if kind == "missing" else project.pk
    if kind != "missing":
        change_visibility(project, agent, kind)
    assert not admission.request_refresh(target)
    assert not MetricProjectRefresh.objects.filter(project_id=target).exists()


@pytest.mark.parametrize("flag", [0, 1])
@pytest.mark.parametrize("kind", ["hidden", "deleted"])
def test_queued_worker_reauthorizes_without_work(
    project, agent, enabled, settings, monkeypatch, flag, kind
):
    state, reservation = queued(project)
    change_visibility(project, agent, kind)
    settings.METRICS_ENABLED = flag
    runner = Mock()
    monkeypatch.setattr(admission, "run_project_pass", runner)
    # Actual activity invocation reaches the continuous admission boundary.
    assert not tasks.refresh_derived_metric_project._original_func(
        reservation.payload()
    )
    runner.assert_not_called()
    state.refresh_from_db()
    assert state.lease_token is None and state.lease_holder == ""
    assert state.fencing_epoch == reservation.grant.fencing_epoch
    assert state.watermark_ns == 123
    assert state.reconciliation_cursor == {"phase": "spans"}
    assert state.refresh_requested_at == reservation.requested_at
    assert state.pending_backfill and state.pending_repair
    assert state.pending_expiry and state.pending_erasure
    assert state.consecutive_failures == 0 and state.last_error_code == ""
    assert not MetricRefreshAttempt.objects.filter(project=project).exists()


def test_visible_activity_completes_and_releases(project, enabled, monkeypatch):
    state, reservation = queued(project)
    called = []

    def run(lease, purpose):
        assert not connection.in_atomic_block
        lease.check()
        called.append((lease.project_id, purpose))

    monkeypatch.setattr(admission, "run_project_pass", run)
    assert tasks.refresh_derived_metric_project._original_func(reservation.payload())
    assert called == [(project.pk, reservation.purpose)]
    state.refresh_from_db()
    assert state.lease_token is None and state.refresh_requested_at is None
    assert state.pending_expiry and state.pending_erasure
    assert not tasks.refresh_derived_metric_project._original_func(
        reservation.payload()
    )
    assert len(called) == 1


@pytest.mark.parametrize("kind", ["hidden", "deleted"])
def test_rechecks_after_eligibility_before_lease(
    project, agent, enabled, monkeypatch, kind
):
    state = MetricProjectRefresh.objects.create(project=project)
    real_eligible = admission.eligible_projects

    class Rows(list):
        # reserve_projects streams the queryset with iterator(chunk_size=...).
        def iterator(self, chunk_size=None):
            return iter(self)

    def changed(now):
        candidates = Rows(real_eligible(now))
        assert [row.project_id for row in candidates] == [project.pk]
        change_visibility(project, agent, kind)
        return candidates

    monkeypatch.setattr(admission, "eligible_projects", changed)
    assert admission.reserve_projects() == []
    state.refresh_from_db()
    assert state.fencing_epoch == 0
    assert state.lease_token is None and state.last_started_at is None


@pytest.mark.parametrize("kind", ["hidden", "deleted"])
def test_rechecks_before_enqueue(project, agent, enabled, monkeypatch, kind):
    state = MetricProjectRefresh.objects.create(project=project, pending_erasure=True)
    real_reserve = admission.reserve_projects

    def changed(**kwargs):
        reservations = real_reserve(**kwargs)
        assert len(reservations) == 1
        change_visibility(project, agent, kind)
        return reservations

    monkeypatch.setattr(admission, "reserve_projects", changed)
    payloads = []
    assert admission.dispatch(payloads.append) == 0
    assert payloads == []
    state.refresh_from_db()
    assert state.lease_token is None and state.pending_erasure
    assert state.consecutive_failures == 0


def test_foreign_payload_cannot_claim_or_release_another_project(
    project, observe_project, enabled, monkeypatch
):
    own, reservation = queued(project)
    other = MetricProjectRefresh.objects.create(project=observe_project)
    grant = MetricProjectRefresh.take_lease(
        observe_project.pk,
        holder="metrics:queued:incremental",
        ttl=timedelta(minutes=3),
    )
    payload = {**reservation.payload(), "project_id": str(observe_project.pk)}
    runner = Mock()
    monkeypatch.setattr(admission, "run_project_pass", runner)
    assert not tasks.refresh_derived_metric_project._original_func(payload)
    runner.assert_not_called()
    own.refresh_from_db()
    other.refresh_from_db()
    assert own.lease_token == reservation.grant.lease_token
    assert other.lease_token == grant.lease_token
    assert other.lease_holder == "metrics:queued:incremental"


def test_rejected_old_delivery_preserves_replacement(project, agent, enabled):
    state, old = queued(project)
    later = old.grant.expires_at + timedelta(seconds=1)
    (replacement,) = admission.reserve_projects(clock=lambda: later)
    change_visibility(project, agent, "hidden")
    runner = Mock()
    assert not admission.execute_reservation(
        old.payload(), clock=lambda: later, runner=runner
    )
    runner.assert_not_called()
    state.refresh_from_db()
    assert state.lease_token == replacement.grant.lease_token
    assert state.fencing_epoch == replacement.grant.fencing_epoch


def test_off_duplicate_does_not_release_running_job(project, enabled, settings):
    state, reservation = queued(project)

    def run(lease, purpose):
        settings.METRICS_ENABLED = 0
        assert not admission.execute_reservation(reservation.payload(), runner=Mock())
        lease.check()

    assert admission.execute_reservation(reservation.payload(), runner=run)
    state.refresh_from_db()
    assert state.lease_token is None and state.consecutive_failures == 0


def test_admission_waits_for_visibility_commit(project, agent, enabled, monkeypatch):
    state, reservation = queued(project)
    reached_lock = Event()
    runner = Mock()
    monkeypatch.setattr(admission, "run_project_pass", runner)

    def execute():
        close_old_connections()
        try:

            def observe_query(execute, sql, params, many, context):
                if '"tracer_project"' in sql and "FOR UPDATE" in sql:
                    reached_lock.set()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe_query):
                return tasks.refresh_derived_metric_project._original_func(
                    reservation.payload()
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            # Ordinary native UPDATE does not acquire the lifecycle advisory lock.
            change_visibility(project, agent, "hidden")
            future = pool.submit(execute)
            assert reached_lock.wait(timeout=10)
            assert not future.done()
        assert future.result(timeout=10) is False
    runner.assert_not_called()
    state.refresh_from_db()
    assert state.lease_token is None and state.watermark_ns == 123


def test_failed_reservation_rolls_back_parent_and_lease(project, enabled, monkeypatch):
    state = MetricProjectRefresh.objects.create(project=project)
    real_take = MetricProjectRefresh.take_lease

    def fail(*args, **kwargs):
        assert real_take(*args, **kwargs) is not None
        raise RuntimeError("synthetic rollback")

    monkeypatch.setattr(MetricProjectRefresh, "take_lease", fail)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        admission.reserve_projects()
    state.refresh_from_db()
    assert state.fencing_epoch == 0
    assert state.lease_token is None and state.last_started_at is None
    monkeypatch.setattr(MetricProjectRefresh, "take_lease", real_take)
    assert len(admission.reserve_projects()) == 1
