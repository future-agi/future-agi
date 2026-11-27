"""Real pre-escalation upgrades: statuses are kept and open legacy issues join automation."""

import importlib
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tracer.tests.test_feed_escalation_policy import T


def _unapply_feed_escalation(executor):
    """Return tracer to its 0116 shape for the Error Feed escalation branch only.

    Unapplies 0117_feed_escalation_policy and everything that depends on it, in
    the order Django's own backwards migration uses. Other branches after 0116
    stay applied: none of them changes the tables this test writes through the
    0116 models, and some (the attachment fences) are irreversible by design.
    """
    graph = executor.loader.graph
    applied = executor.loader.applied_migrations
    plan = [
        (graph.nodes[key], True)
        for key in graph.backwards_plan(("tracer", "0117_feed_escalation_policy"))
        if key in applied
    ]
    assert plan, "the Error Feed escalation branch is not applied"
    executor.migrate([], plan=plan)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_upgrade_keeps_legacy_statuses_and_automation_adopts_open_issues(
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
    _unapply_feed_escalation(executor)
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
        legacy_ids = [issues["escalating"].pk, *(issue.pk for issue in defaults)]
        for issue in TraceErrorGroup.no_workspace_objects.filter(pk__in=legacy_ids):
            assert issue.status == "escalating"
            assert issue.status_origin == "legacy_unknown"
            assert issue.status_revision == 0
            assert issue.escalation_state == {}
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

        # Runtime keeps the status while timestamps are missing, then repairs real
        # source times, adopts the issue and recovers it only once it is quiet.
        monkeypatch.setattr(
            "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
            lambda project_id, trace_ids: {str(t): (None, "") for t in trace_ids},
        )
        now = T + timedelta(minutes=10)
        assert evaluate_issue(cluster.pk, now=now) == "error"
        assert evaluate_issue(cluster.pk, now=now) == "error"
        cluster.refresh_from_db()
        assert cluster.status == "escalating"
        assert cluster.status_origin == "legacy_unknown"
        assert cluster.escalation_state["evaluation_error"] == "CountUnavailable"
        monkeypatch.setattr(
            "tracer.services.feed_escalation_evidence.capture_trace_occurrences",
            lambda project_id, trace_ids: {
                str(t): (T - timedelta(minutes=20), "trace_end") for t in trace_ids
            },
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
        for minute in range(0, 45, 5):
            sweep_issues(now=now + timedelta(minutes=minute))
            cluster.refresh_from_db()
            assert (cluster.status, cluster.status_origin) == ("escalating", "system")
        for minute in (45, 50, 55):
            sweep_issues(now=now + timedelta(minutes=minute))
        cluster.refresh_from_db()
        assert cluster.status == "for_review"
        assert cluster.status_revision == 1
        before = cluster.status_revision, cluster.escalation_state
        apps = executor.loader.project_state(latest).apps
        with connection.schema_editor(atomic=False) as editor:
            migration.backfill_feed_issue_statuses(apps, editor)
        cluster.refresh_from_db()
        assert cluster.status == "for_review"
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


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_backfill_copies_omega_root_times_across_batches_and_reruns_as_noop(
    observe_project, monkeypatch
):
    from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
    from tracer.models.trace_investigation import TraceInvestigationFinding
    from tracer.tests.test_grouping_snapshot import _saved_report

    migration = importlib.import_module(
        "tracer.migrations.0118_backfill_feed_issue_statuses"
    )
    monkeypatch.setattr(migration, "BATCH", 2)
    cluster = TraceErrorGroup.no_workspace_objects.create(
        project=observe_project, cluster_id="BACKFILL-OMEGA", error_type="finding"
    )
    timed = _saved_report(observe_project, identity="timed")
    untimed = _saved_report(observe_project, identity="untimed")
    untimed.job.root_end_time = None
    untimed.job.save(update_fields=["root_end_time"])

    def member(report, index, **fields):
        finding = TraceInvestigationFinding.no_workspace_objects.create(
            id=uuid.uuid4(),
            report=report,
            finding_id=f"backfill-{index}",
            ordinal=100 + index,
            statement="Backfill defect",
            cluster=cluster,
        )
        return ErrorClusterTraces.no_workspace_objects.create(
            cluster=cluster, finding=finding, trace_id=report.trace_id, **fields
        ).pk

    copied = [member(timed, index) for index in range(5)]  # > BATCH: three batches
    no_root = [member(untimed, index) for index in range(3)]
    already = T - timedelta(days=3)
    kept = member(timed, 9, occurred_at=already, occurrence_time_source="trace_end")
    scanner = ErrorClusterTraces.no_workspace_objects.create(
        cluster=cluster, trace_id=uuid.uuid4()
    ).pk

    def snapshot():
        return {
            row["pk"]: (row["occurred_at"], row["occurrence_time_source"])
            for row in ErrorClusterTraces.no_workspace_objects.filter(
                cluster=cluster
            ).values("pk", "occurred_at", "occurrence_time_source")
        }

    apps = (
        MigrationExecutor(connection)
        .loader.project_state([("tracer", "0118_backfill_feed_issue_statuses")])
        .apps
    )
    with connection.schema_editor(atomic=False) as editor:
        migration.backfill_feed_issue_statuses(apps, editor)
    after = snapshot()
    root_end = timed.job.root_end_time
    assert root_end is not None
    for pk in copied:
        assert after[pk] == (root_end, "omega_root_end")
    for pk in (*no_root, scanner):
        assert after[pk] == (None, "")
    assert after[kept] == (already, "trace_end")

    with connection.schema_editor(atomic=False) as editor:
        migration.backfill_feed_issue_statuses(apps, editor)
    assert snapshot() == after
