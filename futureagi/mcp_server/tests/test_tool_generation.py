import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from mcp_server.tool_generation import (
    ToolGenerationError,
    _json_schema,
    generate_tool_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "api_contracts/openapi/swagger.json"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog/tools.yaml"


def test_committed_catalog_generates_expected_tools():
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)

    assert manifest["tool_count"] == 100
    tool_names = [tool["name"] for tool in manifest["tools"]]
    assert len(tool_names) == len(set(tool_names))
    assert {
        "whoami",
        "list_datasets",
        "create_prompt_template",
        "test_evaluation",
        "search_traces",
        "submit_annotation",
        "create_experiment",
        "get_test_execution_analytics",
        "get_gateway_analytics",
        "list_dashboards",
        "query_dashboard_widget",
        "get_usage_overview",
    }.issubset(tool_names)
    assert len(manifest["source_contract_sha256"]) == 64
    for tool in manifest["tools"]:
        Draft7Validator.check_schema(tool["inputSchema"])


def test_generator_maps_path_query_and_body_parameters():
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    assert "id" in tools["get_prompt_template"]["request"]["parameters"]["path"]
    assert tools["list_datasets"]["request"]["parameters"]["query"]
    assert tools["create_prompt_template"]["request"]["parameters"]["body"]
    assert tools["update_prompt_template"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    for tool in tools.values():
        request = tool["request"]
        assert set(re.findall(r"{([^}]+)}", request["path"])) == set(
            request["parameters"]["path"]
        )
        mapped_fields = {
            name for fields in request["parameters"].values() for name in fields
        }
        assert mapped_fields <= set(tool["inputSchema"]["properties"])


def test_open_world_hint_is_reviewed_catalog_metadata():
    """Tools that reach an external provider must advertise an open domain.

    The generator cannot infer this: the view looks local, and only a reviewer
    knows a run/eval/simulation call ends up at a third-party model provider.
    """
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    external = {
        name
        for name, tool in tools.items()
        if tool["annotations"]["openWorldHint"]
    }
    assert external == {
        "run_prompt",
        "run_dataset_prompts",
        "run_dataset_evals",
        "test_evaluation",
        "run_simulation",
        "create_experiment",
        "create_optimization_run",
        "create_eval_task",
        "update_eval_task",
        "resume_eval_task",
    }
    # Pausing stops work; it never reaches a provider.
    assert tools["pause_eval_task"]["annotations"]["openWorldHint"] is False
    assert tools["whoami"]["annotations"]["openWorldHint"] is False


@pytest.mark.parametrize("declared", [True, False])
def test_generator_honours_the_open_world_override(tmp_path, declared):
    catalog = {
        "expected_tool_count": 1,
        "tools": [
            {
                "name": "whoami",
                "group": "context",
                "description": "Current user",
                "operation": {"method": "GET", "path": "/accounts/user-info/"},
                "open_world": declared,
            }
        ],
    }
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    manifest = generate_tool_manifest(CONTRACT_PATH, catalog_path)
    assert manifest["tools"][0]["annotations"]["openWorldHint"] is declared


def test_generator_rejects_a_non_boolean_open_world_hint(tmp_path):
    catalog = {
        "expected_tool_count": 1,
        "tools": [
            {
                "name": "whoami",
                "group": "context",
                "description": "Current user",
                "operation": {"method": "GET", "path": "/accounts/user-info/"},
                "open_world": "yes",
            }
        ],
    }
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ToolGenerationError, match="Invalid open_world hint"):
        generate_tool_manifest(CONTRACT_PATH, catalog_path)


def test_generator_rejects_missing_operation(tmp_path):
    catalog = {
        "expected_tool_count": 1,
        "tools": [
            {
                "name": "missing",
                "group": "context",
                "description": "Missing operation",
                "operation": {"method": "GET", "path": "/does-not-exist/"},
            }
        ],
    }
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(ToolGenerationError, match="OpenAPI operation not found"):
        generate_tool_manifest(CONTRACT_PATH, catalog_path)


