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
from tracer.models.trace_scan import TraceScanIssue, TraceScanResult, TraceScanStatus


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
            name=f"{name} WS", organization=org, is_default=True,
            is_active=True, created_by=user,
        )
        return Project.objects.create(
            name=f"{name} Project", organization=org, workspace=ws,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM, trace_type="observe",
        )

    home, foreign = _project("Home"), _project("Foreign")
    clear_workspace_context()
    return home, foreign


def _cluster(project, label):
    now = timezone.now()
    return TraceErrorGroup.objects.create(
        project=project, cluster_id=label, error_type=f"{label}-err",
        source=ClusterSource.SCANNER, title=f"{label} issue",
        first_seen=now, last_seen=now, error_count=1, unique_traces=1,
    )


def _scan_issue(project, cluster, *, group="Tool Failures"):
    sr = TraceScanResult.objects.create(
        trace_id=str(uuid.uuid4()), project_id=project.id,
        status=TraceScanStatus.COMPLETED,
    )
    return TraceScanIssue.objects.create(
        scan_result=sr, cluster=cluster, category="cat", group=group,
        fix_layer="Tools", brief="b",
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
        assert selectors.resolve_cluster_context(str(fc.id), str(foreign.id))["uuid"] == str(fc.id)
        assert selectors.resolve_cluster_context("FOREIGN-1", str(foreign.id))["uuid"] == str(fc.id)

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
        # [explicit] via scan_result__project_id (TraceScanIssue has no direct
        # project FK) — nik13 flagged this one as untested.
        home, foreign = tenants
        issue = _scan_issue(foreign, _cluster(foreign, "FOREIGN-3"))
        assert selectors.get_scan_issue_for_read(str(issue.id), str(home.id)) is None
        assert selectors.get_scan_issue_for_read(str(issue.id), str(foreign.id)).id == issue.id

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
        trace_id = str(issue.scan_result.trace_id)
        buckets, total = selectors.count_scan_issues_by(str(hc.id), [trace_id], "group")
        assert total == 1
        assert buckets == [{"key": "Tool Failures", "count": 1}]
        for b in buckets:
            assert set(b) == {"key", "count"}
            assert isinstance(b["count"], int)
            assert b["key"] is None or isinstance(b["key"], str)


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
