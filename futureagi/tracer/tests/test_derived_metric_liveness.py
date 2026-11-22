# ruff: noqa: F811 -- shared PG admission fixtures
"""Deadline and lock liveness without weakening publication/erasure fencing."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic

import pytest
from django.db import close_old_connections, transaction

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import admission
from tracer.services.derived_metrics.deletion_protocol import serialized_project
from tracer.services.derived_metrics.lease import MetricPassDeadline, ProjectLease
from tracer.tests.test_derived_metric_admission import (  # noqa: F401
    NOW,
    enabled,
    state_factory,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "level,owners,overflow",
    [("conversation", 2, True), ("conversation", 1, False), ("span", 1, True)],
)
def test_large_native_arrival_batch_routes_to_repair_without_consuming_cursors(
    enabled, state_factory, monkeypatch, level, owners, overflow
):
    from types import SimpleNamespace

    from tracer.services.derived_metrics import backfill, changes

    state = state_factory(pending_backfill=True)
    writer = SimpleNamespace(
        lease=SimpleNamespace(project_id=state.project_id),
        org_id=state.project.organization_id,
        incremental_unit_limit=1,
    )
    batch = changes.ChangeBatch(
        (changes.SourceKey("trace", "a"), changes.SourceKey("trace", "b")),
        tuple(changes.UnitKey("conversation", str(i)) for i in range(owners)),
        (),
        (),
        (),
        {"watermark_ns": 42},
    )
    monkeypatch.setattr(changes, "discover_changes", lambda *args, **kwargs: batch)
    definitions = [SimpleNamespace(level=level, inputs=[])]
    if overflow:
        with pytest.raises(changes.arrivals.ContinuousCandidateOverflow):
            backfill.incremental_changes(None, writer, None, definitions, now=NOW)
    else:
        assert (
            backfill.incremental_changes(None, writer, None, definitions, now=NOW)
            is batch
        )
    state.refresh_from_db()
    assert state.watermark_ns == 0 and state.pending_backfill


def test_initial_coverage_proof_requires_zero_cursors_and_empty_parent(
    enabled, state_factory, workspace
):
    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics import backfill, changes
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_backfill import (
        EmptyPolicyRecorder,
        make_version,
    )

    state = state_factory()
    definition = make_version(state.project, workspace)
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="initial-proof", ttl=timedelta(minutes=5), now=NOW
    )
    lease = ProjectLease(grant, clock=lambda: NOW)
    writer = RefreshWriter(
        lease,
        EmptyPolicyRecorder(),
        org_id=workspace.organization_id,
        workspace_id=workspace.id,
        retention_days=lambda: 90,
    )
    runner = backfill.BackfillRunner(
        writer,
        None,
        Scope(workspace.organization_id, workspace.id, state.project_id),
        exhaustive_initial=True,
    )
    empty = changes.ChangeBatch((), (), (), (), (), {})
    control = runner._initial_publication([definition], None, empty, now=NOW)
    assert control["initial_empty_head"] and len(control["backfills"]) == 1
    MetricProjectRefresh.objects.filter(pk=state.pk).update(watermark_ns=1)
    assert runner._initial_publication([definition], None, empty, now=NOW) is None
    MetricProjectRefresh.objects.filter(pk=state.pk).update(watermark_ns=0)
    attempt = writer.begin()
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    with pytest.raises(MetricStateConflict, match="empty parent"):
        backfill.check_backfill_publication(writer, control)
    definition.refresh_from_db()
    assert definition.backfill_status == "pending"
    lease.release()


def test_deadline_cannot_be_extended_by_keepalive(enabled, state_factory):
    state = state_factory(pending_backfill=True)
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="deadline-test", ttl=timedelta(minutes=5), now=NOW
    )
    lease = ProjectLease(grant, clock=lambda: NOW, deadline=monotonic() - 1)
    for operation in (lease.check, lease.renew, lease.keepalive):
        with pytest.raises(MetricPassDeadline):
            operation()
    state.refresh_from_db()
    assert state.lease_expires_at == grant.expires_at
    lease.release()


def test_deadline_is_not_normalized_into_an_ordinary_read_failure(
    enabled, state_factory
):
    from unittest.mock import Mock

    from tracer.services.derived_metrics import changes

    state = state_factory()
    grant = MetricProjectRefresh.take_lease(
        state.project_id, holder="deadline-read", ttl=timedelta(minutes=5), now=NOW
    )
    lease = ProjectLease(grant, clock=lambda: NOW, deadline=monotonic() - 1)
    client = Mock()
    with pytest.raises(MetricPassDeadline):
        changes.read(
            admission.NativeMetricAnalytics(client, lease=lease),
            "SELECT 1",
            {},
            changes.MetricReadBudget(),
        )
    client.execute.assert_not_called()
    lease.release()


def test_yield_releases_slot_and_lease_without_failure_or_ack(enabled, state_factory):
    state = state_factory(pending_backfill=True, refresh_requested_at=NOW)
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)

    def run(lease, purpose):
        lease.deadline = monotonic() - 1
        lease.check()
        pytest.fail("deadline did not stop Python work")

    assert not admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=run
    )
    state.refresh_from_db()
    assert state.pending_backfill and state.refresh_requested_at == NOW
    assert state.lease_token is None and state.consecutive_failures == 0
    (replacement,) = admission.reserve_projects(clock=lambda: NOW)
    assert admission.execute_reservation(
        replacement.payload(), clock=lambda: NOW, runner=lambda *_: None
    )


def test_yield_after_publication_clears_old_backoff_but_preserves_request(
    enabled, state_factory
):
    from tracer.services.derived_metrics.refresh import RefreshWriter
    from tracer.tests.test_derived_metric_refresh import Recorder

    state = state_factory(
        pending_backfill=True,
        refresh_requested_at=NOW,
        consecutive_failures=3,
        last_error_at=NOW - timedelta(hours=1),
        last_error_code="metric_refresh_failed",
    )
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)

    def run(lease, purpose):
        writer = RefreshWriter(lease, Recorder(), org_id=state.project.organization_id)
        attempt = writer.begin(purpose="backfill")
        writer.seal(attempt)
        writer.stage_and_publish(attempt)
        lease.deadline = monotonic() - 1
        lease.check()

    assert not admission.execute_reservation(
        reservation.payload(), clock=lambda: NOW, runner=run
    )
    state.refresh_from_db()
    assert state.head_manifest_id and state.consecutive_failures == 0
    assert state.pending_backfill and state.refresh_requested_at == NOW
    assert state.lease_token is None
    (replacement,) = admission.reserve_projects(clock=lambda: NOW)
    admission.release(replacement, now=NOW)


def test_run_now_does_not_wait_for_lifecycle_or_refresh_row_lock(
    enabled, state_factory, workspace, monkeypatch
):
    from functools import partial

    from tracer.services.derived_metrics import service
    from tracer.tests.test_derived_metric_backfill import make_version
    from tracer.views.derived_metric import MetricViewSet

    state = state_factory()
    metric = make_version(state.project, workspace, active=True).metric
    (reservation,) = admission.reserve_projects(clock=lambda: NOW)
    requested_at = NOW + admission.RUN_NOW_THROTTLE + timedelta(seconds=1)
    monkeypatch.setattr(
        admission,
        "request_refresh",
        partial(admission.request_refresh, clock=lambda: requested_at),
    )
    entered, release_row, row_released, finish = Event(), Event(), Event(), Event()

    def run(lease, purpose):
        with transaction.atomic(), serialized_project(state.project_id):
            MetricProjectRefresh.objects.select_for_update().get(pk=state.pk)
            entered.set()
            assert release_row.wait(10)
        row_released.set()
        assert finish.wait(10)

    def writer():
        close_old_connections()
        try:
            return admission.execute_reservation(
                reservation.payload(), clock=lambda: NOW, runner=run
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        try:
            assert entered.wait(5)
            state.refresh_from_db()
            assert state.refresh_requested_at is None
            assert requested_at > state.last_started_at + admission.RUN_NOW_THROTTLE
            started = monotonic()
            with pytest.raises(service.MetricUnavailableError) as error:
                service.admit_refresh(metric)
            assert monotonic() - started < 0.5
            response = MetricViewSet().handle_exception(error.value)
            assert response.status_code == 503
            assert response.data["code"] == "refresh_unavailable"
            assert response.data["retryable"] is True
            assert isinstance(
                error.value.__cause__, admission.RefreshAdmissionUnavailable
            )
            state.refresh_from_db()
            assert state.refresh_requested_at is None
            release_row.set()
            assert row_released.wait(5)
            assert service.admit_refresh(metric) == {"status": "queued"}
            state.refresh_from_db()
            assert state.refresh_requested_at == requested_at
        finally:
            release_row.set()
            finish.set()
        assert future.result(timeout=5)
    # A successful old pass must not acknowledge the retried, later request.
    state.refresh_from_db()
    assert state.refresh_requested_at == requested_at


@pytest.mark.parametrize("parent_kind", ["project", "workspace", "organization"])
def test_run_now_rejects_parent_deleted_after_visibility_check(
    enabled, state_factory, monkeypatch, parent_kind
):
    from django.db.models.query import QuerySet

    from tfc.utils.audit import mute_audit_signals
    from tracer.models.project import Project

    state = state_factory()
    project_id = state.project_id
    parent = {
        "project": state.project,
        "workspace": state.project.workspace,
        "organization": state.project.organization,
    }[parent_kind]
    exists = QuerySet.exists
    visibility_checked = False

    def delete_parent():
        close_old_connections()
        try:
            # Bulk parent deletion cannot retain membership audit FKs to the
            # removed organization. Leave all metric lifecycle hooks enabled.
            with mute_audit_signals():
                type(parent)._base_manager.filter(pk=parent.pk).delete()
        finally:
            close_old_connections()

    def exists_then_delete(queryset):
        nonlocal visibility_checked
        result = exists(queryset)
        if queryset.model is Project and not visibility_checked:
            visibility_checked = True
            assert result
            # Commit on another connection after the visibility SELECT, before
            # request_refresh can look up state or attempt lazy enrollment.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(delete_parent).result(timeout=10)
            assert not MetricProjectRefresh.objects.filter(
                project_id=project_id
            ).exists()
        return result

    monkeypatch.setattr(QuerySet, "exists", exists_then_delete)
    with pytest.raises(admission.RefreshAdmissionUnavailable):
        admission.request_refresh(project_id, clock=lambda: NOW)
    assert visibility_checked
    assert not Project._base_manager.filter(pk=project_id).exists()
    assert not MetricProjectRefresh.objects.filter(project_id=project_id).exists()


def test_run_now_does_not_enroll_missing_refresh_state(enabled, state_factory):
    state = state_factory()
    MetricProjectRefresh.objects.filter(pk=state.pk).delete()
    with pytest.raises(admission.RefreshAdmissionUnavailable):
        admission.request_refresh(state.project_id, clock=lambda: NOW)
    assert not MetricProjectRefresh.objects.filter(project_id=state.project_id).exists()


def test_dispatch_skips_busy_lifecycle_without_starving_next_project(
    enabled, state_factory
):
    busy = state_factory()
    available = state_factory()
    MetricProjectRefresh.objects.filter(pk=busy.pk).update(
        created_at=NOW - timedelta(days=2)
    )
    entered, release = Event(), Event()

    def purge():
        close_old_connections()
        try:
            with serialized_project(busy.project_id):
                entered.set()
                assert release.wait(10)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(purge)
        try:
            assert entered.wait(5)
            started = monotonic()
            (reservation,) = admission.reserve_projects(clock=lambda: NOW)
            assert reservation.grant.project_id == available.project_id
            assert monotonic() - started < 0.5
        finally:
            release.set()
        future.result(timeout=5)
    admission.release(reservation, now=NOW)
