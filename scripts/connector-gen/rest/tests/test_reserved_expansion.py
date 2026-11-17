"""Discovery resource names are segmented, pattern-bound paths, not raw URLs."""

import pytest

from restgen.build import ProviderContext, build_tool
from restgen.discovery import DiscoveryReader
from restgen.overlay import ToolOverlay
from restgen.problems import Problems


def generate(*, pattern="^spaces/[^/]+$", fixed=None, rename=None):
    param = {"location": "path", "required": True, "type": "string", "description": "Resource name, for example spaces/demo."}
    if pattern is not None:
        param["pattern"] = pattern
    reader = DiscoveryReader({"rootUrl": "https://meet.googleapis.com/", "resources": {"spaces": {"methods": {"get": {
        "id": "meet.spaces.get", "httpMethod": "GET", "path": "v2/{+name}", "parameters": {"name": param},
    }}}}})
    overlay = ToolOverlay("get_space", "Read a space by resource name, for example spaces/demo.", {"id": "meet.spaces.get"}, "fixture", annotations={"openWorldHint": False})
    if fixed is not None:
        overlay.fixed["name"] = fixed
    if rename:
        overlay.rename["name"] = rename
    problems = Problems("fixture")
    context = ProviderContext("google-meet", None, "auto", "https://meet.googleapis.com", set())
    tool = build_tool(reader, reader.refs(), overlay, context, problems)
    return tool, problems.items


def test_reserved_path_keeps_pattern_and_renamed_placeholder():
    tool, errors = generate(rename="resource")
    assert not errors
    assert tool["operation"]["path"] == "/v2/{+resource}"
    assert tool["inputSchema"]["properties"]["resource"]["pattern"] == "^spaces/[^/]+$"


def test_reserved_fixed_segments_escape_separately():
    tool, errors = generate(fixed="spaces/demo space")
    assert not errors
    assert tool["operation"]["path"] == "/v2/spaces/demo%20space"


@pytest.mark.parametrize("value", ["spaces/..", "spaces/.", "spaces/", "spaces//demo", "spaces/a/b", "rooms/a", "spaces/a?b", "spaces/a#b", "spaces/a\\b", "spaces/a\n", "spaces/a\x00", "spaces/a\x7f", "spaces/a\x85", "spaces/%2f", "spaces/%2E", "spaces/%3f", "spaces/%23", "spaces/%252e%252e"])
def test_reserved_fixed_rejects_traversal_and_encoding(value):
    tool, errors = generate(fixed=value)
    assert tool is None and errors


@pytest.mark.parametrize("pattern", [None, "spaces/.*", "^[invalid$", "^spaces/(?=x).*$"])
def test_reserved_requires_portable_anchored_pattern(pattern):
    tool, errors = generate(pattern=pattern)
    assert tool is None and errors
