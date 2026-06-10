"""TH-4667 filter-integrity gate.

The bridge used to inject `search`/`page`/`page_size` into EVERY
GET-collection tool as "universally safe" filters. DRF views without
SearchFilter (or a custom handler) silently IGNORE unknown query params, so
the model believed it had name-filtered when it had not — proven live:
``list_datasets(search="definitely-not-real-dataset-999")`` returned
"Showing 10 of 455", and the model then made absence/presence claims off
unfiltered data.

The fix (ai_tools/drf_bridge.py::_detect_collection_params) only advertises a
universal list param when the view DETECTABLY honors it — SearchFilter +
search_fields, a validated query serializer, a DRF paginator, or an explicit
``list_params`` / ``mcp_list_params`` declaration for documented custom
handlers — and execute() remaps advertised names onto the honored query-param
names (e.g. search -> 'name' on DatasetView, page_size -> 'limit' for
PageNumberPagination subclasses).

This module is the guard that the lie cannot recur:
  (a) STRUCTURAL — on every auto-built GET-collection tool, the advertised
      universal params are exactly the binding's collection_param_map keys;
  (b) LOUD-FAILURE — auto-built input models forbid extras, so a
      non-advertised param is a VALIDATION_ERROR, never a silent no-op;
  (c) PINNED — golden expectations for the empirically verified tools,
      including the TH-4667 list_datasets case specifically.
"""

import ai_tools.tools  # noqa: F401  — triggers bridge registration
from ai_tools.drf_bridge import UNIVERSAL_LIST_PARAMS, _resolve_class
from ai_tools.registry import registry

UNIVERSAL = set(UNIVERSAL_LIST_PARAMS)


def _universal_collection_tools():
    """Bridged GET-collection tools whose input model was auto-built."""
    out = []
    for tool in registry.list_all():
        binding = getattr(tool, "binding", None)
        if not binding:
            continue
        if binding.method.upper() != "GET" or binding.detail:
            continue
        if binding.query_params:
            # explicit query_params declarations are a hand-curated contract,
            # not the auto-injected universal params
            continue
        if binding.collection_param_map is None:
            # non-list GET shapes (serializer-derived input etc.)
            continue
        out.append(tool)
    return out


class TestUniversalParamHonesty:
    def test_collection_tools_exist(self):
        tools = _universal_collection_tools()
        assert len(tools) >= 40, (
            f"expected the auto-built GET-collection population (~56), got "
            f"{len(tools)} — did the universal branch stop setting "
            f"collection_param_map?"
        )

    def test_advertised_params_match_map_exactly(self):
        """(a) No auto-built list tool may advertise a universal param that
        is not mapped to a verified, honored view input — and vice versa."""
        bad = []
        for tool in _universal_collection_tools():
            fields = set(getattr(tool.input_model, "model_fields", {}) or {})
            advertised = fields & UNIVERSAL
            mapped = set(tool.binding.collection_param_map)
            if advertised != mapped:
                bad.append(f"{tool.name}: advertised={advertised} mapped={mapped}")
            for actual in tool.binding.collection_param_map.values():
                if not isinstance(actual, str) or not actual:
                    bad.append(f"{tool.name}: non-string map value {actual!r}")
        assert not bad, "advertised/honored mismatch:\n" + "\n".join(bad)

    def test_dropped_params_fail_loud_not_silent(self):
        """(b) extra='forbid': sending a dropped param must raise at
        validation, never silently pass through to an ignoring view."""
        import pydantic
        import pytest

        checked = 0
        for tool in _universal_collection_tools():
            fields = set(getattr(tool.input_model, "model_fields", {}) or {})
            if "search" in fields:
                continue
            with pytest.raises(pydantic.ValidationError):
                tool.input_model.model_validate({"search": "x"})
            checked += 1
        assert checked >= 30, f"expected >=30 dropped-search tools, got {checked}"


