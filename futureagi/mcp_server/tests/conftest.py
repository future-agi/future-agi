import pytest

from mcp_server.models.connection import MCPConnection
from mcp_server.models.session import MCPSession
from mcp_server.models.tool_config import MCPToolGroupConfig
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def mcp_connection(user, workspace):
    """Create an MCP connection for testing."""
    org = user.organization
    set_workspace_context(workspace=workspace, organization=org, user=user)

    conn = MCPConnection(
        user=user,
        organization=org,
        workspace=workspace,
        connection_mode="stdio",
    )
    conn.save()

    config = MCPToolGroupConfig(connection=conn)
    config.save()

    yield conn
    clear_workspace_context()


@pytest.fixture
def mcp_session(mcp_connection):
    """Create an MCP session for testing."""
    return MCPSession.objects.create(
        connection=mcp_connection,
        user=mcp_connection.user,
        organization=mcp_connection.organization,
        workspace=mcp_connection.workspace,
        transport="stdio",
    )
