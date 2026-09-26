"""DB-backed scope-contract tests for the cluster-RCA selectors.

``selectors.py`` centralizes the agent's entire tenant-safety boundary, and the
agent tests stub the selectors away — so this is the module's own boundary test.

  - ``resolve_cluster_context`` is the CENTERPIECE: it's the [explicit] gate that
    stops the agent from ever *obtaining* a foreign ``cluster_uuid`` to hand to
    the [transitive] selectors. Foreign project → None, by id AND by label.
  - the other [explicit] reads (``get_cluster_for_read`` /
    ``get_scan_issue_for_read`` / ``get_version_for_read``) reject a foreign
    project too.
  - [transitive] selectors are isolated to their ``cluster_uuid`` — this pins a
    WHERE clause (cluster isolation), NOT tenant-safety; the tenant gate is
    ``resolve_cluster_context`` above.
  - the agg selectors return the typed ``CountBucket`` shape.
  - ``trace_eval_results`` is UNSCOPED by contract; its guard is
    ``_read_trace``'s project-scoped spans-gate, pinned in ``TestReadTraceGate``.

The fixture clears the workspace context so the selectors run exactly as they do
in production — a Temporal worker with no ambient workspace — making the explicit
``project_id`` filter the ONLY thing in scope.
"""

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ee.agenthub.cluster_rca import selectors
from model_hub.models.ai_model import AIModel
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.models.trace_grouping import TraceGroupingIssueState, TraceGroupingScope
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationFinding,
    TraceInvestigationJob,
    TraceInvestigationReport,
)


@pytest.fixture
def tenants(db):
    """Two isolated tenants (home, foreign); workspace context cleared so the
    selectors' only scope is their explicit project_id, as in the agent."""
    user = User.objects.create_user(
        email=f"sel-{uuid.uuid4().hex[:8]}@futureagi.com", password="x", name="Sel"
    )

    def _project(name):
        org = Organization.objects.create(name=f"{name} Org")
        ws = Workspace.objects.create(
            name=f"{name} WS",
            organization=org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        return Project.objects.create(
            name=f"{name} Project",
            organization=org,
            workspace=ws,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )

    home, foreign = _project("Home"), _project("Foreign")
    clear_workspace_context()
    return home, foreign


def _cluster(project, label):
    now = timezone.now()
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=label,
        error_type=f"{label}-err",
        source=ClusterSource.SCANNER,
        title=f"{label} issue",
        first_seen=now,
        last_seen=now,
        error_count=1,
        unique_traces=1,
    )


def _scan_issue(project, cluster, *, group="Tool Failures"):
    trace_id = uuid.uuid4()
    report = TraceInvestigationReport.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        project=project,
        trace_id=trace_id,
        source="legacy_scan",
        source_record_id=uuid.uuid4(),
        recorded_at=timezone.now(),
        is_current=True,
        execution_status="completed",
        grouping_status="completed",
        has_issues=True,
    )
    return TraceInvestigationFinding.objects.create(
        id=uuid.uuid4(),
        report=report,
        finding_id="legacy-1",
        ordinal=0,
        cluster=cluster,
        category="cat",
        group_label=group,
        fix_layer="Tools",
        statement="b",
    )


def _investigation_finding(project, cluster, *, trace_id=None):
    trace_id = trace_id or uuid.uuid4()
    now = timezone.now()
    job = TraceInvestigationJob.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        project=project,
        trace_id=trace_id,
        root_span_id="root",
        root_end_time=now,
        not_before=now,
    )
    attempt = TraceInvestigationAttempt.objects.create(
        job=job,
        generation=1,
        worker_id="test",
        engine_version="v2",
        lease_token_digest="a" * 64,
        lease_expires_at=now,
        read_cutoff=now,
        memory_snapshot_id="test",
        memory_digest="sha256:" + "b" * 64,
    )
    report = TraceInvestigationReport.objects.create(
        organization=project.organization,
        workspace=project.workspace,
        project=project,
        trace_id=trace_id,
        source="omega",
        recorded_at=now,
        is_current=True,
        job=job,
        attempt=attempt,
        idempotency_key=str(uuid.uuid4()),
        result_digest="sha256:" + "c" * 64,
        contract_version="omega-investigation/v1",
        evidence_digest="sha256:" + "d" * 64,
        execution_status="completed",
        outcome="failure",
        coverage_scope="trace",
        observed_span_count=1,
        read_complete=True,
        future_arrivals_known=False,
        model_calls=1,
        input_tokens=1,
        output_tokens=1,
        cost_status="known",
        grouping_status="completed",
    )
    job.current_report = report
    job.save(update_fields=["current_report"])
    return TraceInvestigationFinding.objects.create(
        id=uuid.uuid4(),
        report=report,
        finding_id="finding-1",
        ordinal=0,
        kind="unmet_requirement",
        statement="Refund amount was wrong",
        recovery="not_observed",
        cluster=cluster,
    )


