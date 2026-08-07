"""
End-to-end workflow tests for error cluster Falcon tools.

These tests run the REAL Falcon AgentLoop with REAL LLM calls.
The LLM decides which tools to call, executes them against the DB,
and we verify the full pipeline — tool selection, data retrieval,
and final response quality.

Run:
    pytest ai_tools/tests/test_workflows_error_cluster.py -v -s

Requires:
    - FALCON_AI_API_KEY set in environment
    - Real database with test data created by fixtures
"""

import asyncio

import pytest
from ai_tools.tests.fixtures import make_full_error_cluster

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.live_llm]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_user_workspace(db):
    """Get or create a user+org+workspace for workflow tests."""
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tfc.constants.roles import OrganizationRoles
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    clear_workspace_context()

    org, _ = Organization.objects.get_or_create(name="Workflow Test Org")
    set_workspace_context(organization=org)

    user, created = User.objects.get_or_create(
        email="workflow-test@futureagi.com",
        defaults={
            "name": "Workflow Test User",
            "organization": org,
            "organization_role": OrganizationRoles.OWNER,
        },
    )
    if created:
        user.set_password("testpassword123")
        user.save()

    workspace, _ = Workspace.objects.get_or_create(
        organization=org,
        name="Workflow Test Workspace",
        defaults={"is_default": True, "is_active": True, "created_by": user},
    )

    set_workspace_context(workspace=workspace, organization=org, user=user)
    return org, user, workspace


@pytest.fixture
def wf_tool_context(org_user_workspace):
    """ToolContext for workflow tests."""
    from ai_tools.base import ToolContext
    from tfc.middleware.workspace_context import set_workspace_context

    org, user, workspace = org_user_workspace
    set_workspace_context(workspace=workspace, organization=org, user=user)
    return ToolContext(user=user, organization=org, workspace=workspace)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class CallbackCollector:
    """Captures all events emitted by the AgentLoop."""

    def __init__(self):
        self.events = []
        self.tool_calls = []
        self.tool_results = []

    async def __call__(self, event):
        self.events.append(event)
        etype = event.get("type", "")
        if etype == "tool_call_start":
            self.tool_calls.append(event["data"])
        elif etype == "tool_call_result":
            self.tool_results.append(event["data"])

    @property
    def tool_names_called(self):
        return [tc["tool_name"] for tc in self.tool_calls]

    @property
    def failed_tools(self):
        return [tr for tr in self.tool_results if tr["status"] == "error"]


def run_falcon(
    tool_context, user, workspace, message, context_page="", context_info=None
):
    """Run one Falcon agent turn with real LLM. Returns (result, collector)."""
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.models import Conversation

    conversation = Conversation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        title="New conversation",
    )

    agent = AgentLoop(tool_context=tool_context, conversation=conversation)
    collector = CallbackCollector()

    async def _run():
        return await agent.run(
            user_message=message,
            history_messages=[],
            send_callback=collector,
            context_page=context_page,
            context_info=context_info or {},
        )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()

    return result, collector


# ===================================================================
# WORKFLOW 1: "What are the top errors?"
# ===================================================================


class TestFalconListsClusters:
    """User asks about errors → Falcon should call list_error_clusters."""

    def test_falcon_lists_clusters_on_error_question(
        self, wf_tool_context, org_user_workspace
    ):
        _, user, workspace = org_user_workspace
        make_full_error_cluster(wf_tool_context, num_traces=3, cluster_id="KFLIVE01")

        result, collector = run_falcon(
            wf_tool_context,
            user,
            workspace,
            "What are the most common errors in my traces this week?",
            context_page="tracing",
        )

        assert {"list_error_clusters", "analyze_errors"} & set(
            collector.tool_names_called
        ), f"Expected an error analysis tool, got: {collector.tool_names_called}"
        assert (
            len(collector.failed_tools) == 0
        ), f"Tool failures: {[t['tool_name'] for t in collector.failed_tools]}"
        assert result["content"], "Falcon produced no response"


# ===================================================================
# WORKFLOW 2: "Investigate this cluster"
# ===================================================================


