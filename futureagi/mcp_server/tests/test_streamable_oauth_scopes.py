"""OAuth scope and tool-group enforcement on the Streamable HTTP transport.

An MCP OAuth access token carries the tool groups the user consented to as its
``scope``. Every tool listed or called over ``/mcp`` with that token must sit
inside those groups *and* inside the connection's enabled tool groups, and a
refused call must stop before rate limiting, session bookkeeping, usage
recording or any API dispatch.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
from asgiref.sync import sync_to_async
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_server import mcp_app
from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from mcp_server.models.usage import MCPUsageRecord
from model_hub.models.develop_dataset import Dataset

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def scoped_client(user, workspace, monkeypatch):
    """An MCP client session authenticated by an OAuth token with ``scope``."""

    @asynccontextmanager
    async def connect(scope):
        from mcp_server.oauth_utils import generate_oauth_token

        token, _ = await sync_to_async(generate_oauth_token)(
            user.id, user.organization_id, workspace.id, "scope-test-client", scope
        )
        monkeypatch.setattr(mcp_app, "_streamable_app", None)
        monkeypatch.setattr(mcp_app, "_session_manager", None)
        mcp_app.get_mcp_streamable_app()
        async with (
            mcp_app._session_manager.run(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
                base_url="http://localhost",
                headers={"Authorization": f"Bearer {token}"},
            ) as http_client,
            streamable_http_client(
                "http://localhost/mcp", http_client=http_client
            ) as streams,
        ):
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    return connect


def _group_tools(*groups):
    return {
        tool.name
        for tool in mcp_app.registry.list_all()
        if tool.group in groups and tool.is_available()
    }


def _bookkeeping(user):
    return {
        "sessions": MCPSession.objects.filter(user=user).count(),
        "usage": MCPUsageRecord.objects.filter(session__user=user).count(),
    }


class _ExecutorSpy:
    """Records every tool the executor is asked to run, then runs it."""

    def __init__(self):
        self.calls = []
        self._execute = mcp_app.executor.execute

    async def __call__(self, tool, arguments, context):
        self.calls.append(tool.name)
        return await self._execute(tool, arguments, context)


@pytest.fixture
def executor_spy(monkeypatch):
    spy = _ExecutorSpy()
    monkeypatch.setattr(mcp_app.executor, "execute", spy)
    return spy


def _assert_refused(result, *, code="FORBIDDEN"):
    assert result.isError is True, result
    assert result.structuredContent["error"]["code"] == code


@pytest.mark.parametrize("scope", ["context", ["context"]])
async def test_token_cannot_call_a_tool_outside_its_scope(
    scoped_client, executor_spy, user, scope
):
    """A context-only token must not create datasets, in either scope shape."""

    before = await sync_to_async(_bookkeeping)(user)
    async with scoped_client(scope) as client:
        created = await client.call_tool(
            "create_dataset", {"new_dataset_name": "out of scope dataset"}
        )
        listed = await client.call_tool("list_datasets", {})

    _assert_refused(created)
    _assert_refused(listed)
    assert "scope" in created.structuredContent["error"]["message"].lower()
    assert executor_spy.calls == []
    assert not await sync_to_async(
        Dataset.no_workspace_objects.filter(name="out of scope dataset").exists
    )()
    assert await sync_to_async(_bookkeeping)(user) == before


@pytest.mark.parametrize(
    "scope, groups",
    [
        ("context", ("context",)),
        (["context"], ("context",)),
        ("context datasets", ("context", "datasets")),
        (["context", "datasets"], ("context", "datasets")),
    ],
)
async def test_token_lists_only_tools_inside_its_scope(scoped_client, scope, groups):
    async with scoped_client(scope) as client:
        listing = await client.list_tools()

    names = {tool.name for tool in listing.tools}
    assert names == await sync_to_async(_group_tools)(*groups)
    assert ("list_datasets" in names) is ("datasets" in groups)


@pytest.mark.parametrize("scope", ["", []])
async def test_token_with_no_scope_gets_no_tools(
    scoped_client, executor_spy, user, scope
):
    before = await sync_to_async(_bookkeeping)(user)
    async with scoped_client(scope) as client:
        listing = await client.list_tools()
        result = await client.call_tool("whoami", {})

    assert listing.tools == []
    _assert_refused(result)
    assert executor_spy.calls == []
    assert await sync_to_async(_bookkeeping)(user) == before


@pytest.mark.parametrize(
    "scope", ["context datasets", ["context", "datasets"], "context,datasets"]
)
async def test_token_calls_tools_inside_its_scope(
    scoped_client, executor_spy, user, workspace, scope
):
    """Authorized positive control: in-scope tools run and are recorded."""

    async with scoped_client(scope) as client:
        whoami = await client.call_tool("whoami", {})
        created = await client.call_tool(
            "create_dataset", {"new_dataset_name": "in scope dataset"}
        )

    assert whoami.isError is False, whoami
    assert whoami.structuredContent["id"] == str(user.id)
    assert created.isError is False, created
    assert executor_spy.calls == ["whoami", "create_dataset"]
    assert await sync_to_async(
        Dataset.no_workspace_objects.filter(name="in scope dataset").exists
    )()
    assert await sync_to_async(
        lambda: set(
            MCPUsageRecord.objects.filter(session__user=user).values_list(
                "tool_name", flat=True
            )
        )
    )() == {"whoami", "create_dataset"}


async def test_in_scope_tool_in_a_disabled_group_is_refused(
    scoped_client, executor_spy, user, workspace
):
    """Scope never widens the connection's enabled tool groups."""

    def disable_datasets():
        connection, _ = MCPConnection.no_workspace_objects.get_or_create(
            user=user,
            workspace=workspace,
            deleted=False,
            defaults={"organization": user.organization},
        )
        config, _ = MCPToolGroupConfig.no_workspace_objects.get_or_create(
            connection=connection
        )
        config.enabled_groups = ["context"]
        config.save(update_fields=["enabled_groups"])

    await sync_to_async(disable_datasets)()
    before = await sync_to_async(_bookkeeping)(user)
    async with scoped_client("context datasets") as client:
        listing = await client.list_tools()
        created = await client.call_tool(
            "create_dataset", {"new_dataset_name": "disabled group dataset"}
        )
        whoami = await client.call_tool("whoami", {})

    assert "create_dataset" not in {tool.name for tool in listing.tools}
    _assert_refused(created)
    assert "disabled" in created.structuredContent["error"]["message"].lower()
    assert whoami.isError is False, whoami
    assert executor_spy.calls == ["whoami"]
    assert not await sync_to_async(
        Dataset.no_workspace_objects.filter(name="disabled group dataset").exists
    )()
    after = await sync_to_async(_bookkeeping)(user)
    # Only the allowed whoami call may open a session and record usage.
    assert after["usage"] == before["usage"] + 1


async def test_refused_call_does_not_consume_rate_limit(scoped_client, user):
    with patch("mcp_server.rate_limiter.check_rate_limit") as check_rate_limit:
        async with scoped_client("context") as client:
            result = await client.call_tool("list_datasets", {})

    _assert_refused(result)
    check_rate_limit.assert_not_called()
