import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationGroupingStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
)
from tracer.models.trace_scan import (
    ScanIssueConfidence,
    TraceScanIssue,
    TraceScanResult,
    TraceScanStatus,
)
from tracer.services.trace_investigation_grouping import (
    OmegaGroupingOperationalError,
    publish_omega_investigation_grouping,
    publish_pending_omega_investigation_groups,
)
from tracer.utils import feed as feed_service


def _cluster(project, cluster_id="S-OMEGA01"):
    return TraceErrorGroup.no_workspace_objects.create(
        project=project,
        cluster_id=cluster_id,
        source=ClusterSource.SCANNER,
        issue_group="outcome",
        issue_category="outcome",
        fix_layer="",
        title="Executed amount differs from the request",
        status=FeedIssueStatus.ESCALATING,
        error_type="outcome",
        error_count=1,
        total_events=1,
        unique_traces=1,
        first_seen=timezone.now(),
        last_seen=timezone.now(),
    )


def _old_issue(projection, cluster):
    issue = TraceScanIssue.no_workspace_objects.create(
        scan_result=projection,
        category="outcome",
        group="outcome",
        fix_layer="",
        confidence=ScanIssueConfidence.MEDIUM,
        brief="Old scanner wording",
        cluster=cluster,
    )
    membership = ErrorClusterTraces.no_workspace_objects.create(
        cluster=cluster,
        trace_id=projection.trace_id,
        scan_issue=issue,
    )
    return issue, membership


def _report(
    project,
    *,
    findings=True,
    execution_status="completed",
    outcome=None,
):
    trace_id = uuid.uuid4()
    job = TraceInvestigationJob.no_workspace_objects.create(
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project=project,
        trace_id=trace_id,
        root_span_id="0123456789abcdef",
        root_end_time=timezone.now(),
        generation=1,
        state=TraceInvestigationJobState.COMPLETED,
        not_before=timezone.now(),
    )
    attempt = TraceInvestigationAttempt.no_workspace_objects.create(
        job=job,
        generation=1,
        worker_id="omega-worker-test",
        engine_version="omega-v1",
        status=TraceInvestigationAttemptStatus.COMPLETED,
        lease_token_digest="a" * 64,
        lease_expires_at=timezone.now(),
        read_cutoff=timezone.now(),
        memory_snapshot_id="snapshot-1",
        memory_digest=f"sha256:{'b' * 64}",
        memory=[],
        limits={},
        completed_at=timezone.now(),
    )
    report_id = uuid.uuid4()
    finding_rows = (
        [
            {
                "finding_id": "finding-1",
                "kind": "outcome",
                "statement": "Executed amount differs from the request",
            }
        ]
        if findings
        else []
    )
    occurrences = (
        [
            {
                "occurrence_id": str(uuid.uuid5(report_id, "finding-1")),
                "finding_id": "finding-1",
            }
        ]
        if findings
        else []
    )
    grouping_status = (
        TraceInvestigationGroupingStatus.PENDING
        if findings
        else TraceInvestigationGroupingStatus.NOT_REQUIRED
    )
    projection = TraceScanResult.no_workspace_objects.create(
        trace_id=trace_id,
        project=project,
        status=(
            TraceScanStatus.COMPLETED
            if execution_status == "completed"
            else TraceScanStatus.FAILED
        ),
        has_issues=bool(findings),
        key_moments=[],
        meta={
            "omega_report_id": str(report_id),
            "grouping_status": grouping_status,
        },
        scan_version="omega-v1",
    )
    report = TraceInvestigationReport.no_workspace_objects.create(
        id=report_id,
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project=project,
        job=job,
        attempt=attempt,
        idempotency_key=f"report-{report_id}",
        result_digest=f"sha256:{'c' * 64}",
        result={
            "execution_status": execution_status,
            "outcome": outcome or ("failure" if findings else "success"),
            "findings": finding_rows,
        },
        occurrences=occurrences,
        grouping_status=grouping_status,
        active_projection_updated=True,
    )
    return report, job, projection


def _mock_vector_db(*, write_error=None):
    db = MagicMock()
    db.execute_read.return_value = []
    if write_error is not None:
        db.client.execute.side_effect = write_error
    return db


def _existing_cluster_pipeline(cluster, *, write_error=None):
    db = _mock_vector_db(write_error=write_error)
    return (
        patch("tracer.utils.trace_scanner.distill_scan_briefs"),
        patch("tracer.utils.trace_scanner.embed_texts", return_value=[[0.1, 0.2]]),
        patch(
            "tracer.utils.trace_scanner.find_nearest_centroid",
            return_value=(cluster.cluster_id, 0.01),
        ),
        patch("tracer.queries.scan_clustering.ClickHouseVectorDB", return_value=db),
    )