@pytest.mark.django_db
class TestExplicitScopeRejectsForeignProject:
    """[explicit] selectors must return None for a row that lives in another
    project — even though the row genuinely exists (so absent the project_id
    filter it WOULD resolve)."""

    def test_resolve_cluster_context_is_the_gate(self, tenants):
        home, foreign = tenants
        fc = _cluster(foreign, "FOREIGN-1")
        # By UUID and by label, with home's project_id → None (both branches).
        assert selectors.resolve_cluster_context(str(fc.id), str(home.id)) is None
        assert selectors.resolve_cluster_context("FOREIGN-1", str(home.id)) is None
        # Within its own project it resolves, both forms — proving the None
        # above is the project filter, not a broken lookup.
        assert selectors.resolve_cluster_context(str(fc.id), str(foreign.id))[
            "uuid"
        ] == str(fc.id)
        assert selectors.resolve_cluster_context("FOREIGN-1", str(foreign.id))[
            "uuid"
        ] == str(fc.id)

    def test_resolve_cluster_context_none_project_fails_closed(self, tenants):
        # project_id is now required; the cross-tenant None branch was deleted.
        # This pins that even a None (e.g. someone re-introducing the old
        # `if project_id:` bypass) fails CLOSED — IS NULL matches no cluster —
        # instead of resolving across tenants.
        _, foreign = tenants
        fc = _cluster(foreign, "FOREIGN-1B")
        assert selectors.resolve_cluster_context(str(fc.id), None) is None

    def test_get_cluster_for_read_foreign_project(self, tenants):
        home, foreign = tenants
        fc = _cluster(foreign, "FOREIGN-2")
        assert selectors.get_cluster_for_read(str(fc.id), str(home.id)) is None
        assert selectors.get_cluster_for_read(str(fc.id), str(foreign.id)).id == fc.id

    def test_get_scan_issue_for_read_foreign_project(self, tenants):
        # The normalized finding resolves only within its report's project.
        home, foreign = tenants
        issue = _scan_issue(foreign, _cluster(foreign, "FOREIGN-3"))
        assert selectors.get_scan_issue_for_read(str(issue.id), str(home.id)) is None
        assert (
            selectors.get_scan_issue_for_read(str(issue.id), str(foreign.id)).id
            == issue.id
        )

    def test_get_version_for_read_foreign_project(self, tenants):
        home, foreign = tenants
        ver = ProjectVersion.objects.create(project=foreign, name="v", version="v1")
        assert selectors.get_version_for_read(str(ver.id), str(home.id)) is None
        assert selectors.get_version_for_read(str(ver.id), str(foreign.id)).id == ver.id


@pytest.mark.django_db
class TestTransitiveIsolation:
    """[transitive] selectors are scoped to their cluster_uuid ONLY. This pins
    cluster isolation (a WHERE clause), NOT tenant-safety — the tenant gate is
    resolve_cluster_context (above), which is why the agent never holds a foreign
    cluster_uuid to pass here."""

    def test_member_trace_ids_do_not_leak_across_clusters(self, tenants):
        home, foreign = tenants
        hc, fc = _cluster(home, "HOME-1"), _cluster(foreign, "FOREIGN-4")
        ht, ft = str(uuid.uuid4()), str(uuid.uuid4())
        ErrorClusterTraces.objects.create(cluster=hc, trace_id=ht)
        ErrorClusterTraces.objects.create(cluster=fc, trace_id=ft)
        assert selectors.cluster_member_trace_ids(str(hc.id)) == [ht]
        assert selectors.cluster_member_trace_ids(str(fc.id)) == [ft]
        # A cluster_uuid never surfaces another cluster's members.
        assert ft not in selectors.cluster_member_trace_ids(str(hc.id))