def test_generator_rejects_duplicate_tool_names(tmp_path):
    entry = {
        "name": "duplicate",
        "group": "context",
        "description": "Duplicate operation",
        "operation": {"method": "GET", "path": "/accounts/user-info/"},
    }
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(
        json.dumps({"expected_tool_count": 2, "tools": [entry, entry]}),
        encoding="utf-8",
    )

    with pytest.raises(ToolGenerationError, match="Duplicate MCP tool name"):
        generate_tool_manifest(CONTRACT_PATH, catalog_path)


@pytest.mark.parametrize(
    "schema,accepted,rejected",
    [
        ({"type": "string", "enum": ["a"], "x-nullable": True}, [None, "a"], ["b", 1]),
        ({"type": "object", "x-string-or-array": True}, ["text", [1, 2]], [{}, 1]),
        ({"type": "object", "x-string-or-object": True}, ["text", {"a": 1}], [[1], 1]),
        ({"type": "object", "x-json-value": True}, [None, 1, "text", [], {}], []),
    ],
)
def test_swagger_extensions_preserve_accepted_values(schema, accepted, rejected):
    validator = Draft7Validator(_json_schema(schema))
    for value in accepted:
        assert validator.is_valid(value), value
    for value in rejected:
        assert not validator.is_valid(value), value


def test_nullable_reference_and_nested_fields():
    schema = {
        "type": "object",
        "properties": {"item": {"$ref": "#/definitions/Item", "x-nullable": True}},
        "definitions": {
            "Item": {
                "type": "object",
                "properties": {"label": {"type": "string", "x-nullable": True}},
            }
        },
    }
    validator = Draft7Validator(_json_schema(schema))
    for value in [{"item": None}, {"item": {"label": None}}, {"item": {"label": "a"}}]:
        assert validator.is_valid(value)
    assert not validator.is_valid({"item": {"label": 42}})


def test_generated_tools_accept_real_prompt_inputs():
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)
    tools = {tool["name"]: tool for tool in manifest["tools"]}
    tool_id = "00000000-0000-0000-0000-000000000001"
    Draft7Validator(tools["create_prompt_template"]["inputSchema"]).validate(
        {"name": "test", "description": None}
    )
    Draft7Validator(tools["update_prompt_template"]["inputSchema"]).validate(
        {"id": tool_id, "description": "updated", "prompt_folder": None}
    )
    Draft7Validator(tools["run_prompt"]["inputSchema"]).validate(
        {
            "id": tool_id,
            "version": "v1",
            "is_run": "prompt",
            "prompt_config": [
                {
                    "messages": [{"role": "user", "content": "Hello"}],
                    "configuration": {"model": "test-model"},
                }
            ],
        }
    )


def test_catalog_exposes_actual_pagination_and_typed_dataset_rows():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    for name in [
        "list_projects",
        "list_eval_groups",
        "list_spans",
        "search_traces",
        "list_sessions",
    ]:
        fields = tools[name]["inputSchema"]["properties"]
        assert {"page_number", "page_size"} <= fields.keys(), name
        assert not {"page", "limit"} & fields.keys(), name
    assert {"page", "limit", "search", "status"} <= tools["list_test_executions"][
        "inputSchema"
    ]["properties"].keys()
    assert {"page", "limit"} <= tools["list_prompt_versions"]["inputSchema"][
        "properties"
    ].keys()
    for name in ["list_projects", "list_eval_groups"]:
        assert (
            tools[name]["inputSchema"]["properties"]["page_size"].get("maximum") is None
        ), name
    assert (
        tools["list_test_executions"]["inputSchema"]["properties"]["limit"].get(
            "maximum"
        )
        is None
    )
    assert (
        tools["list_prompt_versions"]["inputSchema"]["properties"]["limit"].get(
            "maximum"
        )
        is None
    )
    assert (
        tools["list_gateway_request_logs"]["inputSchema"]["properties"]["limit"].get(
            "maximum"
        )
        is None
    )
    assert tools["get_knowledge_base"]["request"]["path"] == "/model-hub/kb/{id}/"
    rows = Draft7Validator(tools["add_dataset_rows"]["inputSchema"])
    tool_id = "00000000-0000-0000-0000-000000000001"
    assert rows.is_valid(
        {
            "dataset_id": tool_id,
            "rows": [{"cells": [{"column_name": "input", "value": "hello"}]}],
        }
    )
    # The API has always treated a row without cells as an empty row, so the
    # contract must keep accepting it; cells themselves stay typed.
    assert rows.is_valid({"dataset_id": tool_id, "rows": [{}]})
    assert not rows.is_valid(
        {"dataset_id": tool_id, "rows": [{"cells": [{"value": "hello"}]}]}
    )
    assert not rows.is_valid({"dataset_id": tool_id, "rows": [{"cells": "hello"}]})


