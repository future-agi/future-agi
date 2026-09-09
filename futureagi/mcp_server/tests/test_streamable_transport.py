from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from asgiref.sync import sync_to_async
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from accounts.models.user import OrgApiKey
from mcp_server import mcp_app

pytestmark = pytest.mark.e2e


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_streamable_http_lists_and_executes_generated_tools(user, workspace):
    credentials = await sync_to_async(OrgApiKey.objects.create)(
        name="MCP transport test",
        api_key=f"mcp-transport-{uuid4().hex}",
        secret_key=uuid4().hex,
        organization=user.organization,
        workspace=workspace,
        user=user,
        type="mcp",
    )
    mcp_app.get_mcp_streamable_app()

    headers = {
        "X-Api-Key": credentials.api_key,
        "X-Secret-Key": credentials.secret_key,
    }
    with patch.object(
        mcp_app.executor,
        "execute",
        new=AsyncMock(return_value={"id": str(user.id)}),
    ):
        async with (
            mcp_app._session_manager.run(),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
                base_url="http://localhost",
                headers=headers,
            ) as http_client,
            streamable_http_client("http://localhost/mcp", http_client=http_client) as (
                read_stream,
                write_stream,
                _,
            ),
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("whoami", {})

    assert len(tools.tools) == 60
    assert result.isError is False
    assert result.structuredContent["id"] == str(user.id)
