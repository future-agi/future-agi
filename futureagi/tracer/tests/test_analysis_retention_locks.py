"""Real PostgreSQL lock compatibility and primary-only privacy decisions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

import pytest
from django.db import OperationalError, connection, connections, transaction
from django.db.models import F
from django.utils import timezone

from tracer.models.insights import InsightConfig, InsightRun
from tracer.models.project import Project
from tracer.services import analysis_retention as retention
from tracer.tests.test_analysis_retention import instant, report


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("edge", ["prepare", "instant", "project_delete"])
def test_project_fence_permits_fk_insert_but_serializes_epoch_and_delete(
    observe_project, monkeypatch, edge
):
    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.units import UnitText
    from tracer.services import instant_eval_rows, project_deletion

    monkeypatch.setattr("tfc.ee_gates.forget_analysis_evidence", lambda run_id: None)
    monkeypatch.setattr(
        project_deletion, "_pause_eval_task_workflows", lambda ids: None
    )
    monkeypatch.setattr(
        project_deletion, "publish_project_invalidation", lambda ids: None
    )
    now = timezone.now()
    _, run, unit = report(observe_project, now, status="pending")
    task, _, _ = instant(observe_project, now)
    monkeypatch.setattr(pipeline, "check_setup", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "load_units",
        lambda *args, **kwargs: {
            unit.unit_id: UnitText(
                unit_id=unit.unit_id, content="retained source", source_created_at=now
            )
        },
    )
    seen = []

    def fk_and_mutations():
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '750ms'")
                # Deferred FK validation occurs on commit, while the other
                # connection still holds the actual production project fence.
                child = InsightConfig.objects.create(
                    project_id=observe_project.id,
                    organization_id=observe_project.organization_id,
                    workspace_id=observe_project.workspace_id,
                    name="Concurrent FK insert",
                )
            for change in (
                {"analysis_source_generation": F("analysis_source_generation") + 1},
                {"deleted": True},
            ):
                with pytest.raises(OperationalError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '250ms'")
                        Project._base_manager.filter(pk=observe_project.id).update(
                            **change
                        )
            return child.pk
        finally:
            connections["default"].close()

    table = connection.ops.quote_name(Project._meta.db_table)

    def probe(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            not seen
            and f"FROM {table}" in sql
            and ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql)
        ):
            seen.append(sql)
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(fk_and_mutations).result(timeout=5)
        return result

    with connection.execute_wrapper(probe):
        if edge == "prepare":
            pipeline.prepare(str(run.id))
        elif edge == "instant":
            instant_eval_rows.write_rows(
                task.id, "traces", [], instant_eval_rows.FacetRows(source_generation=0)
            )
        else:
            project_deletion.soft_delete_projects(
                Project._base_manager.filter(pk=observe_project.id), "observe"
            )
    assert len(seen) == 1 and "FOR NO KEY UPDATE" in seen[0]
    # After release the same non-key updates can commit normally.
    assert (
        Project._base_manager.filter(pk=observe_project.id).update(
            analysis_source_generation=F("analysis_source_generation") + 1, deleted=True
        )
        == 1
    )
    observe_project.refresh_from_db()
    assert observe_project.analysis_source_generation == 1 and observe_project.deleted


@pytest.mark.django_db
def test_privacy_checks_bypass_replica_routing(observe_project, monkeypatch):
    from ee.agenthub.insights import billing, pipeline
    from ee.usage.services import entitlements, metering
    from tfc import routers

    now = timezone.now()
    _, running, _ = report(observe_project, now, status="running")
    _, retired, _ = report(observe_project, now)
    InsightRun._base_manager.filter(pk=retired.pk).update(deleted=True)
    _, old, _ = report(observe_project, now - timedelta(days=40))
    monkeypatch.setattr("tfc.ee_gates.forget_analysis_evidence", lambda run_id: None)
    monkeypatch.setattr(
        "tracer.services.project_deletion._pause_eval_task_workflows", lambda ids: None
    )
    monkeypatch.setattr(routers, "_replica_configured", lambda: True)
    monkeypatch.setattr(
        routers, "_opt_in_keys", lambda: {"ALL_MODELS_USE_READ_REPLICA"}
    )
    # No replica alias exists in this test DB; an accidental routed read fails.
    assert routers.ReadReplicaRouter().db_for_read(InsightRun) == "replica"
    monkeypatch.setattr(
        "django.core.cache.cache.get",
        mock.Mock(side_effect=RuntimeError("cache unavailable")),
    )
    assert pipeline.stop_requested(str(running.id)) is False
    monkeypatch.setattr(
        entitlements,
        "get_redis",
        mock.Mock(side_effect=AssertionError("privacy Redis access")),
    )
    monkeypatch.setattr(
        metering,
        "get_redis",
        mock.Mock(side_effect=AssertionError("privacy plan Redis access")),
    )
    monkeypatch.setattr(
        entitlements.BillingConfig,
        "get",
        lambda: mock.Mock(get_entitlement_default=lambda *args: 30),
    )
    assert (
        entitlements.Entitlements.get_retention_days(
            str(observe_project.organization_id), "traces", use_cache=False
        )
        == 30
    )
    charge = mock.Mock(side_effect=AssertionError("retired report must not be billed"))
    monkeypatch.setattr(billing, "charge_run", charge)
    pipeline._settle_written(retired, {"tops": [], "subs": []})
    charge.assert_not_called()
    assert (
        retention.expire_analysis_data(
            organization_id=observe_project.organization_id,
            cutoff=now - timedelta(days=30),
        )["insight_runs"]
        == 1
    )
    assert InsightRun._base_manager.using("default").get(pk=old.pk).deleted


@pytest.mark.django_db
@pytest.mark.parametrize("edge", ["prepare", "instant"])
def test_late_live_child_cannot_materialize_under_deleted_project(
    observe_project, monkeypatch, edge
):
    from ee.agenthub.insights import pipeline
    from ee.agenthub.insights.units import UnitText
    from tracer.models.eval_task import EvalTask
    from tracer.models.insights import InsightUnit
    from tracer.models.instant_eval import InstantEvalRow
    from tracer.services import instant_eval_rows

    monkeypatch.setattr("tfc.ee_gates.forget_analysis_evidence", lambda run_id: None)
    monkeypatch.setattr(
        "tracer.services.project_deletion._pause_eval_task_workflows", lambda ids: None
    )
    Project._base_manager.filter(pk=observe_project.id).update(deleted=True)
    # These new children are FK-valid despite the already-soft-deleted project.
    # Their captured epoch still matches: full-project deletion need not bump it.
    if edge == "prepare":
        _, run, row = report(observe_project, timezone.now(), status="pending")
        InsightUnit.objects.filter(pk=row.pk).update(
            content="", excerpt="", summary="", lens_answer=""
        )
        monkeypatch.setattr(pipeline, "check_setup", lambda: None)
        monkeypatch.setattr(
            pipeline,
            "load_units",
            lambda *args, **kwargs: {
                row.unit_id: UnitText(
                    unit_id=row.unit_id,
                    content="late private text",
                    source_created_at=timezone.now(),
                )
            },
        )
        with pytest.raises(pipeline.InsightRunNotRunning):
            pipeline.prepare(str(run.id))
        assert not InsightUnit.objects.filter(run_id=run.id).exists()
        assert InsightRun._base_manager.get(pk=run.id).deleted
    else:
        task, _, row = instant(observe_project, timezone.now())
        InstantEvalRow.objects.filter(task=task).delete()
        facets = instant_eval_rows.FacetRows(source_generation=0)
        facets[row.row_id] = {
            "time": timezone.now(),
            "source_created_at": timezone.now(),
            "source_session_ids": [],
            "end_user_id": None,
            "user_label": "",
            "model": "",
            "turns": 1,
            "duration_ms": 0,
            "error": False,
            "excerpt": "late private text",
            "trace_id": None,
        }
        assert (
            instant_eval_rows.write_rows(task.id, "traces", [(row.row_id, 0)], facets)
            == 0
        )
        assert not InstantEvalRow.objects.filter(task=task).exists()
        assert EvalTask._base_manager.get(pk=task.id).deleted
