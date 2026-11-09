"""Exercise the real background worker and agent loop, never provider I/O."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from channels.db import database_sync_to_async
from django.test import override_settings

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from accounts.tests import test_workspace_landing, workspace_write_support
from ee.falcon_ai import agent, oauth_client
from ee.falcon_ai.models import Conversation, MCPConnector
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.queries import deep_analysis_state
from tracer.tasks import error_analysis

landing = test_workspace_landing.landing
scope_no_egress = workspace_write_support.scope_no_egress
pytestmark = pytest.mark.django_db(transaction=True)
SCHEMA = {"name": "inspect", "inputSchema": {"type": "object", "properties": {}}}


@pytest.fixture
def running_analysis(landing, scope_no_egress, monkeypatch):
    project = Project._base_manager.create(
        name="Revocation analysis",
        organization=landing.org,
        workspace=landing.default,
        user=landing.user,
        model_type="generative_llm",
        trace_type="observe",
    )
    connector = MCPConnector.no_workspace_objects.create(
        organization=landing.org,
        workspace=landing.default,
        name="revocation",
        server_url="http://127.0.0.1:9/mcp",
        auth_type="oauth",
        is_active=True,
        is_verified=True,
        discovered_tools=[SCHEMA],
    )
    state = SimpleNamespace(
        landing=landing,
        project=project,
        connector=connector,
        trace_id=str(uuid4()),
        turns=0,
        mutation=None,
        mutate_at="model",
        stall=False,
        cancelled=False,
        batch=False,
    )
    monkeypatch.setattr("ee.usage.deployment.DeploymentMode.is_oss", lambda: False)
    monkeypatch.setattr("tracer.services.clickhouse.client.ClickHouseClient", Mock())
    monkeypatch.setattr(agent, "load_tools_for_mode", lambda mode: [])
    monkeypatch.setattr(agent, "filter_tools_for_message", lambda tools, *a, **k: tools)
    monkeypatch.setattr(agent.tool_registry, "get", lambda name: None)
    monkeypatch.setattr(
        agent, "PromptBuilder", Mock(return_value=Mock(build=lambda **k: "test"))
    )
    monkeypatch.setattr(agent.AgentLoop, "_load_memories", AsyncMock(return_value=[]))

    async def compact(conversation, history, client):
        return conversation, history

    monkeypatch.setattr(
        agent,
        "ContextManager",
        Mock(
            return_value=SimpleNamespace(
                compact_if_needed=compact,
                prepare_messages=lambda *a, **k: [
                    {"role": "user", "content": "inspect"}
                ],
                truncate_messages=lambda messages: messages,
                truncate_result=lambda result: result,
            )
        ),
    )

    async def mutate():
        if state.mutation:
            await database_sync_to_async(state.mutation)()
            state.mutation = None

    async def stream(*args, **kwargs):
        state.turns += 1
        if state.stall:
            try:
                await asyncio.sleep(1)
            finally:
                state.cancelled = True
        if state.turns == 2 and state.mutate_at == "model":
            await mutate()
        if state.turns <= 2:
            calls = [
                {
                    "index": 0,
                    "id": f"call-{state.turns}",
                    "function": {
                        "name": "mcp_revocation_inspect",
                        "arguments": "{}",
                    },
                }
            ]
            if state.batch:
                calls.append({**calls[0], "index": 1, "id": "second-in-batch"})
            yield {
                "choices": [
                    {"delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}
                ]
            }
        else:
            yield {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(
        agent,
        "FalconLLMClient",
        Mock(return_value=SimpleNamespace(stream_with_retry=stream)),
    )
    token = Mock(return_value="synthetic-test-token")
    refresh = Mock(return_value={})
    monkeypatch.setattr(oauth_client, "get_valid_access_token", token)
    monkeypatch.setattr(oauth_client, "refresh_access_token", refresh)

    async def external_call(*args, **kwargs):
        # Boundary stub exercises the token boundary only for admitted calls.
        oauth_client.get_valid_access_token(connector)
        if state.mutate_at == "proxy":
            await mutate()
        return {"content": "Synthetic result", "is_error": False}

    proxy = AsyncMock(side_effect=external_call)
    monkeypatch.setattr("ee.falcon_ai.mcp_proxy.MCPConnectorProxy.execute_tool", proxy)
    sync_proxy = Mock(side_effect=AssertionError("Unexpected synchronous external I/O"))
    monkeypatch.setattr(
        "ee.falcon_ai.mcp_proxy.MCPConnectorProxy.execute_tool_sync", sync_proxy
    )
    state.proxy, state.token, state.refresh = proxy, token, refresh
    with override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    ):
        deep_analysis_state.set_running(state.trace_id)
        yield state
    clear_workspace_context()


def run(state):
    return error_analysis._falcon_analyze_single_trace(
        state.trace_id,
        str(state.project.pk),
        requesting_user_id=str(state.landing.user.pk),
        organization_id=str(state.landing.org.pk),
        workspace_id=str(state.landing.default.pk),
    )


def revoke(state, kind):
    if kind == "membership":
        OrganizationMembership._base_manager.filter(
            user=state.landing.user,
            organization=state.landing.org,
        ).delete()
    elif kind == "role":
        WorkspaceMembership._base_manager.filter(
            user=state.landing.user,
            workspace=state.landing.default,
        ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    elif kind == "user":
        User._base_manager.filter(pk=state.landing.user.pk).update(is_active=False)
    elif kind == "connector-deleted":
        MCPConnector._base_manager.filter(pk=state.connector.pk).delete()
    else:
        MCPConnector._base_manager.filter(pk=state.connector.pk).update(is_active=False)


def assert_aborted(state, result, code):
    assert result.get("code") == code, result
    assert not result["success"]
    assert result["outcome"] == "aborted"
    conversation = Conversation._base_manager.get(
        organization=state.landing.org,
        metadata__type="background_analysis",
    )
    assert conversation.metadata["analysis_outcome"] == {
        "status": "aborted",
        "code": code,
    }
    assert deep_analysis_state.status(state.trace_id, has_analysis=False) == "failed"
    assert not TraceErrorAnalysis._base_manager.filter(trace_id=state.trace_id).exists()
    state.refresh.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["membership", "role", "user", "connector-deleted", "connector-disabled"]
)
@pytest.mark.parametrize(
    "when", ["model", "proxy"], ids=["before-dispatch", "before-next-iteration"]
)
def test_live_revocation_aborts_next_dispatch(running_analysis, kind, when):
    state = running_analysis
    state.mutation = lambda: revoke(state, kind)
    state.mutate_at = when
    result = run(state)
    assert_aborted(state, result, "analysis_access_revoked")
    assert state.turns == (2 if when == "model" else 1)
    assert state.proxy.await_count == 1
    assert state.token.call_count == 1


def test_revocation_stops_remaining_calls_in_same_batch(running_analysis):
    state = running_analysis
    state.batch = True
    state.mutate_at = "proxy"
    state.mutation = lambda: revoke(state, "role")
    result = run(state)
    assert_aborted(state, result, "analysis_access_revoked")
    assert state.proxy.await_count == state.token.call_count == 1


def test_unrevoked_analysis_completes_with_mcp(running_analysis):
    state = running_analysis
    result = run(state)
    assert result["success"], result
    assert state.turns == 3
    assert state.proxy.await_count == state.token.call_count == 2
    assert TraceErrorAnalysis._base_manager.filter(trace_id=state.trace_id).exists()
    state.refresh.assert_not_called()


def test_background_wall_clock_bound_cancels_stalled_model(
    running_analysis, monkeypatch
):
    state = running_analysis
    state.stall = True
    # raising=False keeps the RED test behavioral rather than an import failure.
    monkeypatch.setattr(
        error_analysis, "FALCON_ANALYSIS_TIMEOUT_SECONDS", 0.05, raising=False
    )
    started = time.monotonic()
    result = run(state)
    elapsed = time.monotonic() - started
    assert_aborted(state, result, "analysis_deadline_exceeded")
    assert elapsed < 0.75
    assert state.cancelled
    assert state.turns == 1
    state.proxy.assert_not_awaited()
    state.token.assert_not_called()
