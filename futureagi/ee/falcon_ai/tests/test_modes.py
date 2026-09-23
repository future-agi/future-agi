"""Falcon tool routing over the generated MCP catalog plus native tools."""

import pytest

from ai_tools.generated import GeneratedAPITool
from ai_tools.registry import registry
from ee.falcon_ai.agent import _extract_primary_entity_id
from ee.falcon_ai.modes import (
    ALL_CATEGORIES,
    COMMON_TOOLS,
    CORE_TOOLS,
    MODES,
    PAGE_TO_MODE,
    detect_mode,
    filter_tools_for_message,
    load_tools_for_mode,
)

pytestmark = pytest.mark.django_db


def _names(tools):
    return {t.name for t in tools}


def test_core_and_common_tools_all_resolve():
    missing = [name for name in CORE_TOOLS + COMMON_TOOLS if registry.get(name) is None]
    assert missing == []


def test_every_mode_category_has_registered_tools():
    for category in ALL_CATEGORIES:
        assert registry.list_by_category(category), category
    for mode, config in MODES.items():
        for category in config["categories"]:
            assert category in ALL_CATEGORIES or category == "web", (mode, category)


def test_general_mode_mixes_catalog_and_native_tools():
    tools = load_tools_for_mode("general")
    names = _names(tools)

    # Catalog tools arrive through the generated wrapper
    assert {
        "list_datasets",
        "search_traces",
        "list_dashboards",
        "list_gateways",
    } <= names
    assert isinstance(registry.get("list_datasets"), GeneratedAPITool)
    # Natives with no API equivalent stay
    assert {"search", "read_schema", "search_docs", "render_widget"} <= names
    assert {"list_users", "create_api_key", "create_workspace"} <= names
    assert {"explore_trace_legacy", "read_trace_span", "analyze_error_cluster"} <= names
    # Nothing left from the retired proxy tools
    assert not {"add_columns", "get_cost_breakdown", "list_alert_monitors"} & names


def test_tracing_mode_includes_error_feed_and_dashboards():
    names = _names(load_tools_for_mode("tracing"))
    assert {"get_trace", "list_error_clusters", "query_dashboard_metrics"} <= names
    assert "create_scenario" not in names


def test_gateway_pages_route_to_gateway_mode():
    assert detect_mode("gateway", "show me the request logs") == "gateway"
    assert {"list_gateways", "get_gateway_analytics", "get_usage_overview"} <= _names(
        load_tools_for_mode("gateway")
    )


def test_annotation_pages_route_to_datasets_mode():
    for page in ("annotations", "annotation_queues", "annotation_labels"):
        assert PAGE_TO_MODE[page] == "datasets"
    assert "submit_annotation" in _names(load_tools_for_mode("datasets"))


def test_skill_tool_names_are_loaded():
    class Skill:
        tool_names = ["evaluate_with_agent", "get_dashboard"]

    names = _names(load_tools_for_mode("prompts", active_skill=Skill()))
    assert {"evaluate_with_agent", "get_dashboard"} <= names


def test_builtin_skills_only_reference_registered_tools():
    from ee.falcon_ai.builtin_skills_loader import load_builtin_skills

    unknown = {
        skill["slug"]: [n for n in skill["tool_names"] if registry.get(n) is None]
        for skill in load_builtin_skills()
    }
    assert {slug: names for slug, names in unknown.items() if names} == {}


def test_message_filter_keeps_catalog_discovery_tools():
    tools = load_tools_for_mode("general")
    filtered = filter_tools_for_message(tools, "how much did we spend this week?")
    names = _names(filtered)
    assert len(filtered) <= 40
    assert {"get_usage_overview", "list_dashboards", "list_datasets"} <= names


def test_primary_entity_id_prefers_top_level_json_id():
    nested = '{"workspace": {"id": "11111111-1111-1111-1111-111111111111"}, "id": "22222222-2222-2222-2222-222222222222"}'
    assert _extract_primary_entity_id(nested) == "22222222-2222-2222-2222-222222222222"
    assert (
        _extract_primary_entity_id("Created `33333333-3333-3333-3333-333333333333`")
        == "33333333-3333-3333-3333-333333333333"
    )
    assert _extract_primary_entity_id('{"id": 12, "name": "x"}') is None