@pytest.mark.django_db
class TestCountBucketContract:
    """The agg selectors return the typed CountBucket shape, not a free-form
    dict — a key rename is a type error, not a silent KeyError."""

    def test_count_scan_issues_by_returns_count_buckets(self, tenants):
        home, _ = tenants
        hc = _cluster(home, "HOME-2")
        issue = _scan_issue(home, hc, group="Tool Failures")
        trace_id = str(issue.report.trace_id)
        buckets, total = selectors.count_scan_issues_by(str(hc.id), [trace_id], "group")
        assert total == 1
        assert buckets == [{"key": "Tool Failures", "count": 1}]
        for b in buckets:
            assert set(b) == {"key", "count"}
            assert isinstance(b["count"], int)
            assert b["key"] is None or isinstance(b["key"], str)


@pytest.mark.django_db
class TestInvestigationFindingAdapter:
    def test_v2_findings_feed_rca_issue_tools(self, tenants):
        home, foreign = tenants
        cluster = _cluster(home, "V2-1")
        cluster.issue_group = "Refunds"
        cluster.fix_layer = "Tools"
        cluster.save(update_fields=["issue_group", "fix_layer"])
        finding = _investigation_finding(home, cluster)
        trace_id = str(finding.report.job.trace_id)
        foreign_finding = _investigation_finding(foreign, _cluster(foreign, "OTHER"))

        rows, total = selectors.list_cluster_scan_issues(
            str(cluster.id), [trace_id], 0, 10
        )
        assert total == 1
        assert rows[0].id == finding.id
        assert rows[0].brief == "Refund amount was wrong"
        assert str(rows[0].scan_result.trace_id) == trace_id
        assert selectors.count_cluster_scan_issues(str(cluster.id), [trace_id]) == 1
        assert selectors.count_scan_issues_by(str(cluster.id), [trace_id], "group") == (
            [{"key": "Refunds", "count": 1}],
            1,
        )
        assert selectors.count_scan_issue_traces_by(
            str(cluster.id), [trace_id], "category"
        ) == [
            {"key": "unmet_requirement", "count": 1},
        ]
        assert [
            r.id
            for r in selectors.search_cluster_scan_issues(
                str(cluster.id), [trace_id], "amount", 10
            )
        ] == [finding.id]
        assert (
            selectors.get_scan_issue_for_read(str(finding.id), str(home.id)).id
            == finding.id
        )
        assert (
            selectors.get_scan_issue_for_read(str(finding.id), str(foreign.id)) is None
        )
        assert (
            selectors.get_scan_issue_for_read(str(foreign_finding.id), str(home.id))
            is None
        )

    def test_superseded_report_is_not_exposed(self, tenants):
        home, _ = tenants
        cluster = _cluster(home, "V2-2")
        finding = _investigation_finding(home, cluster)
        trace_id = str(finding.report.job.trace_id)
        finding.report.is_current = False
        finding.report.save(update_fields=["is_current"])

        assert selectors.list_cluster_scan_issues(
            str(cluster.id), [trace_id], 0, 10
        ) == ([], 0)
        assert selectors.get_scan_issue_for_read(str(finding.id), str(home.id)) is None


