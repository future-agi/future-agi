import pytest

from ee.falcon_ai.mcp_tools import load_mcp_tools
from ee.falcon_ai.models import MCPConnector

DISCOVERED_TOOLS = [
    {"name": "search_docs", "description": "Search"},
    {"name": "list_files", "description": "List"},
]


@pytest.mark.django_db
class TestLoadMcpTools:
    def _connector(self, user, workspace, enabled_tool_names):
        return MCPConnector.objects.create(
            organization=user.organization,
            workspace=workspace,
            name="Docs",
            server_url="https://example.com/mcp",
            is_active=True,
            is_verified=True,
            discovered_tools=DISCOVERED_TOOLS,
            enabled_tool_names=enabled_tool_names,
            created_by=user,
        )

    def test_empty_enabled_list_loads_no_tools(self, user, workspace):
        self._connector(user, workspace, enabled_tool_names=[])

        tools = load_mcp_tools(user.organization, workspace)

        assert tools == []

    def test_non_empty_enabled_list_loads_only_those_tools(self, user, workspace):
        self._connector(user, workspace, enabled_tool_names=["search_docs"])

        tools = load_mcp_tools(user.organization, workspace)

        assert [t._remote_tool_name for t in tools] == ["search_docs"]

    def test_full_enabled_list_loads_every_discovered_tool(self, user, workspace):
        self._connector(
            user, workspace, enabled_tool_names=["search_docs", "list_files"]
        )

        tools = load_mcp_tools(user.organization, workspace)

        assert {t._remote_tool_name for t in tools} == {"search_docs", "list_files"}
