"""Falcon tool routing over the generated MCP catalog plus native tools."""

import pytest
from ai_tools.generated import GeneratedAPITool, register_generated_tools
from ai_tools.registry import ToolRegistry, registry
from tfc.ee_loader import has_ee

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


# Categories a specific mode offers that general/auto mode deliberately does
# not: "web" is opt-in per mode, and "visualization" is Imagine-only (its
# render_widget is kept out of general chat).
MODE_ONLY_CATEGORIES = {"web", "visualization"}


def test_every_mode_category_has_registered_tools():
    for category in ALL_CATEGORIES:
        if category == "usage" and not has_ee("ee.cloud"):
            assert not registry.list_by_category(category)
        else:
            assert registry.list_by_category(category), category
    for mode, config in MODES.items():
        for category in config["categories"]:
            assert category in ALL_CATEGORIES or category in MODE_ONLY_CATEGORIES, (
                mode,
                category,
            )


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
    assert {"search", "read_schema", "search_docs"} <= names
    # ...but not render_widget: it is Imagine-only, so "visualization" is not in
    # ALL_CATEGORIES and general chat must not offer it.
    assert "render_widget" not in names
    assert registry.get("render_widget") is not None
    assert {"list_users", "create_api_key", "create_workspace"} <= names
    assert {"explore_trace_legacy", "read_trace_span", "analyze_error_cluster"} <= names
    # Nothing left from the retired proxy tools
    assert not {"add_columns", "get_cost_breakdown", "list_alert_monitors"} & names


def test_imagine_mode_is_where_render_widget_is_offered():
    """The other half of keeping render_widget out of general chat: Imagine is
    the one mode that still offers it."""
    assert detect_mode("imagine", "build me a widget") == "imagine"
    assert "render_widget" in _names(load_tools_for_mode("imagine"))


def test_tracing_mode_includes_error_feed_and_dashboards():
    names = _names(load_tools_for_mode("tracing"))
    assert {"get_trace", "list_error_clusters", "query_dashboard_metrics"} <= names
    assert "create_scenario" not in names


def test_gateway_pages_route_to_gateway_mode():
    assert detect_mode("gateway", "show me the request logs") == "gateway"
    assert {"list_gateways", "get_gateway_analytics"} <= _names(
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


def test_simulation_can_actually_be_executed():
    """`run_simulation` lives in the `agents` catalog group, not `simulation`.

    While `agents` was missing from both ALL_CATEGORIES and the agents mode's own
    category list, Falcon could create an agent, version it, write scenarios and
    save a test — and then had no tool to run any of it, in any mode.
    """
    for mode in ("general", "agents"):
        names = _names(load_tools_for_mode(mode))
        assert "run_simulation" in names, mode
        # reading a run back matters as much as starting one
        assert {"get_test_execution", "list_test_executions"} <= names, mode
        # and scenarios must be discoverable to pick one for a test
        assert "list_scenarios" in names, mode


def test_added_catalog_tools_are_reachable_in_general_mode():
    """Tools added to close incomplete flows — annotate, correct, clean up, filter."""
    names = _names(load_tools_for_mode("general"))
    assert {
        # annotating was impossible: no tool returned label settings, and
        # submit alone never completed an item
        "get_annotation_item_detail",
        "complete_annotation_item",
        # datasets were a one-way ratchet: no cell edit, no delete
        "update_dataset_cell",
        "delete_datasets",
        # trace filters need valid column ids from somewhere
        "list_trace_properties",
        # alerts had no coverage at all
        "list_fired_alerts",
        # dashboards could be created but never removed
        "delete_dashboard",
    } <= names


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
    assert {"list_dashboard_metrics", "list_dashboards", "list_datasets"} <= names


def test_primary_entity_id_prefers_top_level_json_id():
    nested = '{"workspace": {"id": "11111111-1111-1111-1111-111111111111"}, "id": "22222222-2222-2222-2222-222222222222"}'
    assert _extract_primary_entity_id(nested) == "22222222-2222-2222-2222-222222222222"
    assert (
        _extract_primary_entity_id("Created `33333333-3333-3333-3333-333333333333`")
        == "33333333-3333-3333-3333-333333333333"
    )
    assert _extract_primary_entity_id('{"id": 12, "name": "x"}') is None


@pytest.mark.parametrize("cloud_available", [False, True])
def test_usage_tools_follow_cloud_availability(monkeypatch, cloud_available):
    original_has_ee = has_ee
    monkeypatch.setattr(
        "tfc.ee_loader.has_ee",
        lambda module: (
            cloud_available if module == "ee.cloud" else original_has_ee(module)
        ),
    )
    target = ToolRegistry()
    for tool in registry.list_all():
        if not isinstance(tool, GeneratedAPITool):
            target.register(tool)
    register_generated_tools(target=target)
    register_generated_tools(target=target)
    monkeypatch.setattr("ee.falcon_ai.modes.tool_registry", target)
    for mode in ("general", "gateway"):
        names = _names(load_tools_for_mode(mode))
        assert ("get_usage_overview" in names) is cloud_available
        assert "list_dashboard_metrics" in names
    from ee.falcon_ai.builtin_skills_loader import load_builtin_skills

    skill = next(s for s in load_builtin_skills() if s["slug"] == "analyze-costs")
    assert all(target.get(name) for name in skill["tool_names"])
    assert "get_usage_overview" not in skill["instructions"]
