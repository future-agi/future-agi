"""Contract coverage for the error feed MCP tools.

The feed is read-only over MCP: list the issues, then pull the pattern
summary and traces behind one. These tests pin the operations those steps
map onto, the read-only hints a client shows before calling them, and the
page-size ceilings that keep a cluster's evidence inside the MCP response
limit.
"""

from uuid import uuid4

import pytest
from django.urls import resolve
from jsonschema import Draft7Validator
from rest_framework.response import Response

from mcp_server.api_executor import DjangoAPIExecutor
from mcp_server.generated_registry import GeneratedToolRegistry
from mcp_server.response_limits import MAX_RESPONSE_BYTES, bounded_response
from mcp_server.tests.test_tool_generation import CATALOG_PATH, CONTRACT_PATH
from mcp_server.tool_generation import generate_tool_manifest

OPERATIONS = [
    ("list_error_clusters", "GET", "/tracer/feed/issues/"),
    ("get_error_cluster", "GET", "/tracer/feed/issues/{cluster_id}/"),
    (
        "get_error_cluster_overview",
        "GET",
        "/tracer/feed/issues/{cluster_id}/overview/",
    ),
    ("list_error_cluster_traces", "GET", "/tracer/feed/issues/{cluster_id}/traces/"),
]


@pytest.fixture(scope="module")
def registry():
    return GeneratedToolRegistry.from_manifest()


@pytest.fixture(scope="module")
def generated():
    return {
        tool["name"]: tool
        for tool in generate_tool_manifest(CONTRACT_PATH, CATALOG_PATH)["tools"]
    }


def test_error_feed_group_is_exactly_the_read_surface(registry):
    """Triage writes stay out: the group only reads the feed.

    Changing an issue's status, severity or assignee, and filing it to
    Linear, are deliberately not exposed — an agent that could resolve an
    issue could hide a live regression from the humans watching the feed.
    """
    assert {tool.name for tool in registry.list_by_groups(["error_feed"])} == {
        name for name, _, _ in OPERATIONS
    }


@pytest.mark.parametrize("name,method,path", OPERATIONS)
def test_tools_reuse_mounted_feed_operations(registry, name, method, path):
    tool = registry.get(name)

    assert tool.request["method"] == method
    assert tool.request["path"] == path
    assert tool.is_available()

    concrete = path
    for field in tool.request["parameters"]["path"]:
        concrete = concrete.replace("{" + field + "}", str(uuid4()))
    view = resolve(concrete).func
    cls = getattr(view, "cls", None) or getattr(view, "view_class", None)
    assert hasattr(cls, method.lower())


@pytest.mark.parametrize("name,_method,_path", OPERATIONS)
def test_reading_the_feed_is_annotated_read_only(registry, name, _method, _path):
    annotations = registry.get(name).annotations
    assert annotations["readOnlyHint"] is True
    assert annotations["destructiveHint"] is False
    assert annotations["openWorldHint"] is False


def test_cluster_detail_drops_the_rca_trail_but_keeps_the_analysis(registry):
    """The stored investigation trail alone runs past the 64KB MCP limit on deep
    analyses, so the tool returns the conclusion and its evidence ids only.
    """
    rca = {
        "synthesis": "Tool calls time out under load",
        "fix": "Raise the tool timeout",
        "confidence": "H",
        "evidence_trace_ids": [str(uuid4())],
        "trace": [{"type": "reasoning", "text": "x" * MAX_RESPONSE_BYTES}],
    }
    tool = registry.get("get_error_cluster")

    result = DjangoAPIExecutor._normalize_response(
        tool, Response({"status": True, "result": {"row": {}, "rca": rca}})
    )

    assert "trace" not in result["rca"]
    assert result["rca"]["synthesis"] == "Tool calls time out under load"
    assert result["rca"]["evidence_trace_ids"] == rca["evidence_trace_ids"]
    bounded_response(result)
    # An issue nobody has analysed carries no rca block at all.
    assert DjangoAPIExecutor._normalize_response(
        tool, Response({"status": True, "result": {"row": {}, "rca": None}})
    ) == {"row": {}, "rca": None}


@pytest.mark.parametrize(
    "name,field,maximum,rest_maximum",
    [
        ("get_error_cluster_overview", "rep_limit", 20, 200),
        ("list_error_cluster_traces", "limit", 100, 500),
    ],
)
def test_cluster_evidence_pages_are_capped_below_the_rest_bound(
    generated, name, field, maximum, rest_maximum
):
    """Cluster evidence carries whole trace inputs and outputs.

    The REST bounds let a caller ask for a page far larger than the 64KB MCP
    response limit, which only surfaces as a size error after the query runs.
    """
    schema = generated[name]["inputSchema"]["properties"][field]
    validator = Draft7Validator(schema)

    assert maximum < rest_maximum
    assert schema["maximum"] == maximum
    assert validator.is_valid(maximum)
    assert not validator.is_valid(maximum + 1)
    # The ceiling must not reject the default the view applies on its own.
    assert validator.is_valid(schema["default"])
