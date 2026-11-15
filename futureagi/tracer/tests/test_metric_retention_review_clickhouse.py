# ruff: noqa: F811 -- shared native fixtures
"""Independent retention review regressions on real PostgreSQL and ClickHouse."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricRun,
    MetricErasureRequest,
    MetricProjectRefresh,
    MetricRefreshAttempt,
)
from tracer.services.derived_metrics import erasure, retention, source_retention
from tracer.services.derived_metrics.deletion_protocol import _intent
from tracer.sql_workbench.tests.test_permissions import (  # noqa: F401
    create_job,
    tenants,
)
from tracer.tests.test_derived_metric_lifecycle_safety import _metric, _version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import (
    numeric_definition,
    seed_fields_and_buckets,
)

pytestmark = pytest.mark.django_db(transaction=True)


def frozen_run(workspace, project, now, *, status="complete", kind="run"):
    return DerivedMetricRun.objects.create(
        definition=_version(_metric(workspace, project), now),
        window_start=now - timedelta(days=3),
        window_end=now - timedelta(hours=1),
        as_of=now,
        fingerprint=uuid4().hex,
        status=status,
        kind=kind,
        supported_grain="conversation" if kind == "snapshot" else "",
        snapshot_id=uuid4(),
        result_id=uuid4(),
        attempt_id=uuid4() if status != "queued" else None,
    )


def native_result(store, run):
    metric = run.definition.metric
    store.execute(
        "INSERT INTO derived_metric_results (org_id, workspace_id, project_id, metric_id, "
        "run_id, result_id, window_end, rollup, status, value) VALUES "
        "(%(org)s, %(workspace)s, %(project)s, %(metric)s, %(run)s, %(result)s, %(end)s, 'overall', 'ok', 42.)",
        {
            "org": str(metric.organization_id),
            "workspace": str(metric.workspace_id),
            "project": str(metric.project_id),
            "metric": str(metric.pk),
            "run": str(run.pk),
            "result": str(run.result_id),
            "end": run.window_end,
        },
    )


@pytest.mark.parametrize(
    "change", ["expired", "deleted", "retained", "late_policy", "late_delete"]
)
@pytest.mark.parametrize("kind", ["run", "snapshot"])
def test_monitor_graph_guards_frozen_results(
    publication_store, workspace, observe_project, settings, monkeypatch, change, kind
):
    from tracer.services.derived_metrics import integrations, service
    from tracer.utils import monitor_graphs

    settings.METRICS_ENABLED = 1
    now = timezone.now()
    run = frozen_run(workspace, observe_project, now, kind=kind)
    native_result(publication_store, run)
    floor = {"at": now - timedelta(days=30)}
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: floor["at"])
    monkeypatch.setattr(service, "_authorized_metric", lambda metric: True)
    if change == "expired":
        floor["at"] = now - timedelta(days=1)
    if change == "deleted":
        source_retention.expire_run(run)
    monitor = SimpleNamespace(
        metric=str(run.definition.metric_id),
        project_id=observe_project.pk,
        workspace_id=workspace.pk,
        organization_id=workspace.organization_id,
        threshold_type="static",
        filters=[],
        metric_type="derived_metric",
        alert_frequency=60,
    )
    builder = integrations.DerivedMonitorQueryBuilder(monitor)
    monkeypatch.setattr(
        monitor_graphs, "_build_monitor_graph_ch_builder", lambda *a: builder
    )

    def execute(self, sql, params, **kwargs):
        rows = publication_store.execute(sql, params)
        if change == "late_policy":
            floor["at"] = now - timedelta(days=1)
        if change == "late_delete":
            source_retention.expire_run(run)
        return SimpleNamespace(data=[{"timestamp": r[0], "value": r[1]} for r in rows])

    monkeypatch.setattr(
        monitor_graphs.AnalyticsQueryService, "execute_ch_query", execute
    )
    if change.startswith("late_"):
        from tracer.services.derived_metrics.errors import MetricInputError

        with pytest.raises(MetricInputError):
            monitor_graphs.get_static_metric_graph_data(
                monitor, now - timedelta(days=1), now
            )
    else:
        result = monitor_graphs.get_static_metric_graph_data(
            monitor, now - timedelta(days=1), now
        )
        assert bool(result) == (change == "retained")


@pytest.mark.parametrize("empty", [True, False])
def test_empty_or_retained_head_policy_replan_resumes(
    live_writer, workspace, observe_project, empty, settings
):
    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics.admission import run_expiry_pass
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    writer, now = live_writer, timezone.now()
    metric = _metric(workspace, observe_project)
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=numeric_definition(metric.pk).inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        backfill_status="complete",
        history_mode="history",
    )
    if not empty:
        seed_fields_and_buckets(
            writer, definition, [("kept", now - timedelta(hours=1), 7, "kept-user")]
        )
    else:
        first = writer.begin()
        writer.seal(first)
        writer.stage_and_publish(first)
    previous = MetricProjectRefresh.for_project(
        writer.lease.project_id
    ).head_manifest_id
    attempt = writer.begin(ceilings={"as_of": now})
    writer.seal(attempt)
    policies = iter([-1, 1])
    writer.retention_days = lambda: next(policies)
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    writer.retention_days = lambda: 1
    settings.METRICS_ENABLED = 0
    # This is the actual privacy-only runner, not direct debt manipulation.
    run_expiry_pass(writer)
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert state.head_manifest_id is not None and previous is not None
    assert not state.pending_expiry
    assert (
        not MetricErasureRequest.objects.filter(
            project_key=writer.lease.project_id,
            store_payload__protocol="metric_policy_replan",
        )
        .exclude(status="done")
        .exists()
    )
    assert not MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()
    settings.METRICS_ENABLED = 1
    seed_fields_and_buckets(writer, definition, [("fresh", now, 9, "fresh-user")])
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert "fresh" in {r[0] for r in current_fields(writer, head)}


@pytest.mark.parametrize("scope", ["history", "run"])
def test_copy_cleanup_keeps_valid_sql_results(publication_store, tenants, scope):
    from tracer.sql_workbench.metric_retention import guard_result

    (actor, project, _), _ = tenants
    body = {"rows": [[42]]}
    jobs = [
        create_job(actor, project, state=state, sql=sql, result=body)
        for state in ("running", "succeeded")
        for sql in ("SELECT 1", "SELECT value FROM derived_metrics")
    ]
    job = _intent(
        organization_id=actor.organization_id,
        project_id=project.pk,
        scope=scope,
        run_key=uuid4() if scope == "run" else None,
    )
    assert erasure.process_erasure(job.pk, store=publication_store)
    for saved in jobs:
        before = saved.state
        saved.refresh_from_db()
        assert saved.state == before and saved.result == body
        assert guard_result(saved)


def test_subject_erasure_invalidates_only_metric_sql(tenants):
    (actor, project, _), _ = tenants
    body = {"rows": [[42]]}
    ordinary = create_job(actor, project, state="running", sql="SELECT 1", result=body)
    metric = create_job(
        actor,
        project,
        state="running",
        sql="SELECT value FROM derived_metrics",
        result=body,
    )
    erasure.erase_metric_data(
        organization_id=actor.organization_id,
        project_id=project.pk,
        scope_kind="users",
        subject_ids=["synthetic-subject"],
    )
    ordinary.refresh_from_db()
    metric.refresh_from_db()
    assert ordinary.state == "running" and ordinary.result == body
    assert metric.state == "cancelled" and metric.result == {}


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_expired_legacy_run_drains_tracked_native_output(
    publication_store, workspace, observe_project, monkeypatch, status
):
    from tracer.services.derived_metrics import snapshots

    now = timezone.now()
    run = frozen_run(workspace, observe_project, now, status=status)
    # New legacy writers durably enroll in the same native submission protocol.
    # Uninstrumented historical running/failed tasks must remain fail-closed.
    run.source_manifest = {"write_protocol": snapshots.SUBMISSION_PROTOCOL}
    run.save(update_fields=["source_manifest"])
    native_result(publication_store, run)
    monkeypatch.setattr(
        source_retention, "current_cutoff", lambda *a, **k: now - timedelta(days=1)
    )
    assert source_retention.expire_runs() == 1
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert erasure.process_erasure(request.pk, store=publication_store)
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s",
        {"run": str(run.pk)},
    ) == [(0,)]
    # A task holding the old run object cannot dispatch after revocation.
    with pytest.raises(Exception, match="expired capture"):
        snapshots._write(
            publication_store,
            run,
            "INSERT INTO derived_metric_results (run_id) VALUES (%(run)s)",
            {"run": str(run.pk)},
        )
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s",
        {"run": str(run.pk)},
    ) == [(0,)]


def test_blocked_recovery_erasure_completes_without_clearing_recovery(live_writer):
    writer, now = live_writer, timezone.now()
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(writer, definition, [("private", now, 7, "erase-user")])
    blocked = writer.begin(purpose="compaction", ceilings={"recovery_blocked": True})
    writer.freeze_current(
        blocked,
        retention.FIELD_VALUES,
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest,
    )
    writer.seal(blocked)
    writer.stage_and_publish(blocked)
    request = erasure.erase_metric_data(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope_kind="users",
        subject_ids=["erase-user"],
    )
    assert erasure.process_erasure(request.pk, store=writer.store, writer=writer)
    request.refresh_from_db()
    assert request.status == "done" and request.completed_at is not None
    assert set(request.receipts) == set(MetricErasureRequest.Store.values)
    assert not MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()
    assert MetricProjectRefresh.for_project(
        writer.lease.project_id
    ).head_manifest.published_by.source_ceilings["recovery_blocked"]
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values_checkpoint WHERE user_id = 'erase-user'"
    ) == [(0,)]


def test_failed_erasure_closes_staged_attempt_and_bounds_automatic_retries(
    live_writer, monkeypatch
):
    writer, now = live_writer, timezone.now()
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(writer, definition, [("private", now, 7, "erase-user")])
    request = erasure.erase_metric_data(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope_kind="users",
        subject_ids=["erase-user"],
    )

    # Fail after real native insertion, at its digest verification boundary.
    def interrupted(*a, **k):
        raise RuntimeError("synthetic private diagnostic must not be persisted")

    monkeypatch.setattr(writer.store, "verify", interrupted)
    for _ in range(5):
        assert not erasure.process_erasure(
            request.pk, store=writer.store, writer=writer
        )
        assert not MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
        ).exists()
    request.refresh_from_db()
    assert (
        request.status == "failed"
        and request.completed_at is None
        and request.receipts == {}
    )
    assert request.error_code == "native_or_state_cleanup_pending"
    assert request.store_payload["cleanup_failures"] == 5
    assert not erasure.pending_requests().filter(pk=request.pk).exists()


def test_legacy_late_native_output_is_drained_before_final_scrub(
    publication_store, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics import snapshots

    now = timezone.now()
    run = frozen_run(workspace, observe_project, now, status="running")
    run.source_manifest = {"write_protocol": snapshots.SUBMISSION_PROTOCOL}
    run.save(update_fields=["source_manifest"])
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: None)
    native = publication_store.client.execute
    fired = []

    def late(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO derived_metric_results") and not fired:
            fired.append(True)
            source_retention.expire_run(run)
            request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
            assert not erasure.process_erasure(request.pk, store=publication_store)
            request.refresh_from_db()
            assert request.receipts == {} and request.completed_at is None
        return native(sql, *args, **kwargs)

    monkeypatch.setattr(publication_store.client, "execute", late)
    snapshots._write(
        publication_store,
        run,
        "INSERT INTO derived_metric_results (org_id, project_id, run_id, result_id) VALUES (%(org)s, %(project)s, %(run)s, %(result)s)",
        {
            "org": str(workspace.organization_id),
            "project": str(observe_project.pk),
            "run": str(run.pk),
            "result": str(run.result_id),
        },
    )
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s",
        {"run": str(run.pk)},
    ) == [(1,)]
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert erasure.process_erasure(request.pk, store=publication_store)
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s",
        {"run": str(run.pk)},
    ) == [(0,)]


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_untracked_historical_writer_requires_real_operator_drain(
    publication_store, workspace, observe_project, status
):
    run = frozen_run(workspace, observe_project, timezone.now(), status=status)
    native_result(publication_store, run)
    source_retention.expire_run(run)
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert not erasure.process_erasure(request.pk, store=publication_store)
    request.refresh_from_db()
    assert request.error_code == "legacy_writer_drain_required"
    assert request.receipts == {} and request.completed_at is None
    assert not erasure.pending_requests().filter(pk=request.pk).exists()


def test_legacy_execution_registers_every_result_write_before_expiry(
    publication_store, workspace, observe_project, user, monkeypatch
):
    from contextlib import nullcontext

    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics import service, snapshots

    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=numeric_definition(metric.pk).inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        backfill_status="complete",
        history_mode="history",
    )
    run = DerivedMetricRun.objects.create(
        definition=version,
        requested_by=user,
        window_start=now - timedelta(hours=2),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
        snapshot_id=uuid4(),
        source_manifest={
            "write_protocol": snapshots.SUBMISSION_PROTOCOL,
            "schema_version": 1,
            "compiler_version": 1,
            "definition_hash": version.definition_hash,
        },
    )
    publication_store.execute(
        "INSERT INTO derived_metric_inputs (org_id, workspace_id, project_id, snapshot_id, unit_id, session_id, input_values, probability) "
        "VALUES (%(org)s, %(workspace)s, %(project)s, %(snapshot)s, 'synthetic-unit', 'synthetic-unit', map('y', 7.), 1.)",
        {
            "org": str(workspace.organization_id),
            "workspace": str(workspace.pk),
            "project": str(observe_project.pk),
            "snapshot": str(run.snapshot_id),
        },
    )
    monkeypatch.setattr(
        snapshots, "snapshot_store", lambda: nullcontext(publication_store)
    )
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: None)
    monkeypatch.setattr(service, "_authorized_metric", lambda m: True)
    monkeypatch.setattr(service, "_requester_can_run", lambda r: True)
    service.execute_run(run.pk)
    run.refresh_from_db()
    assert run.status == "complete"
    submissions = MetricErasureRequest.objects.filter(
        run_key=run.pk, store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert submissions.count() == 6
    assert (
        submissions.filter(
            status="done", receipts__synchronous_response__drained=1
        ).count()
        == 6
    )
    assert publication_store.execute(
        "SELECT value FROM derived_metric_results WHERE run_id = %(run)s AND rollup = 'overall'",
        {"run": str(run.pk)},
    ) == [(7.0,)]
    source_retention.expire_run(run)
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert erasure.process_erasure(request.pk, store=publication_store)
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s",
        {"run": str(run.pk)},
    ) == [(0,)]
