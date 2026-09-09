import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from mcp_server.tool_generation import ToolGenerationError, generate_tool_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "api_contracts/openapi/swagger.json"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog/tools.yaml"


def test_committed_catalog_generates_expected_tools():
    manifest = generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)

    assert manifest["tool_count"] == 60
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