def test_dashboard_tools_follow_the_real_dashboard_contract():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    dashboard_tools = {
        name for name, tool in tools.items() if tool["group"] == "dashboards"
    }
    assert dashboard_tools == {
        "list_dashboards",
        "get_dashboard",
        "create_dashboard",
        "update_dashboard",
        "list_dashboard_widgets",
        "get_dashboard_widget",
        "create_dashboard_widget",
        "update_dashboard_widget",
        "query_dashboard_widget",
        "query_dashboard_metrics",
        "list_dashboard_metrics",
        "get_dashboard_filter_values",
    }
    # The dashboard list returns every dashboard at once; it must not advertise
    # the default paginator's parameters, while the widget list really paginates.
    assert tools["list_dashboards"]["inputSchema"]["properties"] == {}
    assert {"dashboard_pk", "page", "limit"} <= tools["list_dashboard_widgets"][
        "inputSchema"
    ]["properties"].keys()
    assert tools["query_dashboard_widget"]["annotations"]["readOnlyHint"] is True
    assert tools["create_dashboard_widget"]["annotations"]["readOnlyHint"] is False

    dashboard_id = "00000000-0000-0000-0000-000000000001"
    create = Draft7Validator(tools["create_dashboard"]["inputSchema"])
    assert create.is_valid({"name": "Latency", "description": "Weekly latency"})
    assert not create.is_valid({"description": "missing name"})
    update = Draft7Validator(tools["update_dashboard"]["inputSchema"])
    assert update.is_valid({"id": dashboard_id, "description": "Weekly latency"})
    assert "name" not in tools["update_dashboard"]["inputSchema"].get("required", [])
    assert not update.is_valid({"description": "missing id"})
    widget_schema = tools["create_dashboard_widget"]["inputSchema"]
    assert "time_range" in widget_schema["properties"]["query_config"]["description"]
    assert "chart_type" in widget_schema["properties"]["chart_config"]["description"]
    widget = Draft7Validator(widget_schema)
    assert widget.is_valid(
        {
            "dashboard_pk": dashboard_id,
            "name": "p95 latency",
            "query_config": {
                "time_range": {"preset": "7D"},
                "metrics": [{"name": "latency", "type": "system_metric"}],
            },
            "chart_config": {"chart_type": "line"},
        }
    )
    query = Draft7Validator(tools["query_dashboard_metrics"]["inputSchema"])
    assert query.is_valid(
        {
            "time_range": {"preset": "7D"},
            "metrics": [
                {"name": "latency", "type": "system_metric", "aggregation": "avg"}
            ],
            "refresh": True,
        }
    )
    assert not query.is_valid({"metrics": []})
    assert not query.is_valid(
        {"time_range": {"preset": "7D"}, "metrics": [{"name": "latency"}]}
    )
    assert {"property_id", "metric_name", "search", "cursor"} <= tools[
        "get_dashboard_filter_values"
    ]["inputSchema"]["properties"].keys()