@pytest.mark.django_db
def test_groups_through_existing_pipeline_and_feed_readback(observe_project):
    report, _, projection = _report(observe_project, outcome="success")
    cluster = _cluster(observe_project)
    old_issue, old_membership = _old_issue(projection, cluster)
    distill, embed, nearest, vector_db = _existing_cluster_pipeline(cluster)

    with distill, embed, nearest, vector_db:
        assert publish_omega_investigation_grouping(report.id) == "completed"

    report.refresh_from_db()
    projection.refresh_from_db()
    old_issue.refresh_from_db()
    old_membership.refresh_from_db()
    scan_issue_id = report.occurrences[0]["scan_issue_id"]
    current_issue = TraceScanIssue.no_workspace_objects.get(id=scan_issue_id)
    membership = ErrorClusterTraces.no_workspace_objects.get(
        cluster=cluster, trace_id=projection.trace_id
    )

    assert old_issue.deleted is True
    assert old_membership.deleted is False
    assert membership.id == old_membership.id
    assert str(membership.scan_issue_id) == scan_issue_id
    assert current_issue.cluster_id == cluster.id
    assert current_issue.category == current_issue.group == "outcome"
    assert current_issue.fix_layer == ""
    assert current_issue.confidence == ScanIssueConfidence.MEDIUM
    assert report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED
    assert projection.meta["grouping_adapter"] == "scanner_compatibility_v1"

    # Exercise the same selector/service the Feed list API uses. CH enrichments
    # are orthogonal to publication and are stubbed at their query boundary.
    with (
        patch("tracer.queries.feed._fetch_users_affected_batch", return_value={}),
        patch("tracer.queries.feed._fetch_sessions_batch", return_value={}),
        patch("tracer.queries.feed._fetch_latest_trace_id_batch", return_value={}),
    ):
        feed = feed_service.list_feed_issues(
            project_ids=[str(observe_project.id)], source=ClusterSource.SCANNER
        )
    assert feed.total == 1
    assert feed.data[0].error.name == cluster.title
    assert feed.data[0].occurrences == 1
    assert feed.data[0].trace_count == 1

    with patch(
        "tracer.services.trace_investigation_grouping.cluster_issues"
    ) as cluster_again:
        assert publish_omega_investigation_grouping(report.id) == "completed"
    cluster_again.assert_not_called()
    assert (
        TraceScanIssue.no_workspace_objects.filter(scan_result=projection).count() == 1
    )


@pytest.mark.django_db
def test_no_centroid_match_creates_standard_scanner_feed_cluster(observe_project):
    report, _, projection = _report(observe_project)
    db = _mock_vector_db()
    with (
        patch("tracer.utils.trace_scanner.distill_scan_briefs"),
        patch("tracer.utils.trace_scanner.embed_texts", return_value=[[0.1, 0.2]]),
        patch("tracer.utils.trace_scanner.find_nearest_centroid", return_value=None),
        patch("tracer.queries.scan_clustering._seed_severity", return_value=None),
        patch("tracer.queries.scan_clustering.ensure_centroid_table"),
        patch("tracer.queries.scan_clustering.ClickHouseVectorDB", return_value=db),
    ):
        assert publish_omega_investigation_grouping(report.id) == "completed"

    report.refresh_from_db()
    issue = TraceScanIssue.no_workspace_objects.get(
        id=report.occurrences[0]["scan_issue_id"]
    )
    cluster = issue.cluster
    assert cluster is not None
    assert cluster.source == ClusterSource.SCANNER
    assert cluster.issue_group == cluster.issue_category == "outcome"
    assert cluster.title == "Executed amount differs from the request"
    assert cluster.error_count == cluster.unique_traces == 1
    assert ErrorClusterTraces.no_workspace_objects.filter(
        cluster=cluster,
        trace_id=projection.trace_id,
        scan_issue=issue,
    ).exists()


