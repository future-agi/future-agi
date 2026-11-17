"""Falcon's nested and thread-delegated tool paths keep the caller scope."""

from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync

from ai_tools.base import ToolContext
from ee.agenthub.cluster_rca.falcon_bridge import run_cluster_rca
from ee.falcon_ai.tests import test_mcp_workspace_scope as external_scope
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from ee.falcon_ai.tests.test_mcp_workspace_scope import SCHEMA, grant
from tfc.middleware.workspace_context import set_workspace_context, workspace_context

scope_state = native_scope.scope_state
connector = external_scope.connector
pytestmark = pytest.mark.django_db(transaction=True)


def test_nested_discovery_requires_current_access(scope_state, connector):
    from ee.agenthub.eval_orchestrator.utils import gather_available_resources

    state = scope_state
    membership = grant(state)
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        resources = gather_available_resources(str(state.org.pk))
        assert resources["mcp_tools"] == [
            {"name": SCHEMA["name"], "description": SCHEMA["description"]}
        ]
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        assert gather_available_resources(str(state.org.pk))["mcp_tools"] == []


def test_background_connector_discovery_without_actor_returns_none(
    scope_state, connector
):
    from ee.agenthub.eval_orchestrator.utils import gather_available_resources
    from ee.falcon_ai.mcp_tools import load_mcp_tools

    state = scope_state
    with workspace_context(state.private, state.org, None):
        assert load_mcp_tools(state.org, state.private) == []
        assert gather_available_resources(str(state.org.pk))["mcp_tools"] == []


def test_cluster_delegation_refuses_empty_landing_before_resolution(scope_state):
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project
    from tracer.models.trace_error_analysis import TraceErrorGroup

    state = scope_state
    project = Project.objects.create(
        name="Scope project",
        organization=state.org,
        workspace=state.private,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    cluster = TraceErrorGroup.objects.create(
        project=project, cluster_id="C01", error_type="Synthetic group"
    )
    context = ToolContext(state.member, state.org, None)
    send = AsyncMock()
    # Stub only the gateway I/O boundary. No remote model may be reached.
    with patch(
        "openai.resources.chat.completions.Completions.create",
        side_effect=AssertionError("unexpected model I/O"),
    ) as model_call:
        result = async_to_sync(run_cluster_rca)(
            tool_context=context,
            context_info={
                "entity_type": "error_cluster",
                "entity_id": cluster.cluster_id,
                "project_id": str(project.pk),
            },
            user_message="Inspect this cluster",
            send_callback=send,
        )
    model_call.assert_not_called()
    assert result["tool_calls"] == []
    assert any(
        "isn't available in this workspace" in str(call.args[0])
        for call in send.await_args_list
    )


def test_delegated_dispatch_rechecks_workspace_membership(scope_state):
    from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent

    state = scope_state
    membership = grant(state)
    agent = ClusterAnalysisAgent.__new__(ClusterAnalysisAgent)
    agent._falcon_tool_context = ToolContext(state.member, state.org, state.private)
    agent._call_history = {}
    agent._dominant_dims = {}
    calls = []

    def synthetic_read(**args):
        calls.append(args)
        return {"observed": True}

    agent._tool_read = synthetic_read
    assert agent._execute_tool("read", {"item": "first"}) == {"observed": True}
    assert calls == [{"item": "first"}]
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    result = agent._execute_tool("read", {"item": "second"})
    assert result.get("is_error") is True, result
    assert calls == [{"item": "first"}]
