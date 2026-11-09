"""External tools use the caller's current workspace access at every dispatch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from asgiref.sync import async_to_sync

from accounts.models.workspace import WorkspaceMembership
from ai_tools.base import ToolContext
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.mcp_tools import MCPToolWrapper
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_workspace_scope as native_scope
from ee.falcon_ai.tests.test_workspace_scope import landing, membership_rows
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    get_current_user,
    get_current_workspace,
    set_workspace_context,
    workspace_context,
    workspace_scope_is_empty,
)

scope_state = native_scope.scope_state
pytestmark = pytest.mark.django_db(transaction=True)
SCHEMA = {
    "name": "read_workspace_data",
    "description": "Synthetic workspace tool",
    "inputSchema": {"type": "object", "properties": {}},
}


@pytest.fixture
def connector(scope_state):
    return MCPConnector.no_workspace_objects.create(
        organization=scope_state.org,
        workspace=scope_state.private,
        name="Workspace connector",
        server_url="http://127.0.0.1:9/mcp",
        auth_type="none",
        is_active=True,
        is_verified=True,
        discovered_tools=[SCHEMA],
    )


def grant(state):
    membership, _ = WorkspaceMembership.no_workspace_objects.update_or_create(
        user=state.member,
        workspace=state.private,
        defaults={
            "organization_membership": state.membership,
            "role": OrganizationRoles.WORKSPACE_MEMBER,
            "level": Level.WORKSPACE_MEMBER,
            "is_active": True,
        },
    )
    return membership


def discover(context):
    agent = SimpleNamespace(tool_context=context)
    return AgentLoop.__dict__["_load_mcp_tools_sync"].func(agent)


def dispatch(tool, context, mode):
    params = tool.input_model.model_validate({})
    if mode == "async":
        return async_to_sync(tool.async_execute)(params, context)
    if mode == "sync":
        return tool.execute(params, context)
    if mode == "run":
        return tool.run({}, context)
    agent = SimpleNamespace(tool_context=context)
    return AgentLoop.__dict__["_execute_tool"].func(agent, tool, {})


@pytest.fixture
def proxy_boundary():
    """Patch only the external proxy I/O, not discovery or authorization."""
    reply = {"content": "synthetic workspace data", "is_error": False}
    async_proxy = AsyncMock(return_value=reply)
    sync_proxy = Mock(return_value=reply)
    with (
        patch("ee.falcon_ai.mcp_proxy.MCPConnectorProxy.execute_tool", async_proxy),
        patch("ee.falcon_ai.mcp_proxy.MCPConnectorProxy.execute_tool_sync", sync_proxy),
    ):
        yield SimpleNamespace(async_proxy=async_proxy, sync_proxy=sync_proxy)


def assert_calls(boundary, mode, count):
    assert boundary.async_proxy.await_count == (count if mode == "async" else 0)
    assert boundary.sync_proxy.call_count == (0 if mode == "async" else count)


def test_external_discovery_and_async_dispatch_confine_none_landing(
    scope_state, connector, proxy_boundary
):
    """Retain the independent discovery/async-dispatch reproducer permanently."""
    state = scope_state
    before = membership_rows(state)
    assert not state.member.can_access_workspace(state.private)
    workspace = landing(state, state.member)
    assert workspace is None
    context = ToolContext(state.member, state.org, workspace)
    tools = discover(context)
    selected = [tool for tool in tools if tool._connector.pk == connector.pk]
    for tool in selected:
        dispatch(tool, context, "async")
    assert_calls(proxy_boundary, "async", 0)
    assert selected == [], "Unavailable connector metadata must not be discovered"
    assert not state.member.can_access_workspace(state.private)
    assert membership_rows(state) == before
    assert not workspace_scope_is_empty()


@pytest.mark.parametrize("mode", ["sync", "async", "run", "native"])
def test_direct_wrapper_requires_workspace_access(
    scope_state, connector, proxy_boundary, mode
):
    state = scope_state
    before = membership_rows(state)
    tool = MCPToolWrapper(connector, SCHEMA)
    context = ToolContext(state.member, state.org, None)
    result = dispatch(tool, context, mode)
    assert result.is_error
    assert_calls(proxy_boundary, mode, 0)
    assert membership_rows(state) == before
    assert get_current_workspace() is None
    assert not workspace_scope_is_empty()


@pytest.mark.parametrize("mode", ["sync", "async", "run", "native"])
@pytest.mark.parametrize("change", ["deactivate", "delete", "org", "move", "disable"])
def test_discovered_wrapper_rechecks_current_access(
    scope_state, connector, proxy_boundary, mode, change
):
    state = scope_state
    membership = grant(state)
    workspace = landing(state, state.member)
    assert workspace == state.private
    context = ToolContext(state.member, state.org, workspace)
    tools = discover(context)
    assert len(tools) == 1
    tool = tools[0]
    # An accessible control proves the actual external boundary is reached.
    result = dispatch(tool, context, mode)
    assert not result.is_error, result
    assert_calls(proxy_boundary, mode, 1)
    proxy_boundary.async_proxy.reset_mock()
    proxy_boundary.sync_proxy.reset_mock()
    if change == "deactivate":
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    elif change == "delete":
        membership.delete()
    elif change == "org":
        state.membership.is_active = False
        state.membership.save(update_fields=["is_active"])
    elif change == "move":
        MCPConnector.no_workspace_objects.filter(pk=connector.pk).update(
            workspace=state.default
        )
    else:
        MCPConnector.no_workspace_objects.filter(pk=connector.pk).update(
            is_active=False
        )
    before = membership_rows(state)
    result = dispatch(tool, context, mode)
    assert result.is_error
    assert_calls(proxy_boundary, mode, 0)
    assert membership_rows(state) == before


@pytest.mark.parametrize("actor", ["member", "owner"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_accessible_connector_control(
    scope_state, connector, proxy_boundary, actor, mode
):
    state = scope_state
    user = getattr(state, actor)
    if actor == "member":
        grant(state)
    context = ToolContext(user, state.org, state.private)
    assert user.can_access_workspace(state.private)
    tools = discover(context)
    assert len(tools) == 1
    result = dispatch(tools[0], context, mode)
    assert not result.is_error, result
    assert result.content == "synthetic workspace data"
    assert_calls(proxy_boundary, mode, 1)


@pytest.mark.parametrize("ambient_empty", [False, True])
def test_discovery_restores_ambient_context(scope_state, connector, ambient_empty):
    state = scope_state
    ambient_workspace = None if ambient_empty else state.private
    set_workspace_context(
        workspace=ambient_workspace,
        organization=state.org,
        user=state.owner,
        require_workspace=ambient_empty,
    )
    assert discover(ToolContext(state.member, state.org, None)) == []
    assert get_current_workspace() == ambient_workspace
    assert get_current_user() == state.owner
    assert workspace_scope_is_empty() is ambient_empty


def test_nested_evaluator_rechecks_connector_access(
    scope_state, connector, proxy_boundary
):
    from ee.agenthub.eval_orchestrator.orchestrator import EvalOrchestrator

    state = scope_state
    membership = grant(state)
    # Exercise the real nested dispatch without initializing any model client.
    evaluator = EvalOrchestrator.__new__(EvalOrchestrator)
    evaluator.eval_config = SimpleNamespace(
        available_resources={"mcp_tools": [{"name": SCHEMA["name"]}]}
    )
    evaluator._resolve_organization_id = lambda: str(state.org.pk)
    evaluator._resources_used = []
    with workspace_context(state.private, state.org, state.member):
        set_workspace_context(require_workspace=True)
        result = evaluator._tool_call_mcp(SCHEMA["name"], {})
        assert "error" not in result, result
        assert_calls(proxy_boundary, "sync", 1)
        proxy_boundary.sync_proxy.reset_mock()
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        result = evaluator._tool_call_mcp(SCHEMA["name"], {})
        assert "error" in result, result
        assert_calls(proxy_boundary, "sync", 0)
