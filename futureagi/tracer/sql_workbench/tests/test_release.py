"""PostgreSQL regressions for review findings; no ClickHouse/Temporal server."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils import timezone

from tracer.models.sql_query import SQLExecution
from tracer.sql_workbench import runner, service, tasks
from tracer.sql_workbench.tests.test_permissions import api_request, create_job
from tracer.sql_workbench.tests.test_permissions import tenants as _tenants
from tracer.views.sql_query import SQLQueryViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(monkeypatch):
    return _tenants.__wrapped__(monkeypatch)


def result(rows=None):
    return {
        "columns": [{"name": "n", "type": "Int64"}],
        "rows": rows or [[1]],
        "result_bytes": 40,
    }


@pytest.mark.parametrize("queue", ["tasks_xl", "exact_aggregation"])
def test_submission_dispatches_on_configured_queue(
    tenants, queue, django_capture_on_commit_callbacks
):
    actor, project, _ = tenants[0]
    now = timezone.now()
    with (
        override_settings(EXACT_AGGREGATION_TASK_QUEUE=queue),
        patch.object(service, "connection_config"),
        patch(
            "tfc.temporal.drop_in.runner.start_activity", return_value="started"
        ) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        service.submit(
            actor,
            project_id=project.id,
            sql="SELECT count() FROM spans",
            start=now - timedelta(hours=1),
            end=now,
        )
    assert dispatch.call_args.kwargs["queue"] == queue


def test_invalid_queue_fails_before_admission(tenants):
    actor, project, _ = tenants[0]
    now = timezone.now()
    with (
        override_settings(EXACT_AGGREGATION_TASK_QUEUE="unpolled_typo"),
        patch.object(service, "connection_config"),
        pytest.raises(ImproperlyConfigured),
    ):
        service.submit(
            actor,
            project_id=project.id,
            sql="SELECT count() FROM spans",
            start=now - timedelta(hours=1),
            end=now,
        )
    assert not SQLExecution.objects.exists()


@pytest.mark.parametrize(
    "failure, state, message",
    [
        (runner.QueryCancelled(), "cancelled", ""),
        (runner.QueryTimedOut(), "failed", "30-second"),
        (runner.QueryDeadlineExpired(), "failed", "deadline"),
        (runner.QueryControlLost(), "failed", "control connection"),
        (runner.QueryBusy(), "failed", "busy"),
    ],
)
def test_worker_persists_distinct_safe_outcomes(tenants, failure, state, message):
    actor, project, _ = tenants[0]
    job = create_job(actor, project)
    with patch.object(tasks, "run", side_effect=failure):
        tasks.execute_sql._original_func(str(job.id))
    job.refresh_from_db()
    assert job.state == state and message in job.error
    assert job.result == {} and job.output_bytes == 0


def test_deadline_during_post_run_authorization_cannot_publish(tenants):
    actor, project, _ = tenants[0]
    job = create_job(actor, project)
    authorize = tasks.authorize
    calls = 0

    def reauthorize(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            SQLExecution.objects.filter(id=job.id).update(
                deadline=timezone.now() - timedelta(seconds=1)
            )
        return authorize(*args)

    with (
        patch.object(tasks, "authorize", reauthorize),
        patch.object(tasks, "run", return_value=result()),
    ):
        tasks.execute_sql._original_func(str(job.id))
    job.refresh_from_db()
    assert job.state == "failed" and "deadline" in job.error
    assert job.result == {} and job.output_bytes == 0
    tasks.execute_sql._original_func(str(job.id))
    job.refresh_from_db()
    assert job.state == "failed"


def test_stale_cleanup_is_bounded_and_does_not_release_live_cancellations(tenants):
    actor, project, _ = tenants[0]
    now = timezone.now()
    stale = [
        create_job(actor, project, state=state, deadline=now - timedelta(seconds=1))
        for state in ("queued", "running", "cancelled")
    ]
    live = create_job(actor, project, state="cancelled")
    assert service.reconcile_expired_executions(now=now, batch_size=2) == 2
    assert service.reconcile_expired_executions(now=now, batch_size=2) == 1
    assert service.reconcile_expired_executions(now=now, batch_size=2) == 0
    for job in stale:
        job.refresh_from_db()
        assert job.state in ("failed", "cancelled") and job.finished_at
    live.refresh_from_db()
    assert live.finished_at is None


def test_timeout_failure_notification_is_idempotent_and_respects_live_scan(tenants):
    actor, project, _ = tenants[0]
    job = create_job(actor, project)
    assert tasks.fail_sql_execution._original_func(str(job.id)) == 1
    assert tasks.fail_sql_execution._original_func(str(job.id)) == 0
    live = create_job(actor, project, state="running")
    assert tasks.fail_sql_execution._original_func(str(live.id)) == 0


@pytest.mark.parametrize(
    "state, expired, status",
    [
        ("queued", False, 409),
        ("running", False, 409),
        ("failed", True, 409),
        ("cancelled", False, 409),
        ("succeeded", True, 410),
        ("succeeded", False, 200),
    ],
)
def test_download_distinguishes_state_and_expiry(tenants, state, expired, status):
    actor, project, user = tenants[0]
    job = create_job(
        actor,
        project,
        state=state,
        result=result(),
        expires_at=timezone.now() + timedelta(hours=-1 if expired else 1),
    )
    response = SQLQueryViewSet.as_view({"get": "download"})(
        api_request(
            actor,
            user,
            "get",
            "/download",
            {"project_id": str(project.id), "file_format": "csv"},
        ),
        pk=str(job.id),
    )
    assert response.status_code == status
    assert response["Cache-Control"] == "no-store"
    other, _, other_user = tenants[1]
    denied = SQLQueryViewSet.as_view({"get": "download"})(
        api_request(
            other,
            other_user,
            "get",
            "/download",
            {"project_id": str(project.id), "file_format": "csv"},
        ),
        pk=str(job.id),
    )
    assert denied.status_code == 403


def test_csv_preserves_negative_numeric_precision_but_neutralizes_text(tenants):
    import csv
    import io

    actor, project, _ = tenants[0]
    types = [
        "Decimal(18, 2)",
        "Nullable(Int64)",
        "LowCardinality(Nullable(Decimal64(4)))",
        "String",
        "String",
        "Int64",
    ]
    columns = [
        {"name": "=alias" if i == 0 else f"c{i}", "type": kind}
        for i, kind in enumerate(types)
    ]
    values = ["-12.50", "-1152921504606846976", "-1E+3", "-1+cmd", " =command", None]
    job = create_job(
        actor, project, state="succeeded", result={"columns": columns, "rows": [values]}
    )
    payload, _ = service.export_result(actor, project.id, job.id, "csv")
    header, row = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    assert header[0] == "'=alias"
    assert row == values[:3] + ["'-1+cmd", "' =command", ""]


def test_reuse_receipt_is_per_submission_and_can_be_disabled(tenants):
    actor, project, _ = tenants[0]
    now = timezone.now()
    args = {
        "project_id": project.id,
        "sql": "SELECT count() FROM spans",
        "start": now - timedelta(hours=1),
        "end": now,
    }
    with patch.object(service, "connection_config"):
        fresh = service.submit_receipt(actor, **args)
        assert fresh.reused is False
        SQLExecution.objects.filter(id=fresh.job.id).update(
            state="succeeded", result=result(), finished_at=now
        )
        reused = service.submit_receipt(actor, **args)
        assert reused.reused and reused.job.id == fresh.job.id
        assert service.snapshot(reused.job)["finished_at"] == now
        assert "reused" not in service.snapshot(reused.job)
        again = service.submit_receipt(actor, reuse=False, **args)
        assert not again.reused and again.job.id != fresh.job.id


@pytest.mark.parametrize(
    "params", [{"offset": -1}, {"offset": 10001}, {"limit": 0}, {"limit": 1001}]
)
def test_result_page_validates_bounds(tenants, params):
    actor, project, user = tenants[0]
    job = create_job(actor, project)
    response = SQLQueryViewSet.as_view({"get": "retrieve"})(
        api_request(
            actor, user, "get", "/result", {"project_id": str(project.id), **params}
        ),
        pk=str(job.id),
    )
    assert response.status_code == 400


def test_payload_cleanup_uses_partial_index_and_keeps_erased_audit(tenants):
    from django.db import connection, transaction

    actor, project, _ = tenants[0]
    now = timezone.now()
    erased = create_job(
        actor, project, state="succeeded", expires_at=now - timedelta(hours=1)
    )
    retained = create_job(
        actor,
        project,
        state="succeeded",
        result=result(),
        expires_at=now - timedelta(hours=1),
    )
    candidates = SQLExecution.objects.filter(
        state__in=service.TERMINAL_STATES, output_bytes__gt=0, expires_at__lte=now
    ).order_by("expires_at")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        assert "sql_payload_expiry_idx" in candidates.explain()
    assert service.purge_expired_results()["expired_result_bodies"] == 1
    assert service.purge_expired_results()["expired_result_bodies"] == 0
    for job in (erased, retained):
        job.refresh_from_db()
        assert job.output_bytes == 0 and job.result == {}


def test_cleanup_migration_repairs_legacy_zero_byte_payloads(tenants):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    actor, project, _ = tenants[0]
    job = create_job(actor, project, state="succeeded", result=result(), output_bytes=0)
    migration = import_module("tracer.migrations.0122_sql_payload_expiry_index")
    migration.repair_payload_bytes(apps, SimpleNamespace(connection=connection))
    job.refresh_from_db()
    assert job.output_bytes > 0 and job.result == result()
    before = job.output_bytes
    migration.repair_payload_bytes(apps, SimpleNamespace(connection=connection))
    job.refresh_from_db()
    assert job.output_bytes == before
