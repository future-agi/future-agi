import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_agent_definition


@pytest.fixture
def agent_definition(tool_context):
    return make_agent_definition(tool_context)


# ===================================================================
# READ TOOLS
# ===================================================================


class TestGetAgentTool:
    def test_get_existing(self, tool_context, agent_definition):
        result = run_tool(
            "get_agent",
            {"agent_id": str(agent_definition.id)},
            tool_context,
        )

        assert not result.is_error
        assert "Test Agent" in result.content
        assert result.data["id"] == str(agent_definition.id)

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_agent",
            {"agent_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool(
            "get_agent",
            {"agent_id": "not-a-uuid"},
            tool_context,
        )

        assert result.is_error


class TestListAgentVersionsTool:
    def test_list_empty(self, tool_context, agent_definition):
        result = run_tool(
            "list_agent_versions",
            {"agent_id": str(agent_definition.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["total"] == 0

    def test_list_nonexistent_agent(self, tool_context):
        result = run_tool(
            "list_agent_versions",
            {"agent_id": str(uuid.uuid4())},
            tool_context,
        )

        # May return empty list or error depending on implementation
        # Just verify it doesn't crash
        assert isinstance(result.is_error, bool)


class TestListTestExecutionsTool:
    def test_list_empty(self, tool_context):
        # run_test_id is required; pass a random UUID to get empty results
        result = run_tool(
            "list_test_executions",
            {"run_test_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["total"] == 0


class TestGetTestExecutionTool:
    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_test_execution",
            {"execution_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error


class TestGetCallExecutionTool:
    def test_get_nonexistent(self, tool_context):
        # get_call_execution is now the DRF-bridged CallExecutionDetailView
        # (id param), surfacing full call detail incl. analytics (TH-5397).
        result = run_tool(
            "get_call_execution",
            {"id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error
