"""Phase 4C — navigate_to_page whitelist + chart-answer discovery.

The navigate tool is the ONLY emitter feeding the frontend's `navigate` WS
event, so its whitelist is the open-redirect boundary: every rejection case
here is a path an LLM (or a jailbroken prompt) could try to push the user's
browser to. No DB needed — the tool is pure validation.

Also pins the Phase 4C discovery contract: search_tools must surface
`render_widget` for chart-verb queries and `navigate_to_page` for
take-me-there queries, so both stay reachable in ANY mode (outside the
Imagine page-force) via the search -> auto-pin loop.
"""

import json

import pytest

from ai_tools.base import ToolContext
from ai_tools.registry import registry
from ai_tools.tools.context.navigate import (
    DETAIL_ROUTES,
    LIST_ROUTES,
    PAGE_ALIASES,
    NavigateToPageTool,
    validate_path,
)

UUID = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


def _ctx() -> ToolContext:
    # navigate_to_page never touches user/org/workspace — pure validation.
    return ToolContext(user=None, organization=None, workspace=None)


class TestValidatePathAccepts:
    @pytest.mark.parametrize("path", LIST_ROUTES)
    def test_every_list_route(self, path):
        assert validate_path(path) == path

    @pytest.mark.parametrize("template", sorted(DETAIL_ROUTES.values()))
    def test_every_detail_route_with_uuid(self, template):
        path = template.replace("<id>", UUID)
        assert validate_path(path) == path

    def test_trailing_slash_is_canonicalized(self):
        assert validate_path("/dashboard/alerts/") == "/dashboard/alerts"

    def test_surrounding_whitespace_is_stripped(self):
        assert validate_path("  /dashboard/alerts ") == "/dashboard/alerts"

    @pytest.mark.parametrize("alias,expected", sorted(PAGE_ALIASES.items()))
    def test_friendly_alias(self, alias, expected):
        assert validate_path(alias) == expected

    def test_alias_is_case_insensitive(self):
        assert validate_path("Alerts") == "/dashboard/alerts"

    def test_slug_id_segment(self):
        assert (
            validate_path("/dashboard/observe/my-project_01")
            == "/dashboard/observe/my-project_01"
        )


class TestValidatePathRejects:
    @pytest.mark.parametrize(
        "path",
        [
            "https://evil.com/dashboard/alerts",  # absolute external URL
            "http://evil.com",  # scheme
            "//evil.com/dashboard/alerts",  # scheme-relative redirect
            "javascript:alert(1)",  # scheme smuggling
            "/dashboard/alerts?next=//evil.com",  # query string
            "/dashboard/alerts#frag",  # fragment
            "/dashboard/../admin",  # traversal
            "/dashboard/observe/../../etc/passwd",  # traversal in id slot
            "/dashboard/observe/a/b",  # extra path segment in id slot
            "/dashboard/observe/" + "x" * 65,  # id segment too long
            "/dashboard/observe/.hidden",  # id can't start with punctuation
            "/dashboard\\alerts",  # backslash
            "/dashboard/al erts",  # interior whitespace
            "/admin",  # outside /dashboard
            "/dashboard/secret-page",  # not in the whitelist
            "/dashboard",  # bare root is not a destination
            "/dashboard/develop/experiment/" + UUID,  # detail template mismatch
            "",  # empty
            "   ",  # blank
            "not-a-page-alias",  # unknown alias
        ],
    )
    def test_rejected(self, path):
        assert validate_path(path) is None

    def test_non_string_rejected(self):
        assert validate_path(None) is None
        assert validate_path(123) is None


class TestNavigateToPageTool:
    def test_registered_with_read_policy(self):
        tool = registry.get("navigate_to_page")
        assert tool is not None
        assert tool.category == "context"
        assert tool.execution_policy == "read"

    def test_valid_path_returns_navigated_payload(self):
        result = NavigateToPageTool().run({"path": "/dashboard/alerts"}, _ctx())
        assert not result.is_error
        body = json.loads(result.content)
        assert body["navigated"] is True
        assert body["path"] == "/dashboard/alerts"
        assert result.data == {"path": "/dashboard/alerts"}

    def test_alias_resolves_to_canonical_path(self):
        result = NavigateToPageTool().run({"path": "alerts"}, _ctx())
        assert not result.is_error
        assert json.loads(result.content)["path"] == "/dashboard/alerts"

    def test_arbitrary_path_is_an_error_with_allowed_routes(self):
        result = NavigateToPageTool().run(
            {"path": "https://evil.com/phish"}, _ctx()
        )
        assert result.is_error
        # The error must teach the model the whitelist so it self-corrects.
        assert "/dashboard/alerts" in result.content

    def test_error_result_carries_no_path_data(self):
        # The agent loop emits `navigate` from the parsed result content —
        # an error must never carry an emittable path.
        result = NavigateToPageTool().run({"path": "/admin"}, _ctx())
        assert result.is_error
        assert not (result.data or {}).get("path")


class TestPhase4CDiscovery:
    """search_tools must keep navigate/chart answers reachable in any mode."""

    def _search(self, query):
        tool = registry.get("search_tools")
        result = tool.run({"query": query, "limit": 12}, _ctx())
        assert not result.is_error
        return [t["name"] for t in (result.data or {}).get("tools", [])]

    @pytest.mark.parametrize(
        "query",
        [
            "take me to the alerts page",
            "navigate to my datasets",
            "go to the observe page",
        ],
    )
    def test_navigate_queries_surface_navigate_to_page(self, query):
        assert "navigate_to_page" in self._search(query)

    @pytest.mark.parametrize(
        "query",
        [
            "chart my token usage",
            "visualize latency per model",
            "plot this as a bar graph",
            "render a widget with these numbers",
        ],
    )
    def test_chart_queries_surface_render_widget(self, query):
        assert "render_widget" in self._search(query)
