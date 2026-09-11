import json

import pytest

from mcp_server.generated_registry import (
    GeneratedToolRegistry,
    GeneratedToolRegistryError,
)


def test_committed_generated_registry_loads_all_tools():
    registry = GeneratedToolRegistry.from_manifest()

    assert registry.count() == 100
    assert registry.get("list_datasets").group == "datasets"
    assert registry.get("get_dashboard").group == "dashboards"
    assert registry.get("list_org_members").group == "users"
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


def test_agents_group_matches_legacy_agents_category():
    """`agents` and `simulation` are separate opt-in groups (and OAuth scopes).

    A connection that only enabled `agents` on dev saw the legacy agents
    category: browsing agents/scenarios/executions plus running a saved test.
    The catalog must keep exposing exactly those tools under `agents`, and the
    rest of the simulate surface under `simulation`, so existing configs keep
    working after the OpenAPI migration.
    """
    registry = GeneratedToolRegistry.from_manifest()

    assert {tool.name for tool in registry.list_by_groups(["agents"])} == {
        "list_agents",
        "get_agent",
        "list_scenarios",
        "list_test_executions",
        "get_test_execution",
        "run_simulation",
    }
    assert {tool.name for tool in registry.list_by_groups(["simulation"])} == {
        "get_scenario",
        "get_test_execution_analytics",
        "create_agent",
        "update_agent",
        "create_agent_version",
        "create_scenario",
        "update_scenario",
        "list_simulation_tests",
        "get_simulation_test",
        "create_simulation_test",
    }
    assert not {tool.name for tool in registry.list_by_groups(["agents"])} & {
        tool.name for tool in registry.list_by_groups(["simulation"])
    }


def test_users_group_covers_members_keys_and_workspaces():
    registry = GeneratedToolRegistry.from_manifest()

    assert {tool.name for tool in registry.list_by_groups(["users"])} == {
        "list_org_members",
        "invite_org_member",
        "list_api_keys",
        "create_api_key",
        "create_workspace",
        "list_workspace_members",
        "update_workspace",
    }
    assert registry.list_by_groups(["docs"]) == []


def test_registry_rejects_incorrect_manifest_count(tmp_path):
    path = tmp_path / "tools.generated.json"
    path.write_text(json.dumps({"tool_count": 1, "tools": []}), encoding="utf-8")

    with pytest.raises(GeneratedToolRegistryError, match="declares 1 tools"):
        GeneratedToolRegistry.from_manifest(path)
