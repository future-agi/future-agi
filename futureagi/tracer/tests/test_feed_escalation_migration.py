"""Real pre-escalation upgrades and bounded, repeatable legacy resets."""

import importlib
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tracer.tests.test_feed_escalation_policy import T


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_upgrade_resets_legacy_escalating_and_preserves_other_statuses(
    observe_project, monkeypatch, settings
):
    from simulate.models import RunTest, TestExecution
    from tracer.models.trace_error_analysis import TraceErrorGroup
    from tracer.models.trace_scan import TraceScanIssue
    from tracer.services.feed_escalation import evaluate_issue, sweep_issues

    settings.ERROR_FEED_ESCALATION_MODE = "enabled"
    settings.ERROR_FEED_ESCALATION_WORKSPACE_IDS = ("*",)
    settings.ERROR_FEED_ESCALATION_SOURCES = ("scanner",)
    migration = importlib.import_module(
        "tracer.migrations.0118_backfill_feed_issue_statuses"
    )
    # More than one batch exercises cursor progression and restart safety.
    monkeypatch.setattr(migration, "BATCH", 2)
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    assert not executor.loader.detect_conflicts()
    run = RunTest.no_workspace_objects.create(
        name="Upgrade scope",
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
    )
    execution = TestExecution.no_workspace_objects.create(run_test=run)
    executor.migrate([("tracer", "0116_error_feed_alert_models")])
    try:
        old_apps = executor.loader.project_state(
            [("tracer", "0116_error_feed_alert_models")]
        ).apps
        Issue = old_apps.get_model("tracer", "TraceErrorGroup")
        Scan = old_apps.get_model("tracer", "TraceScanResult")
        Finding = old_apps.get_model("tracer", "TraceScanIssue")
        statuses = ("escalating", "for_review", "acknowledged", "resolved")
        issues = {
            status: Issue.objects.create(
                project_id=observe_project.pk,
                cluster_id=f"UPGRADE-{status}",
                error_type="tool",
                status=status,
            )
            for status in statuses
        }
        defaults = [
            Issue.objects.create(
                project_id=observe_project.pk,
                cluster_id=f"UPGRADE-DEFAULT-{index}",
                error_type="tool",
            )
            for index in range(3)
        ]
        assert all(issue.status == "escalating" for issue in defaults)
        deleted = Issue.objects.create(
            project_id=observe_project.pk,
            cluster_id="UPGRADE-DELETED",
            error_type="tool",
            deleted=True,
        )
        simulation = Issue.objects.create(
            project_id=observe_project.pk,
            cluster_id="UPGRADE-SIMULATION",
            error_type="tool",
            target_type="simulation",
            test_execution_id=execution.pk,
        )
        scan = Scan.objects.create(
            project_id=observe_project.pk,
            trace_id=uuid.uuid4(),
            status="completed",
            has_issues=True,
        )
        # Actual old-schema findings have no occurrence timestamp fields.
        for _ in range(20):
            Finding.objects.create(
                scan_result_id=scan.pk,
                cluster_id=issues["escalating"].pk,
                category="tool",
                group="Tools",
                fix_layer="Tools",
                brief="Old finding",
            )
        executor = MigrationExecutor(connection)
        executor.migrate([("tracer", "0117_feed_escalation_policy")])
        # New writers record ownership; a manual edit during rollout must survive.
        manual = TraceErrorGroup.no_workspace_objects.create(
            project=observe_project,
            cluster_id="UPGRADE-NEW-MANUAL",
            error_type="tool",
            status="escalating",
            status_origin="manual",
        )
        system = TraceErrorGroup.no_workspace_objects.create(
            project=observe_project,
            cluster_id="UPGRADE-NEW-SYSTEM",
            error_type="tool",
            status="escalating",
            status_origin="system",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        reset_ids = [issues["escalating"].pk, *(issue.pk for issue in defaults)]
        for issue in TraceErrorGroup.no_workspace_objects.filter(pk__in=reset_ids):
            assert issue.status == "for_review"
            assert issue.status_origin == "system"
            assert issue.status_revision == 1
            assert issue.status_changed_at is not None
            assert issue.status_changed_by_id is None
            assert issue.escalation_state == {}
            assert issue.escalation_next_evaluation_at is not None
        for status in statuses[1:]:
            issue = TraceErrorGroup.no_workspace_objects.get(pk=issues[status].pk)
            assert issue.status == status
            assert issue.status_origin == "legacy_unknown"
            assert issue.status_revision == 0
        for old in (deleted, simulation, manual, system):
            issue = TraceErrorGroup.all_objects.get(pk=old.pk)
            assert issue.status == "escalating"
            assert issue.status_revision == 0
        cluster = TraceErrorGroup.no_workspace_objects.get(pk=issues["escalating"].pk)
        assert (
            TraceScanIssue.no_workspace_objects.filter(
                cluster=cluster,
                occurred_at__isnull=True,
            ).count()
            == 20
        )

        # Runtime preserves the reset while timestamps are missing, then repairs
        # real source times and requires fresh confirmation before escalating.
        monkeypatch.setattr(
            "tracer.services.feed_escalation_evidence.capture_occurrence",
            lambda **kwargs: (None, ""),
        )
        now = T + timedelta(minutes=10)
        assert evaluate_issue(cluster.pk, now=now) == "error"
        assert evaluate_issue(cluster.pk, now=now) == "error"
        cluster.refresh_from_db()
        assert cluster.status == "for_review"
        assert cluster.escalation_state["evaluation_error"] == "CountUnavailable"
        monkeypatch.setattr(
            "tracer.services.feed_escalation_evidence.capture_occurrence",
            lambda **kwargs: (T - timedelta(minutes=20), "trace_end"),
        )
        monkeypatch.setattr(
            "tracer.services.feed_escalation._wake_issue", lambda pk: None
        )
        assert evaluate_issue(cluster.pk, now=now) == "stale"
        assert set(
            TraceScanIssue.no_workspace_objects.filter(cluster=cluster).values_list(
                "occurred_at",
                flat=True,
            )
        ) == {T - timedelta(minutes=20)}
        for minute in (0, 5, 10, 15):
            sweep_issues(now=now + timedelta(minutes=minute))
            cluster.refresh_from_db()
            assert cluster.status == ("escalating" if minute == 15 else "for_review")
        before = cluster.status_revision, cluster.escalation_state
        apps = executor.loader.project_state(latest).apps
        with connection.schema_editor(atomic=False) as editor:
            migration.backfill_feed_issue_statuses(apps, editor)
        cluster.refresh_from_db()
        assert cluster.status == "escalating"
        assert (cluster.status_revision, cluster.escalation_state) == before
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.integration
@pytest.mark.django_db
def test_old_writer_can_insert_after_additive_schema_without_claiming_system_ownership(
    observe_project,
):
    import uuid

    from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
    from tracer.models.trace_scan import TraceScanIssue, TraceScanResult

    executor = MigrationExecutor(connection)
    old_apps = executor.loader.project_state(
        [("tracer", "0110_expand_investigation_requirement_id")]
    ).apps
    old_issue = old_apps.get_model("tracer", "TraceErrorGroup").objects.create(
        project_id=observe_project.pk,
        cluster_id="OLD-WRITER",
        error_type="tool",
    )
    current = TraceErrorGroup.no_workspace_objects.get(pk=old_issue.pk)
    assert current.status_origin == "legacy_unknown"
    assert current.status_revision == 0
    assert current.escalation_state == {}
    scan = TraceScanResult.no_workspace_objects.create(
        project=observe_project, trace_id=uuid.uuid4(), status="completed"
    )
    old_finding = old_apps.get_model("tracer", "TraceScanIssue").objects.create(
        scan_result_id=scan.pk,
        cluster_id=old_issue.pk,
        category="tool",
        group="Tools",
        fix_layer="Tools",
        brief="Legacy writer",
    )
    old_member = old_apps.get_model("tracer", "ErrorClusterTraces").objects.create(
        cluster_id=old_issue.pk,
        trace_id=scan.trace_id,
        scan_issue_id=old_finding.pk,
    )
    assert (
        TraceScanIssue.no_workspace_objects.get(
            pk=old_finding.pk
        ).occurrence_time_source
        == ""
    )
    assert (
        ErrorClusterTraces.no_workspace_objects.get(
            pk=old_member.pk
        ).occurrence_time_source
        == ""
    )