@pytest.mark.django_db
def test_clickhouse_failure_rolls_back_assignment_and_retries(observe_project):
    report, _, projection = _report(observe_project)
    cluster = _cluster(observe_project)
    old_issue, old_membership = _old_issue(projection, cluster)
    distill, embed, nearest, vector_db = _existing_cluster_pipeline(
        cluster, write_error=RuntimeError("clickhouse unavailable")
    )

    with (
        distill,
        embed,
        nearest,
        vector_db,
        pytest.raises(OmegaGroupingOperationalError),
    ):
        publish_omega_investigation_grouping(report.id)

    report.refresh_from_db()
    old_issue.refresh_from_db()
    old_membership.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.PENDING
    assert "scan_issue_id" not in report.occurrences[0]
    assert old_issue.deleted is False
    assert old_issue.cluster_id == cluster.id
    assert old_membership.scan_issue_id == old_issue.id
    assert (
        TraceScanIssue.no_workspace_objects.filter(
            scan_result=projection, cluster__isnull=True
        ).count()
        == 0
    )

    distill, embed, nearest, vector_db = _existing_cluster_pipeline(cluster)
    with distill, embed, nearest, vector_db:
        assert publish_omega_investigation_grouping(report.id) == "completed"


@pytest.mark.django_db
def test_newer_generation_retires_only_staged_issues(observe_project):
    report, job, projection = _report(observe_project)
    cluster = _cluster(observe_project)
    old_issue, old_membership = _old_issue(projection, cluster)
    distill, embed, nearest, vector_db = _existing_cluster_pipeline(
        cluster, write_error=RuntimeError("clickhouse unavailable")
    )
    with (
        distill,
        embed,
        nearest,
        vector_db,
        pytest.raises(OmegaGroupingOperationalError),
    ):
        publish_omega_investigation_grouping(report.id)
    report.refresh_from_db()

    job.generation = 2
    job.state = TraceInvestigationJobState.WAITING
    job.save(update_fields=["generation", "state", "updated_at"])
    assert publish_omega_investigation_grouping(report.id) == "stale"

    report.refresh_from_db()
    old_issue.refresh_from_db()
    old_membership.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.STALE
    assert old_issue.deleted is False
    assert old_membership.deleted is False
    assert (
        TraceScanIssue.no_workspace_objects.filter(
            scan_result=projection, cluster__isnull=True
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_completed_success_without_findings_atomically_clears_old(observe_project):
    report, _, projection = _report(observe_project, findings=False)
    cluster = _cluster(observe_project)
    old_issue, membership = _old_issue(projection, cluster)

    assert publish_omega_investigation_grouping(report.id) == "completed"

    report.refresh_from_db()
    projection.refresh_from_db()
    old_issue.refresh_from_db()
    membership.refresh_from_db()
    cluster.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.COMPLETED
    assert projection.has_issues is False
    assert old_issue.deleted is True
    assert membership.deleted is True
    assert cluster.error_count == cluster.unique_traces == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("execution_status", "outcome"),
    [("failed", "unknown"), ("completed", "unknown")],
)
def test_failed_or_unknown_zero_finding_report_preserves_old_projection(
    observe_project, execution_status, outcome
):
    report, _, projection = _report(
        observe_project,
        findings=False,
        execution_status=execution_status,
        outcome=outcome,
    )
    cluster = _cluster(observe_project)
    old_issue, membership = _old_issue(projection, cluster)

    assert publish_omega_investigation_grouping(report.id) == "failed"

    report.refresh_from_db()
    projection.refresh_from_db()
    old_issue.refresh_from_db()
    membership.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.FAILED
    assert projection.has_issues is True
    assert projection.meta["grouping_status"] == "failed"
    assert old_issue.deleted is False
    assert membership.deleted is False


@pytest.mark.django_db
def test_cross_project_projection_is_never_mutated(
    observe_project, organization, workspace
):
    report, _, projection = _report(observe_project)
    foreign_project = Project.no_workspace_objects.create(
        name="Foreign Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
        metadata={},
        session_config=[],
    )
    projection.project = foreign_project
    projection.save(update_fields=["project", "updated_at"])
    foreign_cluster = _cluster(foreign_project, "S-FOREIGN1")
    foreign_issue, membership = _old_issue(projection, foreign_cluster)

    assert publish_omega_investigation_grouping(report.id) == "stale"

    report.refresh_from_db()
    projection.refresh_from_db()
    foreign_issue.refresh_from_db()
    membership.refresh_from_db()
    assert report.grouping_status == TraceInvestigationGroupingStatus.STALE
    assert projection.project_id == foreign_project.id
    assert foreign_issue.deleted is False
    assert membership.deleted is False


@pytest.mark.django_db
def test_pending_sweep_is_bounded_and_validates_limit(observe_project):
    _report(observe_project, findings=False)
    summary = publish_pending_omega_investigation_groups(report_limit=1)
    assert summary == {
        "selected": 1,
        "completed": 1,
        "stale": 0,
        "failed": 0,
        "deferred": 0,
    }
    with pytest.raises(ValueError, match="between 1 and 100"):
        publish_pending_omega_investigation_groups(report_limit=True)