@pytest.mark.django_db
class TestF6CurrentMembership:
    def test_f6_reads_require_completed_current_matching_finding_membership(
        self, tenants
    ):
        home, _ = tenants
        cluster = _cluster(home, "F6-RCA")
        scope = TraceGroupingScope.objects.create(
            project=home, organization=home.organization, workspace=home.workspace
        )
        TraceGroupingIssueState.objects.create(scope=scope, cluster=cluster)
        finding = _investigation_finding(home, cluster)
        trace_id = str(finding.report.trace_id)

        # A finding FK alone is not F6 membership; its active junction is canonical.
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == []
        assert selectors.count_cluster_scan_issues(str(cluster.id), [trace_id]) == 0
        assert selectors.get_scan_issue_for_read(str(finding.id), str(home.id)) is None

        member = ErrorClusterTraces.objects.create(
            cluster=cluster, finding=finding, trace_id=trace_id
        )
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == [trace_id]
        assert [
            m.id for m in selectors.cluster_memberships(str(cluster.id), [trace_id])
        ] == [member.id]
        assert selectors.current_finding_ids_by_trace(str(cluster.id), [trace_id]) == {
            trace_id: finding.id
        }
        assert selectors.count_cluster_scan_issues(str(cluster.id), [trace_id]) == 1
        assert (
            selectors.get_scan_issue_for_read(str(finding.id), str(home.id)).id
            == finding.id
        )

        finding.report.grouping_status = "pending"
        finding.report.save(update_fields=["grouping_status"])
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == []
        assert selectors.count_cluster_scan_issues(str(cluster.id), [trace_id]) == 0
        finding.report.grouping_status = "completed"
        finding.report.save(update_fields=["grouping_status"])

        member.trace_id = uuid.uuid4()
        member.save(update_fields=["trace_id"])
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == []
        assert selectors.count_cluster_scan_issues(str(cluster.id), [trace_id]) == 0
        member.trace_id = trace_id
        member.save(update_fields=["trace_id"])

        finding.report.is_current = False
        finding.report.save(update_fields=["is_current"])
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == []
        assert selectors.get_scan_issue_for_read(str(finding.id), str(home.id)) is None

    def test_dirty_and_retired_f6_are_hidden_without_hiding_unmarked_groups(
        self, tenants
    ):
        home, _ = tenants
        cluster = _cluster(home, "F6-HIDDEN")
        legacy = _cluster(home, "LEGACY-VISIBLE")
        legacy_trace = str(uuid.uuid4())
        ErrorClusterTraces.objects.create(cluster=legacy, trace_id=legacy_trace)
        scope = TraceGroupingScope.objects.create(
            project=home, organization=home.organization, workspace=home.workspace
        )
        state = TraceGroupingIssueState.objects.create(scope=scope, cluster=cluster)
        finding = _investigation_finding(home, cluster)
        trace_id = str(finding.report.trace_id)
        ErrorClusterTraces.objects.create(
            cluster=cluster, finding=finding, trace_id=trace_id
        )

        for field in ("dirty", "retired"):
            setattr(state, field, True)
            state.save(update_fields=[field])
            assert (
                selectors.resolve_cluster_context(str(cluster.id), str(home.id)) is None
            )
            assert (
                selectors.resolve_cluster_context(cluster.cluster_id, str(home.id))
                is None
            )
            assert selectors.get_cluster_for_read(str(cluster.id), str(home.id)) is None
            assert selectors.cluster_member_trace_ids(str(cluster.id)) == []
            assert (
                selectors.get_scan_issue_for_read(str(finding.id), str(home.id)) is None
            )
            setattr(state, field, False)
            state.save(update_fields=[field])

        assert selectors.cluster_member_trace_ids(str(legacy.id)) == [legacy_trace]
        assert (
            selectors.resolve_cluster_context(legacy.cluster_id, str(home.id))
            is not None
        )

    def test_protected_empty_f6_issue_remains_readable(self, tenants):
        home, _ = tenants
        cluster = _cluster(home, "F6-EMPTY")
        scope = TraceGroupingScope.objects.create(
            project=home, organization=home.organization, workspace=home.workspace
        )
        state = TraceGroupingIssueState.objects.create(
            scope=scope, cluster=cluster, protected=True
        )
        cluster.error_count = cluster.total_events = cluster.unique_traces = 0
        cluster.save(update_fields=["error_count", "total_events", "unique_traces"])

        assert state.retired is False
        assert (
            selectors.resolve_cluster_context(cluster.cluster_id, str(home.id))
            is not None
        )
        assert (
            selectors.get_cluster_for_read(str(cluster.id), str(home.id)).error_count
            == 0
        )
        assert selectors.cluster_member_trace_ids(str(cluster.id)) == []


@pytest.mark.django_db
class TestReadTraceGate:
    """trace_eval_results is unscoped; its tenant guard is _read_trace's
    project-scoped spans-gate. A foreign trace (no spans in self.project_id) must
    be rejected BEFORE the unscoped eval read — never surfacing its eval rows."""

    def test_foreign_trace_rejected_before_eval_read(self):
        from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent

        agent = ClusterAnalysisAgent.__new__(ClusterAnalysisAgent)
        agent.project_id = "home-project"
        agent._trace_summary_cache = {}
        foreign_trace = str(uuid.uuid4())

        with (
            patch.object(agent, "_resolve_alias", return_value=foreign_trace),
            patch.object(agent, "_spans_for_trace", return_value=[]) as spans,
            patch(
                "ee.agenthub.cluster_rca.agent.selectors.trace_eval_results"
            ) as eval_read,
        ):
            out = agent._read_trace(foreign_trace, "summary")

        spans.assert_called_once_with(foreign_trace)
        eval_read.assert_not_called()  # gate fired before the unscoped eval read
        assert out.get("is_error") is True
