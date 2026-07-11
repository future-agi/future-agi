from unittest.mock import MagicMock, patch

import pytest

from ai_tools.base import ToolContext, ToolResult
from ai_tools.registry import ToolRegistry, registry
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def tool_context(user, workspace):
    """Create a ToolContext from test fixtures."""
    org = user.organization
    set_workspace_context(workspace=workspace, organization=org, user=user)
    yield ToolContext(user=user, organization=org, workspace=workspace)
    clear_workspace_context()


@pytest.fixture
def fresh_registry():
    """A fresh registry for testing registration logic.

    Does NOT affect the global registry used by tools.
    """
    return ToolRegistry()


def run_tool(name: str, params: dict, context: ToolContext) -> ToolResult:
    """Convenience: look up a tool by name and run it.

    Phase 3A: destructive tools answer the first call with a
    CONFIRMATION_REQUIRED preview (zero side effects). Tests are the
    approver here, so simulate the Confirm button (flip the server-held
    record to approved) and re-call with confirm=true — every existing
    delete-path test now exercises the real two-phase gate.

    Usage:
        result = run_tool("create_dataset", {"name": "ds", "columns": ["a"]}, tool_context)
    """
    tool = registry.get(name)
    assert tool is not None, f"Tool '{name}' not found in registry"
    result = tool.run(params, context)
    if getattr(result, "error_code", None) == "CONFIRMATION_REQUIRED":
        from ai_tools import confirmations

        token = (result.data or {}).get("confirmation", {}).get("token")
        assert token, f"CONFIRMATION_REQUIRED without token for '{name}'"
        confirmations.set_status(token, "approved")
        result = tool.run({**params, "confirm": True}, context)
    return result


@pytest.fixture
def mock_resource_limit():
    """Patch _check_resource_limit to always allow.

    Usage in tests:
        def test_create(self, tool_context, mock_resource_limit):
            result = run_tool("create_dataset", {...}, tool_context)
    """
    with patch(
        "model_hub.services.dataset_service._check_resource_limit",
        return_value=True,
    ):
        yield


@pytest.fixture
def mock_temporal():
    """Patch Temporal client to prevent real workflow starts.

    Returns the mock so tests can assert calls:
        def test_start(self, mock_temporal, tool_context):
            result = run_tool("create_experiment", {...}, tool_context)
            mock_temporal.start_workflow.assert_called_once()
    """
    mock_client = MagicMock()
    mock_client.start_workflow = MagicMock(return_value="fake-workflow-id")
    mock_client.execute_workflow = MagicMock(return_value="fake-result")

    with patch("tfc.temporal.get_client", return_value=mock_client):
        yield mock_client