class TestPinnedExpectations:
    """Golden outcomes from the 2026-06-10 live audit (ws1).

    If one of these fails after a deliberate view/bridge change, re-verify
    the view actually honors the param (impossible-token probe) and update
    the pin in the same PR — that review moment is the point of the gate.
    """

    # tool -> exact expected collection_param_map
    PINS = {
        # THE TH-4667 case: DatasetView honors `name` (get_queryset
        # name__icontains), advertised as `search` via mcp_list_params.
        "list_datasets": {"search": "name", "page": "page", "page_size": "limit"},
        # remapped documented custom handlers
        "list_eval_tasks": {"search": "name", "page": "page", "page_size": "limit"},
        "list_project_versions": {
            "search": "search_name",
            "page": "page",
            "page_size": "limit",
        },
        # `page` deliberately absent: the view's page_number is 0-indexed.
        "list_alert_monitors": {"search": "search_text", "page_size": "page_size"},
        # custom handlers that honor `search` natively (declared)
        "list_agentcc_request_logs": {
            "search": "search",
            "page": "page",
            "page_size": "limit",
        },
        "list_agentcc_sessions": {
            "search": "search",
            "page": "page",
            "page_size": "limit",
        },
        "list_annotation_labels": {
            "search": "search",
            "page": "page",
            "page_size": "limit",
        },
        # auto-detected from @validated_request query serializers
        "list_users": {"search": "search", "page": "page", "page_size": "limit"},
        "list_workspaces": {"search": "search", "page": "page", "page_size": "limit"},
        # strict serializer has search + page_size only — page must be absent
        "list_annotation_queues": {"search": "search", "page_size": "page_size"},
        # strict serializer has NONE of the universal params — pre-fix the
        # advertised `search` was rejected at runtime ("search: Unknown field")
        "list_queue_items": {},
        # auto-detected native SearchFilter + search_fields
        "list_knowledge_bases": {
            "search": "search",
            "page": "page",
            "page_size": "limit",
        },
        # mixin-paginated views with NO search support: page/page_size only
        # (page_size remaps to the paginator's `limit`)
        "list_traces": {"page": "page", "page_size": "limit"},
        "list_sessions": {"page": "page", "page_size": "limit"},
        "list_spans": {"page": "page", "page_size": "limit"},
        "list_annotations": {"page": "page", "page_size": "limit"},
        "list_custom_eval_configs": {"page": "page", "page_size": "limit"},
        # custom non-paginating handlers: nothing is advertised
        "list_dashboards": {},
        "list_saved_views": {},
        "list_shared_links": {},
        "list_prompt_folders": {},
        "get_trace_properties": {},
        "get_observation_span_fields": {},
        "list_active_tests": {},
        "list_system_personas": {},
        "list_workspace_personas": {},
    }

    def test_pinned_param_maps(self):
        bad = []
        for name, expected in self.PINS.items():
            tool = registry.get(name)
            if tool is None:
                bad.append(f"{name}: tool missing from registry")
                continue
            actual = tool.binding.collection_param_map
            if actual != expected:
                bad.append(f"{name}: map={actual} expected={expected}")
        assert not bad, "pinned expectations drifted:\n" + "\n".join(bad)

    def test_th4667_list_datasets_search_is_real(self):
        """The original lie, pinned end-to-end at the schema level: the tool
        advertises search, the view declares the honored mapping, and the
        view's get_queryset actually filters by that param."""
        import inspect

        tool = registry.get("list_datasets")
        fields = set(tool.input_model.model_fields)
        assert "search" in fields
        assert tool.binding.collection_param_map["search"] == "name"

        view_cls = _resolve_class(tool.binding.viewset_class)
        assert getattr(view_cls, "mcp_list_params", None) == {"search": "name"}
        src = inspect.getsource(view_cls.get_queryset)
        assert 'query_params.get("name")' in src, (
            "DatasetView.get_queryset no longer reads the `name` query param "
            "— the list_datasets search remap is broken; update "
            "mcp_list_params (and this pin) to a param the view honors."
        )

    def test_declared_query_params_tools_untouched(self):
        """Hand-curated query_params tools keep their explicit schemas and
        never carry a collection_param_map (A9: no schema drift there)."""
        for name in ("list_trace_projects", "list_run_tests", "list_scenarios"):
            tool = registry.get(name)
            assert tool is not None, name
            assert tool.binding.collection_param_map is None, name
            assert tool.binding.query_params, name

    def test_declared_page_size_remaps_to_limit(self):
        """Same lie class in the hand-curated branch: these four tools
        declared `page_size` but their ExtendedPageNumberPagination paginator
        reads `limit` — page_size=2 returned 10 rows (probed 2026-06-10).
        The "actual" remap pins the fix; execute() renames on the wire."""
        for name in (
            "list_prompt_templates",
            "list_prompt_versions",
            "list_experiments",
            "list_personas",
        ):
            tool = registry.get(name)
            assert tool is not None, name
            entry = (tool.binding.query_params or {}).get("page_size")
            assert isinstance(entry, dict) and entry.get("actual") == "limit", (
                f"{name}: declared page_size must remap to the paginator's "
                f"`limit` query param (got {entry!r})"
            )
