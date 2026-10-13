import json
import re
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, FormatChecker

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

    assert manifest["tool_count"] == 96
    tool_names = [tool["name"] for tool in manifest["tools"]]
    assert len(tool_names) == len(set(tool_names))
    assert {
        "whoami",
        "list_datasets",
        "create_dataset_version",
        "create_prompt_template",
        "test_evaluation",
        "search_traces",
        "submit_annotation",
        "create_experiment",
        "create_external_experiment_run",
        "ingest_external_experiment_results",
        "compare_experiment_runs",
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


def test_snapshot_creation_tool_is_bounded_non_idempotent_write():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    tool = tools["create_dataset_version"]
    assert tool["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    schema = tool["inputSchema"]
    assert set(schema["required"]) == {"dataset_id", "items"}
    assert schema["properties"]["items"]["maxItems"] == 10_000
    assert tool["request"]["parameters"]["path"] == ["dataset_id"]
    validator = Draft7Validator(schema)
    arguments = {"dataset_id": str(uuid.uuid4()), "items": [{"input": "question"}]}
    assert validator.is_valid(arguments)
    assert validator.is_valid({**arguments, "items": []})
    assert validator.is_valid(
        {
            **arguments,
            "items": [
                {
                    "input": {"nested": None},
                    "expected": None,
                    "metadata": {"nullable": None},
                }
            ],
        }
    )
    assert not validator.is_valid({**arguments, "items": [{"input": None}]})
    assert not validator.is_valid({**arguments, "organization": "other"})
    assert not validator.is_valid({**arguments, "items": [{}]})
    assert not validator.is_valid(
        {**arguments, "items": [{"input": "ok", "created_by": "other"}]}
    )


def test_run_comparison_is_a_scoped_read_with_typed_body():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    tool = tools["compare_experiment_runs"]
    assert tool["group"] == "experiments"
    assert tool["request"]["method"] == "POST"
    assert tool["request"]["path"] == "/model-hub/experiment-runs/compare/"
    assert tool["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    required = {"baseline_run_id", "candidate_run_id", "metric_id"}
    fields = required | {
        "passing_score",
        "higher_is_better",
        "release_gate",
        "include_trial_stability",
    }
    schema = tool["inputSchema"]
    assert set(schema["required"]) == required
    assert set(schema["properties"]) == fields
    assert set(tool["request"]["parameters"]["body"]) == fields
    assert tool["request"]["parameters"]["path"] == []
    assert tool["request"]["parameters"]["query"] == []
    arguments = dict.fromkeys(required, "bd8958a7-8eea-46d6-906f-cae2e77d9d61")
    validator = Draft7Validator(schema)
    assert validator.is_valid(arguments)
    assert validator.is_valid({**arguments, "passing_score": None})
    assert validator.is_valid(
        {**arguments, "passing_score": 0.8, "higher_is_better": False}
    )
    assert not validator.is_valid({**arguments, "workspace_id": "other"})
    assert not validator.is_valid({**arguments, "organization_id": "other"})
    assert not validator.is_valid({**arguments, "passing_score": "invalid"})
    assert validator.is_valid({**arguments, "include_trial_stability": True})
    assert not validator.is_valid({**arguments, "include_trial_stability": None})
    assert validator.is_valid({**arguments, "release_gate": {}})
    critical = {"example_id": arguments["baseline_run_id"], "trial_number": 1}
    assert validator.is_valid(
        {
            **arguments,
            "release_gate": {"max_regressions": 1, "critical_cases": [critical]},
        }
    )
    for policy in (
        None,
        {"max_regressions": True},
        {"max_regressions": -1},
        {"max_regressions": 0.5},
        {"unexpected": True},
        {"critical_cases": [{**critical, "unexpected": True}]},
    ):
        assert not validator.is_valid({**arguments, "release_gate": policy})
    assert not validator.is_valid({**arguments, "higher_is_better": "false"})
    assert not validator.is_valid({"baseline_run_id": arguments["baseline_run_id"]})


def test_external_run_creation_tool_pins_a_bounded_plan_without_tenant_overrides():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    tool = tools["create_external_experiment_run"]
    assert tool["group"] == "experiments"
    assert tool["request"]["method"] == "POST"
    assert tool["request"]["path"] == "/model-hub/experiment-runs/"
    assert tool["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    schema = tool["inputSchema"]
    assert set(schema["required"]) == {"run_id", "dataset_version_id", "expected_items"}
    assert schema["properties"]["expected_items"]["minItems"] == 1
    assert schema["properties"]["expected_items"]["maxItems"] == 10000
    assert schema["properties"]["evaluator_metric_ids"]["maxItems"] == 100
    assert tool["request"]["parameters"]["path"] == []
    assert tool["request"]["parameters"]["query"] == []
    assert set(tool["request"]["parameters"]["body"]) == set(schema["properties"])
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    identity = {"example_id": str(uuid.uuid4())}
    arguments = {
        "run_id": str(uuid.uuid4()),
        "dataset_version_id": str(uuid.uuid4()),
        "expected_items": [identity],
        "experiment_id": None,
        "model_config": {"temperature": 0.5, "optional": None},
        "metadata": {"tags": ["candidate", None]},
    }
    assert validator.is_valid(arguments)
    for field in ("organization_id", "workspace_id", "created_by", "task_kind"):
        assert not validator.is_valid({**arguments, field: "override"})
    assert not validator.is_valid({**arguments, "run_id": "not-a-uuid"})
    assert not validator.is_valid({**arguments, "expected_items": []})
    assert not validator.is_valid({**arguments, "expected_items": [identity] * 10001})
    assert not validator.is_valid(
        {**arguments, "evaluator_metric_ids": [str(uuid.uuid4())] * 101}
    )
    assert not validator.is_valid({**arguments, "model_config": None})
    assert not validator.is_valid({**arguments, "metadata": []})
    for extra in (
        {"trial_number": 0},
        {"trial_number": 2147483648},
        {"workspace_id": "override"},
    ):
        assert not validator.is_valid(
            {**arguments, "expected_items": [{**identity, **extra}]}
        )


def test_external_result_tool_preserves_json_outputs_and_strict_nested_scores():
    tools = {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }
    tool = tools["ingest_external_experiment_results"]
    assert tool["group"] == "experiments"
    assert tool["request"]["method"] == "POST"
    assert tool["request"]["path"] == "/model-hub/experiment-runs/{run_id}/results/"
    assert tool["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tool["request"]["parameters"] == {
        "path": ["run_id"],
        "query": [],
        "body": ["items"],
    }
    schema = tool["inputSchema"]
    assert set(schema["required"]) == {"run_id", "items"}
    assert schema["properties"]["items"]["minItems"] == 1
    assert schema["properties"]["items"]["maxItems"] == 100
    result_schema = schema["definitions"]["ExternalRunResult"]
    assert result_schema["properties"]["score_refs"]["maxItems"] == 100
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    score = {
        "metric_id": str(uuid.uuid4()),
        "evaluator_version_id": str(uuid.uuid4()),
        "status": "assessed",
        "value": 0.8,
    }
    item = {
        "example_id": str(uuid.uuid4()),
        "receipt_key": "candidate-case-1-trial-1",
        "status": "completed",
        "score_refs": [score],
    }
    arguments = {"run_id": str(uuid.uuid4()), "items": [item]}
    for output in (
        "answer",
        ["answer", None, {"tool": "search"}],
        None,
        {"answer": True},
    ):
        assert validator.is_valid({**arguments, "items": [{**item, "output": output}]})
    for field in ("organization_id", "workspace_id", "created_by"):
        assert not validator.is_valid({**arguments, field: "override"})
        assert not validator.is_valid(
            {**arguments, "items": [{**item, field: "override"}]}
        )
        assert not validator.is_valid(
            {
                **arguments,
                "items": [{**item, "score_refs": [{**score, field: "override"}]}],
            }
        )
    assert not validator.is_valid({**arguments, "items": []})
    assert not validator.is_valid({**arguments, "items": [item] * 101})
    for extra in (
        {"status": "running"},
        {"receipt_key": ""},
        {"latency_ms": -1},
        {"cost": -1},
        {"score_refs": [score] * 101},
    ):
        assert not validator.is_valid({**arguments, "items": [{**item, **extra}]})
    for extra in (
        {"value": True},
        {"value": "0.8"},
        {"status": "invented"},
        {"evaluator_version_id": None},
    ):
        assert not validator.is_valid(
            {**arguments, "items": [{**item, "score_refs": [{**score, **extra}]}]}
        )


def test_open_world_hint_is_reviewed_catalog_metadata():
    """Tools that reach an external provider must advertise an open domain.

    The generator cannot infer this: the view looks local, and only a reviewer
    knows a run/eval/simulation call ends up at a third-party model provider.
    """
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    external = {
        name for name, tool in tools.items() if tool["annotations"]["openWorldHint"]
    }
    assert external == {
        "run_prompt",
        "add_dataset_prompt",
        "update_dataset_prompt",
        "create_scenario",
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
    for name, field in [
        ("list_projects", "page_size"),
        ("list_eval_groups", "page_size"),
        ("list_test_executions", "limit"),
        ("list_prompt_versions", "limit"),
        ("list_gateway_request_logs", "limit"),
        ("list_optimization_runs", "limit"),
    ]:
        assert tools[name]["inputSchema"]["properties"][field]["maximum"] == 100
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
