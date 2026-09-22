"""F6 Feed reads use current findings, not raw junction row cardinality."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    Priority,
    TraceErrorGroup,
)
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope
from tracer.models.trace_investigation import (
    TraceInvestigationEvidenceReceipt,
    TraceInvestigationFinding,
    TraceInvestigationFindingEvidence,
    TraceInvestigationGroupingStatus,
)
from tracer.queries import feed, scan_clustering
from tracer.tests.test_grouping_snapshot import _saved_report
from tracer.types.feed_types import FeedUpdatePayload
from tracer.views.feed.linear_issue_view import CreateLinearIssueView

pytestmark = pytest.mark.django_db


@pytest.fixture
def omega_issue(observe_project):
    report = _saved_report(observe_project)
    report.grouping_status = TraceInvestigationGroupingStatus.COMPLETED
    report.save(update_fields=["grouping_status", "updated_at"])
    scope = TraceGroupingScope.objects.create(
        project=observe_project,
        organization_id=observe_project.organization_id,
        workspace_id=observe_project.workspace_id,
    )
    cluster = TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="F6-FEED-1",
        error_type="investigation_finding",
        issue_group="Investigation findings",
        title="Incorrect refund amount",
        error_count=1,
        total_events=1,
        unique_traces=1,
    )
    state = TraceGroupingIssueState.objects.create(scope=scope, cluster=cluster)
    finding = report.findings.get(finding_id="finding-1")
    finding.cluster = cluster
    finding.save(update_fields=["cluster", "updated_at"])
    ErrorClusterTraces.objects.create(
        cluster=cluster, trace_id=report.trace_id, finding=finding
    )
    return report, cluster, state, finding


def test_f6_membership_requires_current_completed_matching_finding(omega_issue):
    report, cluster, state, finding = omega_issue
    members = feed._current_memberships().filter(cluster=cluster)
    assert members.count() == 1

    report.grouping_status = TraceInvestigationGroupingStatus.PENDING
    report.save(update_fields=["grouping_status", "updated_at"])
    assert members.count() == 0
    report.grouping_status = TraceInvestigationGroupingStatus.COMPLETED
    report.save(update_fields=["grouping_status", "updated_at"])

    finding.cluster = None
    finding.save(update_fields=["cluster", "updated_at"])
    assert members.count() == 0
    finding.cluster = cluster
    finding.save(update_fields=["cluster", "updated_at"])

    member = ErrorClusterTraces.objects.get(finding=finding)
    member.trace_id = uuid.uuid4()
    member.save(update_fields=["trace", "updated_at"])
    assert members.count() == 0
    member.trace_id = report.trace_id
    member.save(update_fields=["trace", "updated_at"])

    state.dirty = True
    state.save(update_fields=["dirty", "updated_at"])
    assert members.count() == 0
    assert not feed._base_qs([str(cluster.project_id)]).filter(pk=cluster.pk).exists()
    assert not feed._cluster_qs_for_access(
        cluster.cluster_id, [str(cluster.project_id)]
    ).exists()
    state.dirty = False
    state.save(update_fields=["dirty", "updated_at"])
    assert members.count() == 1

    report.is_current = False
    report.save(update_fields=["is_current", "updated_at"])
    assert members.count() == 0


def test_unmarked_legacy_and_eval_memberships_keep_their_units(omega_issue):
    _report, omega_cluster, _state, _finding = omega_issue
    legacy = TraceErrorGroup.objects.create(
        project=omega_cluster.project,
        cluster_id="LEGACY-FEED-1",
        error_type="legacy",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    eval_group = TraceErrorGroup.objects.create(
        project=omega_cluster.project,
        cluster_id="EVAL-FEED-1",
        error_type="eval",
        issue_group="Test eval",
        source=ClusterSource.EVAL,
    )
    legacy_member = ErrorClusterTraces.objects.create(
        cluster=legacy, trace_id=uuid.uuid4()
    )
    eval_member = ErrorClusterTraces.objects.create(
        cluster=eval_group, trace_session_id=uuid.uuid4()
    )

    assert feed._current_memberships().filter(pk=legacy_member.pk).exists()
    assert feed._current_memberships().filter(pk=eval_member.pk).exists()
    legacy_member.deleted = True
    legacy_member.save(update_fields=["deleted", "updated_at"])
    assert not feed._current_memberships().filter(pk=legacy_member.pk).exists()


def test_f6_pages_distinct_traces_before_offset_and_keeps_missing_ch(omega_issue):
    report, cluster, _state, _first = omega_issue
    for ordinal in range(1, 101):
        finding = TraceInvestigationFinding.objects.create(
            id=uuid.uuid4(),
            report=report,
            finding_id=f"extra-{ordinal}",
            ordinal=ordinal,
            statement=f"Finding {ordinal}",
            cluster=cluster,
        )
        ErrorClusterTraces.objects.create(
            cluster=cluster, trace_id=report.trace_id, finding=finding
        )

    project_id = str(cluster.project_id)
    with (
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_get_trace_totals_batch", return_value={}),
        patch.object(feed, "_get_trace_scores_batch", return_value={}),
        patch.object(feed, "_get_investigation_reports_batch", return_value={}),
        patch.object(feed, "_session_judges_batch", return_value={}),
    ):
        first_page, total = feed._fetch_trace_rows(
            cluster.cluster_id, project_id, limit=1, offset=0
        )
        second_page, second_total = feed._fetch_trace_rows(
            cluster.cluster_id, project_id, limit=1, offset=1
        )

    assert total == second_total == 1
    assert [row.id for row in first_page] == [str(report.trace_id)]
    assert first_page[0].input is None  # CH content unavailable, member still visible
    assert second_page == []

    member = feed._members_with_occurrence_time().filter(cluster=cluster).first()
    assert member.occurrence_at == report.recorded_at
    with (
        patch.object(feed, "_avg_eval_score", return_value=None),
        patch.object(feed, "_users_affected_in_window", return_value=0),
    ):
        metrics = feed._fetch_trend_metrics(cluster.cluster_id, project_id, days=14)
    assert metrics[0].value == "100%"  # 101 findings, one distinct failing trace


def test_f6_reel_uses_only_evidence_linked_to_selected_issue(omega_issue):
    report, cluster, scope, finding = omega_issue
    other_cluster = TraceErrorGroup.objects.create(
        project=cluster.project,
        cluster_id="F6-FEED-2",
        error_type="investigation_finding",
        issue_group="Investigation findings",
    )
    TraceGroupingIssueState.objects.create(scope=scope.scope, cluster=other_cluster)
    other_finding = TraceInvestigationFinding.objects.create(
        id=uuid.uuid4(),
        report=report,
        finding_id="unrelated",
        ordinal=1,
        statement="Different mechanism",
        cluster=other_cluster,
    )
    other_receipt = TraceInvestigationEvidenceReceipt.objects.create(
        report=report,
        evidence_id="unrelated-receipt",
        ordinal=2,
        span_id="other-span",
        excerpt="Evidence for the different issue",
    )
    TraceInvestigationFindingEvidence.objects.create(
        finding=other_finding, evidence=other_receipt
    )
    ErrorClusterTraces.objects.create(
        cluster=other_cluster, trace_id=report.trace_id, finding=other_finding
    )

    selected = feed._cluster_evidence_by_trace(
        cluster.cluster_id, str(cluster.project_id), [str(report.trace_id)]
    )
    reel = feed._investigation_reel(
        report, selected_receipts=selected[str(report.trace_id)]
    )

    assert [step["raw"] for step in reel] == ["requested=100; executed=10"]
    assert finding.id != other_finding.id


def test_brief_derived_cards_ignore_unattached_or_dirty_f6_findings(omega_issue):
    report, cluster, state, finding = omega_issue
    TraceInvestigationFinding.objects.create(
        id=uuid.uuid4(),
        report=report,
        finding_id="unattached",
        ordinal=1,
        statement="Stale unrelated issue text",
        cluster=cluster,
    )

    cluster_ids, docs = feed._project_cluster_briefs_corpus(str(cluster.project_id))
    assert cluster_ids == [cluster.cluster_id]
    assert docs == [finding.statement]

    state.dirty = True
    state.save(update_fields=["dirty", "updated_at"])
    assert feed._project_cluster_briefs_corpus(str(cluster.project_id)) == ([], [])


def test_feed_human_edits_fence_f6_but_leave_legacy_updates_alone(omega_issue, user):
    _report, cluster, state, _finding = omega_issue
    scope = state.scope
    old_revision = state.revision
    old_registry = scope.registry_revision
    payload = FeedUpdatePayload(
        status=FeedIssueStatus.ACKNOWLEDGED,
        severity="high",
        assignee=user.email,
        assignee_provided=True,
    )
    with patch.object(feed, "get_cluster_detail", return_value="detail"):
        assert (
            feed.update_cluster(cluster.cluster_id, [str(cluster.project_id)], payload)
            == "detail"
        )
    cluster.refresh_from_db()
    state.refresh_from_db()
    scope.refresh_from_db()
    assert (cluster.status, cluster.priority, cluster.assignee_id) == (
        FeedIssueStatus.ACKNOWLEDGED,
        Priority.HIGH,
        user.id,
    )
    assert state.protected is True
    assert state.revision == old_revision + 1
    assert scope.registry_revision == old_registry + 1

    with patch.object(feed, "get_cluster_detail", return_value="detail"):
        feed.update_cluster(
            cluster.cluster_id,
            [str(cluster.project_id)],
            FeedUpdatePayload(assignee=None, assignee_provided=True),
        )
    cluster.refresh_from_db()
    state.refresh_from_db()
    scope.refresh_from_db()
    assert cluster.assignee_id is None
    assert state.revision == old_revision + 2
    assert scope.registry_revision == old_registry + 2

    legacy = TraceErrorGroup.objects.create(
        project=cluster.project,
        cluster_id="LEGACY-HUMAN-EDIT",
        error_type="legacy",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    with patch.object(feed, "get_cluster_detail", return_value="detail"):
        feed.update_cluster(
            legacy.cluster_id,
            [str(cluster.project_id)],
            FeedUpdatePayload(status=FeedIssueStatus.RESOLVED),
        )
    legacy.refresh_from_db()
    scope.refresh_from_db()
    assert legacy.status == FeedIssueStatus.RESOLVED
    assert scope.registry_revision == old_registry + 2

    state.dirty = True
    state.save(update_fields=["dirty", "updated_at"])
    with patch.object(feed, "get_cluster_detail") as detail:
        assert (
            feed.update_cluster(
                cluster.cluster_id,
                [str(cluster.project_id)],
                FeedUpdatePayload(status=FeedIssueStatus.RESOLVED),
            )
            is None
        )
    detail.assert_not_called()
    cluster.refresh_from_db()
    scope.refresh_from_db()
    assert cluster.status == FeedIssueStatus.ACKNOWLEDGED
    assert scope.registry_revision == old_registry + 2


def test_repeated_severity_edit_does_not_churn_f6_versions(omega_issue, user):
    _report, cluster, state, _finding = omega_issue
    scope = state.scope
    payload = FeedUpdatePayload(
        status=FeedIssueStatus.ACKNOWLEDGED,
        severity="high",
        assignee=user.email,
        assignee_provided=True,
    )
    with patch.object(feed, "get_cluster_detail", return_value="detail"):
        feed.update_cluster(cluster.cluster_id, [str(cluster.project_id)], payload)
        state.refresh_from_db()
        scope.refresh_from_db()
        first_revision = state.revision
        first_registry_revision = scope.registry_revision

        feed.update_cluster(cluster.cluster_id, [str(cluster.project_id)], payload)

    cluster.refresh_from_db()
    state.refresh_from_db()
    scope.refresh_from_db()
    assert cluster.priority == Priority.HIGH
    assert state.protected is True
    assert state.revision == first_revision
    assert scope.registry_revision == first_registry_revision


def test_linear_link_protects_f6_before_mocked_external_call(
    omega_issue, user, monkeypatch
):
    _report, cluster, state, _finding = omega_issue
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)
    scope = state.scope
    issue = {
        "url": "https://linear.app/example/issue/TEAM-123",
        "identifier": "TEAM-123",
        "title": "Investigate refund mismatch",
    }
    with (
        patch(
            "integrations.models.integration_connection.IntegrationConnection.objects.filter"
        ) as connections,
        patch(
            "tracer.views.feed.linear_issue_view.CredentialManager.decrypt",
            return_value={"token": "mock"},
        ),
        patch(
            "integrations.services.linear_service.LinearService.create_issue",
            return_value=issue,
        ) as create_issue,
    ):
        connections.return_value.exclude.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            encrypted_credentials="mock"
        )
        request = APIRequestFactory().post(
            f"/tracer/feed/issues/{cluster.cluster_id}/create-linear-issue/",
            {"team_id": "team-local-test"},
            format="json",
        )
        force_authenticate(request, user=user)
        response = CreateLinearIssueView.as_view()(
            request, cluster_id=cluster.cluster_id
        )

    assert response.status_code == 200
    create_issue.assert_called_once()
    cluster.refresh_from_db()
    state.refresh_from_db()
    scope.refresh_from_db()
    assert (cluster.external_issue_url, cluster.external_issue_id) == (
        issue["url"],
        issue["identifier"],
    )
    assert state.protected is True
    assert state.revision == 3  # intent fence and durable link
    assert scope.registry_revision == 2


def test_legacy_scanner_writers_cannot_retitle_or_join_f6_issue(omega_issue):
    _report, cluster, _state, _finding = omega_issue
    with (
        patch.object(scan_clustering, "embed_texts") as embed,
        patch.object(scan_clustering, "_seed_severity") as severity,
    ):
        scan_clustering._retitle_from_members(cluster)
        scan_clustering._refresh_severity(cluster)
    embed.assert_not_called()
    severity.assert_not_called()
    with pytest.raises(ValueError, match="Omega-owned"):
        scan_clustering.assign_to_cluster(
            cluster.cluster_id, str(cluster.project_id), None, []
        )
    legacy = TraceErrorGroup.objects.create(
        project=cluster.project,
        cluster_id="S-LEGACY",
        error_type="legacy",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )
    assert (
        scan_clustering._merge_pg(
            cluster.cluster_id, legacy.cluster_id, str(cluster.project_id)
        )
        is None
    )
    cluster.refresh_from_db()
    assert cluster.deleted is False


def test_protected_empty_f6_issue_remains_readable_with_zero_members(omega_issue):
    _report, cluster, state, finding = omega_issue
    state.protected = True
    state.save(update_fields=["protected", "updated_at"])
    member = ErrorClusterTraces.objects.get(finding=finding)
    member.deleted = True
    member.save(update_fields=["deleted", "updated_at"])
    finding.cluster = None
    finding.save(update_fields=["cluster", "updated_at"])
    cluster.error_count = cluster.total_events = cluster.unique_traces = 0
    cluster.save(
        update_fields=["error_count", "total_events", "unique_traces", "updated_at"]
    )

    project_id = str(cluster.project_id)
    assert feed._base_qs([project_id]).filter(pk=cluster.pk).exists()
    assert feed._cluster_qs_for_access(cluster.cluster_id, [project_id]).exists()
    assert feed._fetch_trace_rows(cluster.cluster_id, project_id, 10, 0) == ([], 0)
