"""Contract and routing coverage for the MCP workflow expansion."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.urls import resolve
from jsonschema import Draft7Validator

from mcp_server.generated_registry import GeneratedToolRegistry
from mcp_server.tool_generation import ToolGenerationError, generate_tool_manifest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "api_contracts/openapi/swagger.json"
CATALOG = Path(__file__).resolve().parents[1] / "catalog/tools.yaml"

OPERATIONS = [
    (
        "create_dataset_column",
        "POST",
        "/model-hub/develops/{dataset_id}/add_static_column/",
    ),
    (
        "rename_dataset_column",
        "PUT",
        "/model-hub/develops/{dataset_id}/update_column_name/{column_id}/",
    ),
    (
        "configure_dataset_evaluation",
        "POST",
        "/model-hub/develops/{dataset_id}/add_user_eval/",
    ),
    (
        "list_dataset_evaluations",
        "GET",
        "/model-hub/develops/{dataset_id}/get_evals_list/",
    ),
    ("add_dataset_prompt", "POST", "/model-hub/develops/add_run_prompt_column/"),
    ("update_dataset_prompt", "POST", "/model-hub/develops/edit_run_prompt_column/"),
    ("run_dataset_prompts", "POST", "/model-hub/run-prompt-for-rows/"),
    (
        "get_dataset_prompt_stats",
        "GET",
        "/model-hub/dataset/{dataset_id}/run-prompt-stats/",
    ),
    ("get_prompt_run", "GET", "/model-hub/prompt-templates/{id}/get-run-status/"),
    ("create_agent", "POST", "/simulate/agent-definitions/create/"),
    ("update_agent", "PUT", "/simulate/agent-definitions/{agent_id}/edit/"),
    (
        "create_agent_version",
        "POST",
        "/simulate/agent-definitions/{agent_id}/versions/create/",
    ),
    ("create_scenario", "POST", "/simulate/scenarios/create/"),
    ("update_scenario", "PUT", "/simulate/scenarios/{scenario_id}/edit/"),
    ("list_simulation_tests", "GET", "/simulate/run-tests/"),
    ("get_simulation_test", "GET", "/simulate/run-tests/{run_test_id}/"),
    ("create_simulation_test", "POST", "/simulate/run-tests/create/"),
    ("run_simulation", "POST", "/simulate/run-tests/{run_test_id}/execute/"),
    ("update_eval_task", "PATCH", "/tracer/eval-task/update_eval_task/"),
    ("pause_eval_task", "POST", "/tracer/eval-task/pause_eval_task/"),
    ("resume_eval_task", "POST", "/tracer/eval-task/unpause_eval_task/"),
]


@pytest.mark.parametrize("name,method,path", OPERATIONS)
def test_workflow_tools_reuse_existing_operations(name, method, path):
    tool = GeneratedToolRegistry.from_manifest().get(name)
    assert tool.request["method"] == method
    assert tool.request["path"] == path
    assert tool.is_available()
    concrete = path
    for field in tool.request["parameters"]["path"]:
        concrete = concrete.replace("{" + field + "}", str(uuid4()))
    match = resolve(concrete)
    view = match.func
    actions = getattr(view, "actions", {})
    cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
    assert method.lower() in actions or hasattr(cls, method.lower())
    assert tool.annotations["readOnlyHint"] is (method == "GET")


def test_execution_arguments_and_safety_hints():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT, CATALOG)["tools"]
    }
    assert (
        "template_version" in tools["get_prompt_run"]["request"]["parameters"]["query"]
    )
    for name in ("pause_eval_task", "resume_eval_task"):
        assert "eval_task_id" in tools[name]["request"]["parameters"]["query"]
    update = tools["update_eval_task"]
    assert update["annotations"]["destructiveHint"] is True
    assert update["annotations"]["idempotentHint"] is False
    # Rerunning an eval task dispatches work to external model providers.
    assert update["annotations"]["openWorldHint"] is True
    assert tools["run_prompt"]["annotations"]["openWorldHint"] is True
    assert tools["get_prompt_run"]["annotations"]["openWorldHint"] is False
    # PATCH bodies are partial; path-less custom PATCH still exposes the
    # fields without copying the create serializer's required list.
    assert {"eval_task_id", "edit_type"} <= set(update["inputSchema"]["properties"])
    assert "eval_task_id" not in update["inputSchema"].get("required", [])
    assert {"run_prompt_ids"} <= set(
        tools["run_dataset_prompts"]["inputSchema"]["required"]
    )
    assert {"agent_definition_id", "scenario_ids", "name"} <= set(
        tools["create_simulation_test"]["inputSchema"]["required"]
    )
    assert tools["create_agent_version"]["annotations"]["idempotentHint"] is False


@pytest.mark.parametrize("hint", ["false", 0, None, {}])
def test_invalid_idempotency_override_fails_generation(tmp_path, hint):
    catalog = {
        "tools": [
            {
                "name": "invalid_hint",
                "group": "context",
                "description": "Invalid hint",
                "operation": {"method": "GET", "path": "/accounts/user-info/"},
                "idempotent": hint,
            }
        ]
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ToolGenerationError, match="Invalid idempotent hint"):
        generate_tool_manifest(CONTRACT, path)


def test_workflow_schemas_accept_real_nested_payloads():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT, CATALOG)["tools"]
    }
    identifier = str(uuid4())
    payloads = {
        "add_dataset_prompt": {
            "dataset_id": identifier,
            "name": "answer",
            "config": {
                "model": "test-model",
                "messages": [{"role": "user", "content": "{{question}}"}],
                "run_prompt_config": {"temperature": None},
                "response_format": {"type": "text"},
            },
        },
        "configure_dataset_evaluation": {
            "dataset_id": identifier,
            "name": "quality",
            "template_id": identifier,
            "config": {"text": "question"},
            "run": False,
        },
        "run_dataset_prompts": {
            "run_prompt_ids": [identifier],
            "row_ids": [],
            "selected_all_rows": True,
        },
        "create_agent": {
            "agent_name": "Support",
            "agent_type": "text",
            "commit_message": "Initial",
        },
        "create_simulation_test": {
            "name": "Support checks",
            "agent_definition_id": identifier,
            "agent_version": identifier,
            "scenario_ids": [identifier],
        },
        "run_simulation": {"run_test_id": identifier},
        "get_prompt_run": {"id": identifier, "template_version": "v1"},
    }
    for name, payload in payloads.items():
        validator = Draft7Validator(tools[name]["inputSchema"])
        assert not list(validator.iter_errors(payload)), name
        assert not validator.is_valid({**payload, "unexpected_field": True}), name
