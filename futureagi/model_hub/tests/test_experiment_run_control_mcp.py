"""Generated run-control tools preserve the HTTP gate through MCP dispatch.

Neither route is in the production curated catalog at the EXC8 base. Generate
these two test-only entries from the live schema, not a hand-built tool or a
mocked refusal. This proves the generator/executor/handler boundary without
adding tools to the deployed catalog or hand-editing generated artifacts.
"""

import json
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync

from mcp_server import mcp_app
from mcp_server.api_executor import MCPRequestContext
from mcp_server.generated_registry import GeneratedTool, GeneratedToolRegistry
from mcp_server.tool_generation import generate_tool_manifest
from model_hub.tests.test_experiment_run_control_gate import (
    CODE,
    bind,
    schema_document,
    seed,
    state,
)
from model_hub.tests.test_experiment_run_control_gate import no_control as no_control
from model_hub.tests.test_experiment_run_control_gate import off as off

OPERATIONS = (
    ("resume_experiment", "POST", "resume", "write"),
    ("get_experiment_run_control", "GET", "run-control", "read"),
)


@pytest.fixture
def generated_control_tools(off, tmp_path):
    contract = schema_document()
    # drf-yasg factors a shared URL prefix into basePath; the MCP generator
    # consumes the canonical full-path contract used by the schema export.
    prefix = contract.get("basePath", "").rstrip("/")
    contract["paths"] = {
        f"{prefix}{path}": operation for path, operation in contract["paths"].items()
    }
    contract["basePath"] = "/"
    contract_path = tmp_path / "run-control.openapi.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    catalog_path = tmp_path / "run-control.catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "expected_tool_count": 2,
                "tools": [
                    {
                        "name": name,
                        "group": "experiments",
                        "access": access,
                        "operation": {
                            "method": method,
                            "path": (
                                f"/model-hub/experiments/v2/{{experiment_id}}/{suffix}/"
                            ),
                        },
                    }
                    for name, method, suffix, access in OPERATIONS
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = generate_tool_manifest(contract_path, catalog_path)
    assert manifest["tool_count"] == 2
    return GeneratedToolRegistry(GeneratedTool.from_dict(t) for t in manifest["tools"])


@pytest.mark.django_db
@pytest.mark.parametrize("name", [operation[0] for operation in OPERATIONS])
def test_generated_mcp_tools_return_the_run_control_refusal(
    generated_control_tools,
    user,
    workspace,
    organization,
    no_control,
    monkeypatch,
    name,
):
    experiment, _, variants = seed(organization, workspace, user)
    bind(variants[0], "STOPPED")
    before = state(experiment)
    tool = generated_control_tools.get(name)
    assert (
        "DISABLED unless the operator enables EXPERIMENT_RUN_CONTROL_ENABLED"
        in tool.to_mcp_tool().description
    )
    monkeypatch.setattr(mcp_app, "registry", generated_control_tools)
    # Isolate group selection and rate/usage bookkeeping, not the executor,
    # tenant checks, DRF route, exception handler or runtime admission gate.
    monkeypatch.setattr(
        mcp_app, "_enabled_tools_for_context", lambda context: (object(), {name})
    )
    monkeypatch.setattr(
        "mcp_server.rate_limiter.get_rate_limit_tier", lambda org: "test"
    )
    monkeypatch.setattr("mcp_server.rate_limiter.check_rate_limit", lambda *args: None)
    monkeypatch.setattr("mcp_server.usage_helpers.get_or_create_session", Mock())
    usage = Mock()
    counters = Mock()
    monkeypatch.setattr("mcp_server.usage_helpers.record_usage", usage)
    monkeypatch.setattr("mcp_server.usage_helpers.update_session_counters", counters)
    context = MCPRequestContext(
        user=user, organization=organization, workspace=workspace
    )
    token = mcp_app._request_context.set(context)
    arguments = {"experiment_id": str(experiment.id)}
    if tool.request["method"] == "POST":
        arguments["run_ids"] = [str(variants[0][0].id)]
    try:
        result = async_to_sync(mcp_app.call_generated_tool)(name, arguments)
    finally:
        mcp_app._request_context.reset(token)

    assert result.isError is True
    error = result.structuredContent["error"]
    assert error["code"] == "HTTP_404"
    assert error["details"]["code"] == CODE
    assert "Run control is not available." in json.dumps(error)
    assert json.loads(result.content[0].text) == result.structuredContent
    assert usage.call_args.kwargs["status"] == "error"
    assert counters.call_args.args[1] is True
    assert state(experiment) == before
