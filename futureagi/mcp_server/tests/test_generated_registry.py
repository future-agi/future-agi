import json

import pytest

from mcp_server.generated_registry import (
    GeneratedToolRegistry,
    GeneratedToolRegistryError,
)


def test_committed_generated_registry_loads_all_tools():
    registry = GeneratedToolRegistry.from_manifest()

    assert registry.count() == 93
    assert registry.get("list_datasets").group == "datasets"
    assert registry.get("get_dashboard").group == "dashboards"
    assert registry.get("missing") is None


def test_registry_returns_mcp_protocol_tools():
    registry = GeneratedToolRegistry.from_manifest()

    tool = registry.get("create_prompt_template").to_mcp_tool()

    assert tool.name == "create_prompt_template"
    assert tool.inputSchema["type"] == "object"
    assert tool.annotations.readOnlyHint is False


def test_registry_filters_tools_by_group():
    registry = GeneratedToolRegistry.from_manifest()

    assert [tool.name for tool in registry.list_by_groups(["context"])] == [
        "whoami",
        "list_workspaces",
        "get_workspace",
    ]
    assert {tool.name for tool in registry.list_by_groups(["prompts"])} == {
        "list_prompt_templates",
        "get_prompt_template",
        "list_prompt_versions",
        "create_prompt_template",
        "update_prompt_template",
        "run_prompt",
        "get_prompt_run",
    }


def test_registry_rejects_incorrect_manifest_count(tmp_path):
    path = tmp_path / "tools.generated.json"
    path.write_text(json.dumps({"tool_count": 1, "tools": []}), encoding="utf-8")

    with pytest.raises(GeneratedToolRegistryError, match="declares 1 tools"):
        GeneratedToolRegistry.from_manifest(path)
