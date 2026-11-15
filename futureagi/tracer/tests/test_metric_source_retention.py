# ruff: noqa: F811 -- shared real PostgreSQL fixture
"""Source-retention regressions on real PostgreSQL control state."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricRun,
    MetricErasureRequest,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics import admission, reads, source_retention
from tracer.services.derived_metrics.contributors import read_contributors
from tracer.services.derived_metrics.erasure import erase_metric_data
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.service import (
    MetricUnavailableError,
    read_results,
)
from tracer.sql_workbench import service as sql_service
from tracer.sql_workbench import tasks
from tracer.sql_workbench.tests.test_permissions import (  # noqa: F401
    create_job,
    tenants,
)
from tracer.tests.test_derived_metric_lifecycle_safety import (
    _commit,
    _metric,
    _version,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def policy(monkeypatch):
    value = {"cutoff": timezone.now() - timedelta(days=30)}
    monkeypatch.setattr(
        source_retention, "current_cutoff", lambda *a, **k: value["cutoff"]
    )
    return value


def test_expired_and_boundary_windows_are_unavailable_not_stale_totals(policy):
    floor = policy["cutoff"]
    manifest = SimpleNamespace(
        published_by=SimpleNamespace(
            source_ceilings={
                "retention_floor": floor.isoformat(),
            }
        )
    )
    for start in (floor - timedelta(days=1), floor - timedelta(microseconds=1)):
        with pytest.raises(MetricUnavailableError, match="outside current retention"):
            source_retention.require_window(uuid4(), start, manifest=manifest)
    source_retention.require_window(uuid4(), floor, manifest=manifest)
    policy["cutoff"] = floor - timedelta(days=60)
    assert not source_retention.window_retained(
        uuid4(), floor - timedelta(days=1), manifest=manifest
    )


@pytest.mark.parametrize("kind", ["run", "snapshot"])
def test_legacy_and_snapshot_expiry_is_permanent_and_has_cleanup_intent(
    workspace,
    observe_project,
    policy,
    kind,
):
    now = timezone.now()
    version = _version(_metric(workspace, observe_project), now)
    run = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=60),
        window_end=now - timedelta(days=59),
        as_of=now,
        fingerprint=uuid4().hex,
        status="complete",
        kind=kind,
        supported_grain="conversation" if kind == "snapshot" else "",
        snapshot_id=uuid4(),
        result_id=uuid4(),
        attempt_id=uuid4(),
    )
    for reader in (read_results, read_contributors):
        with pytest.raises(MetricInputError, match="source retention"):
            reader(run)
    run.refresh_from_db()
    assert run.deleted_at and run.cleanup_status == "pending"
    intent = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert intent.snapshot_key == run.snapshot_id and intent.result_key == run.result_id
    policy["cutoff"] = None
    with pytest.raises(MetricInputError):
        read_results(run)


def test_history_finality_uses_watermark_and_settle_bound(workspace, observe_project):
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    version = _version(_metric(workspace, observe_project), now)
    version.history_mode = "history"
    _commit(
        observe_project, now, cursors={"watermark_ns": int(now.timestamp()) * 10**9}
    )
    state = MetricProjectRefresh.objects.get(project=observe_project)
    window = {
        "window_start": now - timedelta(hours=1),
        "window_end": now + timedelta(hours=1),
    }
    finality = reads.read_finality(
        None, version.metric, version, state.head_manifest, window
    )
    assert finality is not None
    disclosure = finality.disclosure(window)
    assert disclosure["partial"] and disclosure["nonfinal_buckets"] >= 4
    assert disclosure["complete_through"] <= now


def test_disabled_rollout_dispatches_only_enrolled_privacy_work(
    workspace,
    observe_project,
    settings,
):
    settings.METRICS_ENABLED = 0
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    state = MetricProjectRefresh.objects.create(
        project=observe_project, pending_expiry=True
    )
    payloads = []
    assert admission.dispatch(payloads.append) == 1
    (payload,) = payloads
    assert payload["purpose"] == "expiry"
    assert payload["project_id"] == str(state.project_id)
    runner = Mock()
    assert admission.execute_reservation(payload, runner=runner)
    assert runner.call_args.args[1] == "expiry"


def metric_job(actor, project, **kwargs):
    return create_job(
        actor, project, sql="SELECT group_key, value FROM derived_metrics", **kwargs
    )


def test_saved_sql_poll_download_cache_and_stale_object_are_erased(tenants, policy):
    (actor, project, _), (other, foreign, _) = tenants
    body = {
        "columns": [{"name": "group_key", "type": "String"}],
        "rows": [["private-subject"]],
    }
    job = metric_job(
        actor,
        project,
        state="succeeded",
        result=body,
        cache_key="a" * 64,
        finished_at=timezone.now(),
    )
    foreign_job = metric_job(other, foreign, state="succeeded", result=body)
    erase_metric_data(
        organization_id=actor.organization_id,
        project_id=project.id,
        scope_kind="users",
        subject_ids=["private-subject"],
    )
    assert sql_service.snapshot(job)["result"] == {}
    with pytest.raises(sql_service.ResultExpired):
        sql_service.export_result(actor, project.id, job.pk, "csv")
    assert sql_service.reusable_result(actor, project, "a" * 64, timezone.now()) is None
    foreign_job.refresh_from_db()
    assert foreign_job.result == body


@pytest.mark.parametrize("change", ["erasure", "shortening"])
def test_sql_completion_cannot_republish_after_policy_change(
    tenants, policy, monkeypatch, change
):
    (actor, project, _), _ = tenants
    monkeypatch.setenv("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "true")
    job = metric_job(actor, project)

    def native(*args, **kwargs):
        if change == "erasure":
            request = erase_metric_data(
                organization_id=actor.organization_id,
                project_id=project.pk,
                scope_kind="project",
            )
            # The completion guard must also reject an already-completed erase.
            MetricErasureRequest.objects.filter(pk=request.pk).update(
                status="done", completed_at=timezone.now()
            )
        else:
            policy["cutoff"] = timezone.now()
        return {"columns": [], "rows": [["must-not-reappear"]], "result_bytes": 50}

    monkeypatch.setattr(tasks, "run", native)
    tasks.execute_sql(str(job.pk))
    job.refresh_from_db()
    assert job.state != "succeeded" and job.result == {} and job.output_bytes == 0


def test_saved_sql_expiry_stays_erased_after_policy_extension(tenants, policy):
    (actor, project, _), _ = tenants
    job = metric_job(actor, project, state="succeeded", result={"rows": [[1]]})
    policy["cutoff"] = timezone.now()
    assert sql_service.snapshot(job)["result"] == {}
    policy["cutoff"] = None
    assert sql_service.snapshot(job)["result"] == {}


def test_policy_outage_does_not_disable_existing_erasure_cleanup(settings, monkeypatch):
    from django.core.exceptions import ImproperlyConfigured

    from tracer.services.derived_metrics import erasure, history_gc
    from tracer.sql_workbench import metric_retention
    from tracer.tasks.derived_metric import cleanup_derived_metric_snapshots

    settings.METRICS_ENABLED = 0

    def unavailable(**kwargs):
        raise ImproperlyConfigured("synthetic policy outage")

    monkeypatch.setattr(source_retention, "expire_runs", unavailable)
    monkeypatch.setattr(metric_retention, "expire_results", unavailable)
    sweep = Mock(return_value=1)
    monkeypatch.setattr(erasure, "sweep", sweep)
    monkeypatch.setattr(history_gc, "run_scheduled", Mock(return_value=[]))
    assert cleanup_derived_metric_snapshots() == 1
    sweep.assert_called_once_with(limit=10)


def test_expiry_uses_current_snapshot_namespace_under_project_lock(
    workspace,
    observe_project,
    policy,
):
    now = timezone.now()
    run = DerivedMetricRun.objects.create(
        definition=_version(_metric(workspace, observe_project), now),
        window_start=now - timedelta(days=60),
        window_end=now - timedelta(days=59),
        as_of=now,
        fingerprint=uuid4().hex,
        status="running",
        kind="snapshot",
        supported_grain="conversation",
        snapshot_id=uuid4(),
        attempt_id=uuid4(),
    )
    new_snapshot = uuid4()
    # The scheduler read the old row before a capture takeover allocated this
    # namespace. expire_run must never write an outbox with the stale key.
    DerivedMetricRun.objects.filter(pk=run.pk).update(
        snapshot_id=new_snapshot, attempt_id=uuid4()
    )
    source_retention.expire_run(run)
    request = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert request.snapshot_key == new_snapshot


def test_expiry_preserves_an_earlier_pin_revocation(workspace, observe_project):
    from tracer.services.derived_metrics.deletion_protocol import capture_pin

    now = timezone.now()
    version = _version(_metric(workspace, observe_project), now)
    _commit(observe_project, now)
    pin = capture_pin(
        observe_project.pk, consumer_kind="snapshot", ttl=timedelta(minutes=5)
    )
    revoked = timezone.now()
    type(pin).objects.filter(pk=pin.pk).update(
        invalidated_at=revoked, invalidation_reason="erased"
    )
    run = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=60),
        window_end=now - timedelta(days=59),
        as_of=now,
        fingerprint=uuid4().hex,
        pin=pin,
    )
    source_retention.expire_run(run)
    pin.refresh_from_db()
    assert pin.invalidated_at == revoked and pin.invalidation_reason == "erased"


def test_pending_privacy_work_bypasses_paused_batch_interval(
    workspace,
    observe_project,
    settings,
):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    settings.METRICS_MAX_CHECKPOINT_ROWS = 10
    now = timezone.now()
    MetricProjectRefresh.objects.create(
        project=observe_project,
        paused_reason="size_cap",
        paused_at=now,
        paused_rows=11,
        last_started_at=now,
        pending_expiry=True,
    )
    (reservation,) = admission.reserve_projects()
    assert reservation.purpose == "expiry"
    admission.release(reservation, now=timezone.now())


def test_erasure_remains_queued_if_initial_sql_cleanup_fails(tenants, policy, monkeypatch):
    from tracer.sql_workbench import metric_retention

    (actor, project, _), _ = tenants
    job = metric_job(
        actor, project, state="succeeded", result={"rows": [["erased-subject"]]}
    )

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic SQL cleanup outage")

    monkeypatch.setattr(metric_retention, "invalidate_scope", unavailable)
    request = erase_metric_data(
        organization_id=actor.organization_id,
        project_id=project.pk,
        scope_kind="project",
    )
    request.refresh_from_db()
    assert request.status == "queued" and request.completed_at is None
    # Even before the worker's retry, the durable intent denies poll/download.
    assert sql_service.snapshot(job)["result"] == {}


def test_sql_database_failure_cannot_roll_back_a_queued_erasure(
    tenants, policy, monkeypatch,
):
    from django.db import connection, transaction

    from tracer.sql_workbench import metric_retention

    (actor, project, _), _ = tenants
    job = metric_job(actor, project, state="succeeded", result={"rows": [[1]]})

    def database_error(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 / 0")

    monkeypatch.setattr(metric_retention, "invalidate_scope", database_error)
    with transaction.atomic():
        request = erase_metric_data(
            organization_id=actor.organization_id,
            project_id=project.pk,
            scope_kind="project",
        )
    request.refresh_from_db()
    assert request.status == "queued"
    assert sql_service.snapshot(job)["result"] == {}


def test_finality_projection_cannot_drop_winner_keys():
    from tracer.services.derived_metrics.bounded_reads import windowed_rows_select
    from tracer.services.derived_metrics.storage import FIELD_VALUES

    kwargs = {"org_id": uuid4(), "project_id": uuid4(), "predicate": "status = 'open'", "params": {}}
    with pytest.raises(ValueError, match="complete winner keys"):
        windowed_rows_select(FIELD_VALUES, None, columns=("bucket_start", "status"), **kwargs)
    sql, _ = windowed_rows_select(
        FIELD_VALUES, None, columns=(
            *FIELD_VALUES.logical_key, "generation", FIELD_VALUES.version_column,
            "bucket_start", "status", "is_deleted",
        ), **kwargs,
    )
    assert "inputs" not in sql and "user_id" not in sql


def test_small_result_reads_do_not_jit_or_relax_work_budgets():
    client = Mock()
    reads.MetricReadStore(client).execute("SELECT 1")
    settings = client.execute.call_args.kwargs["settings"]
    assert settings["compile_expressions"] == 0
    assert settings["compile_aggregate_expressions"] == 0
    assert settings["compile_sort_description"] == 0
    assert settings["max_memory_usage"] == 512 * 1024 * 1024
    assert settings["max_threads"] == 2 and settings["max_execution_time"] == 60