class TestFalconInvestigatesCluster:
    """User on feed detail page → Falcon should use analyze/detail tool."""

    def test_falcon_analyzes_cluster_from_context(
        self, wf_tool_context, org_user_workspace
    ):
        _, user, workspace = org_user_workspace
        cluster, *_ = make_full_error_cluster(
            wf_tool_context, num_traces=3, cluster_id="KFLIVE02"
        )

        result, collector = run_falcon(
            wf_tool_context,
            user,
            workspace,
            f'Investigate error cluster {cluster.cluster_id}: "Hallucinated Content". '
            f"What's the root cause pattern across affected traces?",
            context_page="tracing",
            context_info={
                "entity_type": "error_cluster",
                "entity_id": cluster.cluster_id,
                "path": f"/dashboard/feed/{cluster.cluster_id}",
            },
        )

        cluster_tools = {
            "analyze_error_cluster",
            "get_error_cluster_detail",
            "list_error_clusters",
            "analyze_errors",
        }
        assert (
            set(collector.tool_names_called) & cluster_tools
        ), f"Expected a cluster tool, got: {collector.tool_names_called}"
        assert len(collector.failed_tools) == 0
        assert result["content"]


# ===================================================================
# WORKFLOW 3: Multi-turn — list then drill in
# ===================================================================


class TestFalconMultiTurnInvestigation:
    """Two separate turns: list clusters, then analyze one."""

    def test_two_turn_list_then_analyze(self, wf_tool_context, org_user_workspace):
        _, user, workspace = org_user_workspace
        cluster, *_ = make_full_error_cluster(
            wf_tool_context, num_traces=4, cluster_id="KFLIVE03"
        )

        # Turn 1: List
        result1, collector1 = run_falcon(
            wf_tool_context,
            user,
            workspace,
            "Show me the error clusters from the last 30 days",
            context_page="tracing",
        )
        assert {"list_error_clusters", "analyze_errors"} & set(
            collector1.tool_names_called
        )
        assert result1["content"]

        # Turn 2: Drill in
        result2, collector2 = run_falcon(
            wf_tool_context,
            user,
            workspace,
            f"Analyze error cluster {cluster.cluster_id} in detail. "
            "What are the common root causes?",
            context_page="tracing",
        )
        cluster_tools = {
            "analyze_error_cluster",
            "get_error_cluster_detail",
            "analyze_errors",
        }
        assert (
            set(collector2.tool_names_called) & cluster_tools
        ), f"Expected cluster analysis tool, got: {collector2.tool_names_called}"
        assert result2["content"]


# ===================================================================
# WORKFLOW 4: Question-driven investigation
# ===================================================================


class TestFalconAnswersQuestion:
    """User asks a specific question about a cluster."""

    def test_falcon_handles_targeted_question(
        self, wf_tool_context, org_user_workspace
    ):
        _, user, workspace = org_user_workspace
        cluster, *_ = make_full_error_cluster(
            wf_tool_context, num_traces=3, cluster_id="KFLIVE04"
        )

        result, collector = run_falcon(
            wf_tool_context,
            user,
            workspace,
            f"For error cluster {cluster.cluster_id}, which traces have the "
            "lowest scores? What's wrong with them?",
            context_page="tracing",
            context_info={
                "entity_type": "error_cluster",
                "entity_id": cluster.cluster_id,
            },
        )

        cluster_tools = {
            "analyze_error_cluster",
            "analyze_errors",
            "explore_trace_legacy",
            "get_error_cluster_detail",
            "get_trace",
            "get_trace_error_analysis",
            "search_traces",
        }
        assert (
            set(collector.tool_names_called) & cluster_tools
        ), f"Expected cluster/trace tool, got: {collector.tool_names_called}"
        assert len(collector.failed_tools) == 0
        assert result["content"]


# ===================================================================
# WORKFLOW 5: Context-only ("analyze this" on cluster page)
# ===================================================================


class TestFalconUsesContextEntity:
    """User says 'analyze this' — Falcon picks up cluster ID from context."""

    def test_falcon_uses_context_entity_id(self, wf_tool_context, org_user_workspace):
        _, user, workspace = org_user_workspace
        cluster, *_ = make_full_error_cluster(
            wf_tool_context, num_traces=2, cluster_id="KFLIVE05"
        )

        result, collector = run_falcon(
            wf_tool_context,
            user,
            workspace,
            "Analyze this error cluster. What should we fix first?",
            context_page="tracing",
            context_info={
                "entity_type": "error_cluster",
                "entity_id": cluster.cluster_id,
                "path": f"/dashboard/feed/{cluster.cluster_id}",
            },
        )

        cluster_tools = {
            "analyze_error_cluster",
            "get_error_cluster_detail",
            "analyze_errors",
        }
        assert (
            set(collector.tool_names_called) & cluster_tools
        ), f"Expected cluster tool from context, got: {collector.tool_names_called}"
        assert len(collector.failed_tools) == 0
        assert result["content"]
