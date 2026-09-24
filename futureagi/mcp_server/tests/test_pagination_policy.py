"""MCP page-size limits must apply before invoking the underlying REST view."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from jsonschema import Draft7Validator
from rest_framework.response import Response

from mcp_server.api_executor import APIExecutionError, MCPRequestContext, executor
from mcp_server.generated_registry import registry
from mcp_server.tests.test_tool_generation import CATALOG_PATH, CONTRACT_PATH
from mcp_server.tool_generation import ToolGenerationError, generate_tool_manifest

PAGE_SIZE_FIELDS = {"limit", "page_size"}
PAGINATED_TOOLS = [
    (tool, field)
    for tool in registry.list_all()
    for field in tool.input_schema["properties"]
    if field in PAGE_SIZE_FIELDS
]


@pytest.mark.parametrize(
    "tool,field", PAGINATED_TOOLS, ids=[f"{t.name}.{f}" for t, f in PAGINATED_TOOLS]
)
def test_every_page_size_is_bounded(tool, field):
    schema = tool.input_schema["properties"][field]
    validator = Draft7Validator(schema)
    maximum = 50 if tool.name == "get_dashboard_filter_values" else 100
    assert schema["maximum"] == maximum
    assert validator.is_valid(maximum)
    assert not validator.is_valid(maximum + 1)
    for invalid in (0, -1, None, True, "100"):
        assert not validator.is_valid(invalid)


@pytest.mark.parametrize(
    "tool,field", PAGINATED_TOOLS, ids=[f"{t.name}.{f}" for t, f in PAGINATED_TOOLS]
)
def test_the_cap_never_introduces_a_page_size_default(tool, field):
    """Omitting a page size must keep selecting the view's own default.

    Every one of these endpoints already applies a default page size of its own.
    A default invented here would silently resize pages for MCP callers while
    claiming the REST contract is unchanged, so the generator must not add one
    and the executor must have no defaults to inject.
    """
    assert "pagination_defaults" not in tool.request
    schema = tool.input_schema["properties"][field]
    if "default" in schema:
        # Only a default the source contract already declared may survive.
        assert Draft7Validator(schema).is_valid(schema["default"])


def _generate_policy(tmp_path, pagination, parameter=None):
    contract = {
        "swagger": "2.0",
        "paths": {
            "/things/": {
                "get": {
                    "parameters": [
                        parameter or {"name": "limit", "in": "query", "type": "integer"}
                    ]
                }
            }
        },
    }
    catalog = {
        "tools": [
            {
                "name": "list_things",
                "group": "context",
                "description": "List things",
                "operation": {"method": "GET", "path": "/things/"},
                "pagination": pagination,
            }
        ]
    }
    contract_path = tmp_path / "swagger.json"
    catalog_path = tmp_path / "tools.json"
    contract_path.write_text(json.dumps(contract))
    catalog_path.write_text(json.dumps(catalog))
    manifest = generate_tool_manifest(contract_path, catalog_path)
    assert json.loads(contract_path.read_text()) == contract
    return manifest["tools"][0]


@pytest.mark.parametrize(
    "policy",
    [
        None,
        [],
        {"missing": {"maximum": 100}},
        {"limit": 100},
        {"limit": {}},
        {"limit": {"maximum": 100, "typo": 1}},
        # A page-size default is not the catalog's to set; see the test above.
        {"limit": {"maximum": 100, "default": 20}},
        {"limit": {"default": 20}},
        {"limit": {"maximum": True}},
        {"limit": {"maximum": "100"}},
        {"limit": {"maximum": 0}},
        {"limit": {"maximum": -1}},
    ],
)
def test_generator_rejects_invalid_pagination_policy(tmp_path, policy):
    with pytest.raises(ToolGenerationError, match="Invalid pagination policy"):
        _generate_policy(tmp_path, policy)


@pytest.mark.parametrize(
    "parameter",
    [
        {"name": "limit", "in": "query", "type": "string"},
        {"name": "limit", "in": "path", "type": "integer"},
    ],
)
def test_pagination_policy_requires_an_integer_request_field(tmp_path, parameter):
    with pytest.raises(ToolGenerationError, match="Invalid pagination policy"):
        _generate_policy(tmp_path, {"limit": {"maximum": 100}}, parameter)


def test_generator_keeps_a_stricter_rest_bound(tmp_path):
    parameter = {
        "name": "limit",
        "in": "query",
        "type": "integer",
        "minimum": 5,
        "maximum": 10,
    }
    tool = _generate_policy(tmp_path, {"limit": {"maximum": 100}}, parameter)
    assert tool["inputSchema"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 5,
        "maximum": 10,
    }


def test_generator_preserves_a_default_the_contract_declared(tmp_path):
    parameter = {
        "name": "limit",
        "in": "query",
        "type": "integer",
        "default": 10,
    }
    tool = _generate_policy(tmp_path, {"limit": {"maximum": 100}}, parameter)
    assert tool["inputSchema"]["properties"]["limit"]["default"] == 10
    assert tool["inputSchema"]["properties"]["limit"]["maximum"] == 100


def test_mcp_cap_does_not_change_the_rest_contract(tmp_path):
    import yaml

    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    for entry in catalog["tools"]:
        entry.pop("pagination", None)
    catalog_path = tmp_path / "rest-only.json"
    catalog_path.write_text(json.dumps(catalog))
    tools = {
        t["name"]: t
        for t in generate_tool_manifest(CONTRACT_PATH, catalog_path)["tools"]
    }
    for name, field in [
        ("list_agents", "limit"),
        ("list_projects", "page_size"),
        ("list_prompt_versions", "limit"),
    ]:
        schema = tools[name]["inputSchema"]["properties"][field]
        assert "maximum" not in schema
        assert Draft7Validator(schema).is_valid(200)


@pytest.mark.parametrize(
    "name,field,maximum",
    [
        ("list_agents", "limit", 100),
        ("list_eval_templates", "page_size", 100),
        ("get_dashboard_filter_values", "page_size", 50),
    ],
)
@pytest.mark.parametrize("value", ["oversized", 0, -1, None, "100", True])
def test_executor_rejects_invalid_page_sizes_before_dispatch(
    name, field, maximum, value
):
    if value == "oversized":
        value = maximum + 1
    with patch("mcp_server.api_executor.resolve") as resolve:
        with pytest.raises(APIExecutionError) as error:
            executor.execute_sync(registry.get(name), {field: value}, None)
    assert error.value.status_code == 400
    resolve.assert_not_called()


def _dispatch(name, arguments, user, workspace):
    tool = registry.get(name)
    view = Mock(return_value=Response({"result": {"ok": True}}))
    match = SimpleNamespace(func=view, args=(), kwargs={})
    with patch("mcp_server.api_executor.resolve", return_value=match):
        executor.execute_sync(
            tool, arguments, MCPRequestContext(user, user.organization, workspace)
        )
    request = view.call_args.args[0]
    return tool, request


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,field,maximum",
    [
        ("list_agents", "limit", 100),
        ("list_eval_templates", "page_size", 100),
        ("get_dashboard_filter_values", "page_size", 50),
        ("list_annotation_queues", "page_size", 100),
        ("list_annotation_queues", "limit", 100),
    ],
)
def test_executor_sends_the_boundary_value_unchanged(
    user, workspace, name, field, maximum
):
    arguments = {field: maximum}
    original = arguments.copy()
    tool, request = _dispatch(name, arguments, user, workspace)
    payload = (
        json.loads(request.body)
        if field in tool.request["parameters"]["body"]
        else request.GET
    )
    assert int(payload[field]) == maximum
    if name == "list_annotation_queues":
        # limit and page_size are aliases with a precedence order in the view;
        # sending the one the caller did not supply would override their choice.
        alias = "limit" if field == "page_size" else "page_size"
        assert alias not in payload
    assert arguments == original


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,field",
    [
        ("list_agents", "limit"),
        ("list_eval_templates", "page_size"),
        ("list_annotation_queues", "limit"),
        ("list_annotation_queues", "page_size"),
        ("list_dashboard_metrics", "page_size"),
    ],
)
def test_executor_omits_the_page_size_the_caller_omitted(user, workspace, name, field):
    """The view's own default must still apply when the caller says nothing."""
    tool, request = _dispatch(name, {}, user, workspace)
    if field in tool.request["parameters"]["body"]:
        # An omitted body page size leaves the body empty, so none is sent.
        payload = json.loads(request.body) if request.body else {}
    else:
        payload = request.GET
    assert field not in payload
