"""Tests for the DRF ViewSet → MCP bridge.

Validates:
  1. @expose_to_mcp decorator registers tools with correct schemas.
  2. Serializer introspection produces correct Pydantic models.
  3. DRFBridgeTool.execute() constructs DRF requests and calls ViewSets.
  4. Response unwrapping handles success/error envelopes.
  5. Bridge tools are discoverable in the global registry.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action

from ai_tools.drf_bridge import (
    DRFBridgeTool,
    ViewSetBinding,
    _build_drf_request,
    _format_result_for_llm,
    _serializer_to_pydantic,
    _strict_registration,
    _unwrap_response,
    expose_to_mcp,
    viewset_tool,
)
from ai_tools.registry import ToolRegistry, registry

# --- Serializer introspection tests ---


class SampleSerializer(drf_serializers.Serializer):
    name = drf_serializers.CharField(required=True, help_text="Name of the item")
    description = drf_serializers.CharField(
        required=False, help_text="Optional description"
    )
    count = drf_serializers.IntegerField(
        required=False, help_text="Item count", min_value=0
    )
    is_active = drf_serializers.BooleanField(required=False, help_text="Active flag")
    score = drf_serializers.FloatField(
        required=False, min_value=0.0, max_value=1.0, help_text="Score"
    )
    organization = drf_serializers.CharField(required=False)
    created_at = drf_serializers.DateTimeField(required=False)


class TestSerializerToPydantic:
    def test_basic_conversion(self):
        model = _serializer_to_pydantic(SampleSerializer)
        assert model is not None
        schema = model.model_json_schema()
        props = schema.get("properties", {})
        assert "name" in props
        assert "description" in props
        assert "count" in props
        assert "is_active" in props
        assert "score" in props

    def test_skips_read_only_and_internal_fields(self):
        model = _serializer_to_pydantic(SampleSerializer)
        schema = model.model_json_schema()
        props = schema.get("properties", {})
        assert "organization" not in props
        assert "created_at" not in props

    def test_required_fields(self):
        model = _serializer_to_pydantic(SampleSerializer)
        schema = model.model_json_schema()
        required = schema.get("required", [])
        assert "name" in required

    def test_help_text_becomes_description(self):
        model = _serializer_to_pydantic(SampleSerializer)
        schema = model.model_json_schema()
        props = schema["properties"]
        assert "Name of the item" in props["name"].get("description", "")
        assert "Optional description" in props["description"].get("description", "")

    def test_include_fields_filter(self):
        model = _serializer_to_pydantic(
            SampleSerializer, include_fields=["name", "count"]
        )
        schema = model.model_json_schema()
        props = schema.get("properties", {})
        assert "name" in props
        assert "count" in props
        assert "description" not in props
        assert "is_active" not in props

    def test_exclude_fields_filter(self):
        model = _serializer_to_pydantic(SampleSerializer, exclude_fields={"name"})
        schema = model.model_json_schema()
        props = schema.get("properties", {})
        assert "name" not in props
        assert "description" in props

    def test_min_max_constraints(self):
        model = _serializer_to_pydantic(SampleSerializer)
        schema = model.model_json_schema()
        count_spec = schema["properties"]["count"]
        assert count_spec.get("minimum") == 0 or "ge" in str(count_spec)

    def test_empty_serializer_returns_empty_input(self):
        class EmptySerializer(drf_serializers.Serializer):
            organization = drf_serializers.CharField()

        model = _serializer_to_pydantic(EmptySerializer)
        from ai_tools.base import EmptyInput

        assert model is EmptyInput


class TestChoiceFieldConversion:
    def test_choice_field_becomes_enum_in_schema(self):
        class ChoiceSerializer(drf_serializers.Serializer):
            status = drf_serializers.ChoiceField(
                choices=["active", "inactive", "archived"],
                help_text="Current status",
            )

        model = _serializer_to_pydantic(ChoiceSerializer)
        schema = model.model_json_schema()
        status_spec = schema["properties"]["status"]
        # Pydantic Literal generates an enum in JSON Schema
        enum_values = status_spec.get("enum") or status_spec.get("anyOf")
        assert enum_values is not None
        flat = str(status_spec)
        assert "active" in flat
        assert "inactive" in flat
        assert "archived" in flat


# --- Response unwrapping tests ---


class TestResponseUnwrapping:
    def test_success_envelope(self):
        response = MagicMock()
        response.status_code = 200
        response.data = {"status": True, "result": {"name": "test-project"}}
        data, is_error = _unwrap_response(response)
        assert data == {"name": "test-project"}
        assert is_error is False

    def test_error_envelope(self):
        response = MagicMock()
        response.status_code = 400
        response.data = {"status": False, "result": {"error": "Project not found"}}
        data, is_error = _unwrap_response(response)
        assert data == "Project not found"
        assert is_error is True

    def test_raw_list_response(self):
        response = MagicMock()
        response.status_code = 200
        response.data = [{"id": "1"}, {"id": "2"}]
        data, is_error = _unwrap_response(response)
        assert data == [{"id": "1"}, {"id": "2"}]
        assert is_error is False

    def test_http_500_is_error(self):
        response = MagicMock()
        response.status_code = 500
        response.data = {"detail": "Internal error"}
        data, is_error = _unwrap_response(response)
        assert is_error is True


# --- LLM formatting tests ---


class TestFormatResultForLLM:
    def test_format_string(self):
        assert _format_result_for_llm("hello", "list") == "hello"

    def test_format_dict_with_list_key(self):
        data = {
            "projects": [
                {
                    "name": "proj1",
                    "id": "abc",
                    "trace_type": "observe",
                    "created_at": "2026-01-01",
                },
            ],
            "total_count": 1,
        }
        result = _format_result_for_llm(data, "list")
        assert "proj1" in result
        assert "1" in result

    def test_format_dict_key_value(self):
        data = {"project_id": "abc-123", "name": "My Project"}
        result = _format_result_for_llm(data, "create")
        assert "abc-123" in result
        assert "My Project" in result

    def test_format_empty_list(self):
        result = _format_result_for_llm([], "list")
        assert "No results" in result

    def test_format_list_of_dicts(self):
        data = [{"name": "a", "id": "1"}, {"name": "b", "id": "2"}]
        result = _format_result_for_llm(data, "list")
        assert "a" in result
        assert "b" in result


# --- DRF request building tests ---


class TestBuildDRFRequest:
    def test_get_request(self, tool_context):
        request = _build_drf_request("GET", {}, {"page": "1"}, tool_context)
        assert request.user == tool_context.user
        assert request.workspace == tool_context.workspace
        assert request.organization == tool_context.organization

    def test_post_request_carries_data(self, tool_context):
        request = _build_drf_request("POST", {"name": "test"}, {}, tool_context)
        assert request.user == tool_context.user
        assert request.data.get("name") == "test"

    def test_patch_request(self, tool_context):
        request = _build_drf_request("PATCH", {"name": "updated"}, {}, tool_context)
        assert request.data.get("name") == "updated"


# --- Tool execution tests ---


class TestDRFBridgeToolExecution:
    def _make_tool(self, action="list", method="GET", detail=False, pk_field=None):
        class TestInput(PydanticBaseModel):
            page: int = Field(default=0)

        if detail:

            class TestInput(PydanticBaseModel):
                id: str = Field(description="ID")

        return type(
            "TestBridge",
            (DRFBridgeTool,),
            {
                "name": f"test_{action}",
                "description": f"Test {action}",
                "category": "test",
                "input_model": TestInput,
                "binding": ViewSetBinding(
                    viewset_class="test.ViewSet",
                    action=action,
                    method=method,
                    detail=detail,
                    pk_field=pk_field or ("id" if detail else None),
                ),
            },
        )()

    def test_list_action_calls_viewset(self, tool_context):
        tool = self._make_tool("list", "GET")
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.list.return_value = MagicMock(
            status_code=200,
            data={"status": True, "result": {"projects": [], "total_count": 0}},
        )

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(page=0), tool_context)

        assert not result.is_error
        mock_view.list.assert_called_once()

    def test_result_scope_label_is_prepended(self, tool_context):
        """F1: a binding's result_scope hook prepends a scope label to content."""

        def scope(params, data):
            total = (data or {}).get("total_count")
            return f"Scope: {total} observe projects in the current workspace."

        tool = self._make_tool("list", "GET")
        tool.binding.result_scope = scope
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.list.return_value = MagicMock(
            status_code=200,
            data={
                "status": True,
                "result": {
                    "projects": [{"name": "p1", "id": "a", "trace_type": "observe"}],
                    "total_count": 47,
                },
            },
        )

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(page=0), tool_context)

        assert not result.is_error
        assert "Scope: 47 observe projects in the current workspace." in result.content

    def test_result_scope_failure_does_not_break_execute(self, tool_context):
        """A throwing result_scope hook is swallowed — the result still returns."""

        def boom(params, data):
            raise ValueError("scope hook bug")

        tool = self._make_tool("list", "GET")
        tool.binding.result_scope = boom
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.list.return_value = MagicMock(
            status_code=200,
            data={"status": True, "result": {"projects": [], "total_count": 0}},
        )

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(page=0), tool_context)

        assert not result.is_error

    def test_detail_action_sets_pk(self, tool_context):
        tool = self._make_tool("retrieve", "GET", detail=True, pk_field="id")
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.retrieve.return_value = MagicMock(
            status_code=200,
            data={"status": True, "result": {"id": "xyz", "name": "Project"}},
        )

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(id="xyz-123"), tool_context)

        assert not result.is_error
        # Bridge sets the id under "pk" plus the viewset's lookup_field/
        # lookup_url_kwarg names; "pk" is the guaranteed invariant.
        assert mock_view.kwargs["pk"] == "xyz-123"

    def test_error_response_handled(self, tool_context):
        tool = self._make_tool("list", "GET")
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.list.return_value = MagicMock(
            status_code=400,
            data={"status": False, "result": {"error": "Bad query"}},
        )

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(page=0), tool_context)

        assert result.is_error
        assert "Bad query" in result.content

    def test_viewset_exception_caught(self, tool_context):
        tool = self._make_tool("list", "GET")
        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.list.side_effect = RuntimeError("DB is down")

        with patch("ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls):
            result = tool.execute(tool.input_model(page=0), tool_context)

        assert result.is_error
        assert "DB is down" in result.content


# --- @expose_to_mcp decorator registration tests ---


class TestExposeToMCPDecorator:
    def test_registers_tools_from_decorator(self):
        """The @expose_to_mcp on ProjectView should register tools in the global registry."""
        tool = registry.get("list_trace_projects")
        assert tool is not None
        assert tool.category == "tracing"
        assert tool.binding.action == "list"
        assert tool.binding.method == "GET"

    def test_list_tool_has_query_params_schema(self):
        tool = registry.get("list_trace_projects")
        schema = tool.input_schema
        props = schema.get("properties", {})
        assert "name" in props
        assert "project_type" in props
        assert "page_number" in props
        assert "page_size" in props

    def test_retrieve_tool_has_id_field(self):
        tool = registry.get("get_trace_project")
        assert tool.binding.detail is True
        schema = tool.input_schema
        props = schema.get("properties", {})
        assert "id" in props

    def test_create_tool_has_serializer_fields(self):
        tool = registry.get("create_trace_project")
        assert tool.binding.method == "POST"
        schema = tool.input_schema
        props = schema.get("properties", {})
        assert "name" in props
        assert "trace_type" in props
        assert "model_type" in props
        # Internal fields should be excluded
        assert "organization" not in props
        assert "workspace" not in props

    def test_create_tool_descriptions_from_help_text(self):
        tool = registry.get("create_trace_project")
        schema = tool.input_schema
        props = schema["properties"]
        assert "Human-readable project name" in props["name"].get("description", "")
        assert "experiment" in props["trace_type"].get("description", "").lower()

    def test_update_name_tool_uses_custom_serializer(self):
        tool = registry.get("rename_trace_project")
        assert tool.binding.method == "POST"
        schema = tool.input_schema
        props = schema.get("properties", {})
        assert "project_id" in props
        assert "name" in props
        assert "sampling_rate" in props
        # Check help_text flows through
        assert "UUID" in props["project_id"].get("description", "")

    def test_all_bridge_tools_have_descriptions(self):
        for name in [
            "list_trace_projects",
            "get_trace_project",
            "create_trace_project",
            "rename_trace_project",
        ]:
            tool = registry.get(name)
            assert tool is not None, f"{name} not found"
            assert len(tool.description) > 10, f"{name} has no description"

    def test_bridge_tools_use_legacy_names(self):
        """Bridge tools use the same names as the hand-written tools they replaced."""
        for name in [
            "list_trace_projects",
            "get_trace_project",
            "create_trace_project",
            "rename_trace_project",
        ]:
            tool = registry.get(name)
            assert tool is not None
            assert hasattr(tool, "binding"), (
                f"{name} should be a bridge tool (have .binding attr)"
            )

    def test_no_old_bridge_prefix_tools(self):
        """Old bridge_ prefix tools should be gone."""
        old = [t for t in registry.list_all() if t.name.startswith("bridge_")]
        assert len(old) == 0, f"Found old bridge tools: {[t.name for t in old]}"


# --- Phase 2A @action bridge machinery tests (A1-A8) ---


class _NoteSerializer(drf_serializers.Serializer):
    """Create-shaped model serializer used as serializer_class bait."""

    name = drf_serializers.CharField(required=True, help_text="Name")
    body = drf_serializers.CharField(required=False, help_text="Body")


class _CustomActionViewSet:
    """Plain class with real DRF @action metadata for derivation tests."""

    serializer_class = _NoteSerializer

    @action(detail=True, methods=["post"])
    def do_submit(self, request, pk=None):
        """Submit the item for processing.

        Longer detail paragraph that must NOT end up in the description.
        """

    @action(detail=True, methods=["get"])
    def get_variables(self, request, pk=None):
        """Return the variables of one item."""

    @action(detail=False, methods=["get"])
    def next_item(self, request, queue_id=None):
        """Get the next pending item in the queue."""


class TestActionMetadataDerivation:
    """A1: method/detail derived from DRF @action; config overrides win."""

    def _register(self, tools):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(category="test", tools=tools)(_CustomActionViewSet)
        return fresh

    def test_derives_method_and_detail_from_drf_action(self):
        fresh = self._register(
            {"do_submit": {"name": "submit_item_x", "query_params": {"note": "Note."}}}
        )
        tool = fresh.get("submit_item_x")
        assert tool is not None
        assert tool.binding.method == "POST"  # derived from methods=["post"]
        assert tool.binding.detail is True  # derived from detail=True

    def test_config_overrides_derived_metadata(self):
        fresh = self._register(
            {
                "do_submit": {
                    "name": "submit_item_x",
                    "method": "GET",
                    "detail": False,
                    "query_params": {"note": "Note."},
                }
            }
        )
        tool = fresh.get("submit_item_x")
        assert tool.binding.method == "GET"
        assert tool.binding.detail is False

    def test_crud_actions_keep_static_map_defaults(self):
        class PlainViewSet:
            serializer_class = _NoteSerializer

            def list(self, request):
                pass

        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(category="test", tools=["list"])(PlainViewSet)
        tool = fresh.list_all()[0]
        assert tool.binding.method == "GET"
        assert tool.binding.detail is False


class TestRegistrationValidation:
    """A2: the three registration-time ValueError rules (raise under pytest)."""

    def test_unknown_tools_key_raises(self):
        with pytest.raises(ValueError, match="probably a typo"):
            with patch("ai_tools.registry.registry", ToolRegistry()):
                expose_to_mcp(
                    category="test",
                    tools={"definitely_not_an_action": {"name": "get_thing_x"}},
                )(_CustomActionViewSet)

    def test_custom_action_without_name_raises(self):
        with pytest.raises(ValueError, match="explicit 'name'"):
            with patch("ai_tools.registry.registry", ToolRegistry()):
                expose_to_mcp(category="test", tools={"get_variables": {}})(
                    _CustomActionViewSet
                )

    def test_write_action_without_schema_raises(self):
        # do_submit has no @validated_request/yasg serializer and no
        # query_params; serializer_class must NOT count (A3) — so it raises.
        with pytest.raises(ValueError, match="no resolvable serializer"):
            with patch("ai_tools.registry.registry", ToolRegistry()):
                expose_to_mcp(
                    category="test", tools={"do_submit": {"name": "submit_item_x"}}
                )(_CustomActionViewSet)

    def test_http_verb_keys_still_allowed_without_name(self):
        class VerbAPIView:
            def get(self, request):
                """List things via APIView verb handler."""

        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(category="test", tools=["get"])(VerbAPIView)
        assert fresh.count() == 1


class TestSerializerFallbackKilled:
    """A3: serializer_class never leaks into custom action schemas."""

    def test_custom_write_with_query_params_excludes_class_serializer(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "do_submit": {
                        "name": "submit_item_x",
                        "query_params": {"note": "Optional note."},
                    }
                },
            )(_CustomActionViewSet)
        props = fresh.get("submit_item_x").input_schema.get("properties", {})
        # serializer_class fields must NOT leak in
        assert "name" not in props
        assert "body" not in props
        assert "note" in props
        assert "id" in props  # detail pk injection

    def test_custom_detail_get_defaults_to_pk_only_input(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "get_variables": {
                        "name": "get_item_variables_x",
                        "pk_field": "item_id",
                    }
                },
            )(_CustomActionViewSet)
        tool = fresh.get("get_item_variables_x")
        assert tool.binding.method == "GET"
        assert tool.binding.detail is True
        schema = tool.input_schema
        assert set(schema.get("properties", {})) == {"item_id"}
        assert schema.get("required", []) == ["item_id"]


class TestDescriptionDerivation:
    """A4: custom action docstring first paragraph; config overrides."""

    def test_action_docstring_first_paragraph_used(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "do_submit": {
                        "name": "submit_item_x",
                        "query_params": {"note": "Note."},
                    }
                },
            )(_CustomActionViewSet)
        desc = fresh.get("submit_item_x").description
        assert desc == "Submit the item for processing."
        assert "must NOT end up" not in desc

    def test_config_description_overrides_docstring(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "do_submit": {
                        "name": "submit_item_x",
                        "description": "Explicit description wins.",
                        "query_params": {"note": "Note."},
                    }
                },
            )(_CustomActionViewSet)
        assert fresh.get("submit_item_x").description == "Explicit description wins."


class TestPathKwargs:
    """A5: extra URL path kwargs — schema injection + execute() routing."""

    def test_path_kwargs_become_required_fields(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "next_item": {
                        "name": "get_next_item_x",
                        "path_kwargs": {
                            "queue_id": {
                                "description": "Queue UUID.",
                                "id_source": "list_annotation_queues",
                            }
                        },
                    }
                },
            )(_CustomActionViewSet)
        tool = fresh.get("get_next_item_x")
        schema = tool.input_schema
        assert "queue_id" in schema.get("properties", {})
        assert "queue_id" in schema.get("required", [])
        assert "list_annotation_queues" in schema["properties"]["queue_id"].get(
            "description", ""
        )
        assert tool.binding.path_kwargs == {
            "queue_id": {
                "description": "Queue UUID.",
                "id_source": "list_annotation_queues",
            }
        }

    def test_path_kwargs_list_form_normalized(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "next_item": {
                        "name": "get_next_item_x",
                        "path_kwargs": ["queue_id"],
                    }
                },
            )(_CustomActionViewSet)
        tool = fresh.get("get_next_item_x")
        assert tool.binding.path_kwargs == {"queue_id": {}}
        assert "queue_id" in tool.input_schema.get("required", [])

    def test_execute_routes_path_kwargs_alongside_pk(self, tool_context):
        class DualInput(PydanticBaseModel):
            item_id: str = Field(description="Item id")
            queue_id: str = Field(description="Queue id")
            status: str = Field(default="done", description="Status")

        tool = type(
            "DualKwargBridge",
            (DRFBridgeTool,),
            {
                "name": "complete_item_x",
                "description": "Complete item",
                "category": "test",
                "input_model": DualInput,
                "binding": ViewSetBinding(
                    viewset_class="test.ViewSet",
                    action="complete_item",
                    method="POST",
                    detail=True,
                    pk_field="item_id",
                    path_kwargs={"queue_id": {}},
                ),
            },
        )()

        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.complete_item.return_value = MagicMock(
            status_code=200, data={"status": True, "result": {"ok": 1}}
        )

        with patch(
            "ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls
        ):
            result = tool.execute(
                tool.input_model(item_id="item-1", queue_id="q-9"), tool_context
            )

        assert not result.is_error
        call_kwargs = mock_view.complete_item.call_args.kwargs
        assert call_kwargs["queue_id"] == "q-9"  # path kwarg routed
        assert call_kwargs["pk"] == "item-1"  # standard pk routing intact
        # path kwarg must NOT leak into the request body
        request = mock_view.complete_item.call_args.args[0]
        assert "queue_id" not in request.data
        assert request.data.get("status") == "done"

    def test_execute_missing_path_kwarg_is_validation_error(self, tool_context):
        class OnlyQueueInput(PydanticBaseModel):
            queue_id: str | None = Field(default=None, description="Queue id")

        tool = type(
            "MissingKwargBridge",
            (DRFBridgeTool,),
            {
                "name": "next_item_x",
                "description": "Next item",
                "category": "test",
                "input_model": OnlyQueueInput,
                "binding": ViewSetBinding(
                    viewset_class="test.ViewSet",
                    action="next_item",
                    method="GET",
                    detail=False,
                    pk_field=None,
                    path_kwargs={"queue_id": {}},
                ),
            },
        )()
        with patch(
            "ai_tools.drf_bridge._resolve_class", return_value=MagicMock()
        ):
            result = tool.execute(tool.input_model(), tool_context)
        assert result.is_error
        assert "queue_id" in result.content


class TestQueryRoutingOnWrites:
    """A6: query_params entries with "in": "query" land on the query string."""

    def test_in_query_param_lands_in_query_params_on_post(self, tool_context):
        class ExportInput(PydanticBaseModel):
            fmt: str = Field(description="Format")
            note: str = Field(description="Note")

        tool = type(
            "QueryRoutedBridge",
            (DRFBridgeTool,),
            {
                "name": "export_thing_x",
                "description": "Export",
                "category": "test",
                "input_model": ExportInput,
                "binding": ViewSetBinding(
                    viewset_class="test.ViewSet",
                    action="do_export",
                    method="POST",
                    detail=False,
                    pk_field=None,
                    query_params={
                        "fmt": {"type": str, "description": "Format", "in": "query"},
                        "note": {"type": str, "description": "Note"},
                    },
                ),
            },
        )()

        mock_viewset_cls = MagicMock()
        mock_view = MagicMock()
        mock_viewset_cls.return_value = mock_view
        mock_view.default_response_headers = {}
        mock_view.do_export.return_value = MagicMock(
            status_code=200, data={"status": True, "result": {"ok": 1}}
        )

        with patch(
            "ai_tools.drf_bridge._resolve_class", return_value=mock_viewset_cls
        ):
            result = tool.execute(
                tool.input_model(fmt="csv", note="hello"), tool_context
            )

        assert not result.is_error
        request = mock_view.do_export.call_args.args[0]
        assert request.query_params.get("fmt") == "csv"  # routed to query
        assert "fmt" not in request.data
        assert request.data.get("note") == "hello"  # default body routing

    def test_default_post_routing_unchanged(self, tool_context):
        request = _build_drf_request("POST", {"a": 1}, {}, tool_context)
        assert request.data.get("a") == 1
        assert dict(request.query_params) == {}

    def test_build_request_extra_query_on_post(self, tool_context):
        request = _build_drf_request(
            "POST", {"a": 1}, {}, tool_context, extra_query={"fmt": "csv"}
        )
        assert request.query_params.get("fmt") == "csv"
        assert request.data.get("a") == 1


class TestIdSourceOnBinding:
    """A7: id_source is stored on the binding for verify_bridges harvesting."""

    def test_id_source_stored(self):
        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="test",
                tools={
                    "get_variables": {
                        "name": "get_item_variables_x",
                        "id_source": "list_items_x",
                    }
                },
            )(_CustomActionViewSet)
        tool = fresh.get("get_item_variables_x")
        assert tool.binding.id_source == "list_items_x"
        # and the id hint flows into the pk field description
        assert "list_items_x" in tool.input_schema["properties"]["id"].get(
            "description", ""
        )


class TestStrictRegistration:
    """A8: raise under pytest/DEBUG/AI_TOOLS_STRICT_BRIDGE=1; swallow in prod."""

    def test_strict_under_pytest(self):
        assert _strict_registration() is True

    def test_env_var_forces_strict_without_pytest(self):
        with patch.dict(os.environ, {"AI_TOOLS_STRICT_BRIDGE": "1"}):
            with patch.dict(sys.modules):
                del sys.modules["pytest"]
                with override_settings(DEBUG=False):
                    assert _strict_registration() is True

    def test_prod_is_not_strict(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_TOOLS_STRICT_BRIDGE"}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict(sys.modules):
                del sys.modules["pytest"]
                with override_settings(DEBUG=False):
                    assert _strict_registration() is False
                with override_settings(DEBUG=True):
                    assert _strict_registration() is True

    def test_prod_swallows_registration_failure(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_TOOLS_STRICT_BRIDGE"}
        with patch.dict(os.environ, env, clear=True):
            with patch.dict(sys.modules):
                del sys.modules["pytest"]
                with override_settings(DEBUG=False):
                    fresh = ToolRegistry()
                    with patch("ai_tools.registry.registry", fresh):
                        # broken config (custom action without name) must NOT
                        # raise in prod — swallow + log, tool just absent.
                        expose_to_mcp(
                            category="test", tools={"get_variables": {}}
                        )(_CustomActionViewSet)
                    assert fresh.count() == 0


class TestRealViewSetRegistration:
    """One real-viewset registration (QueueItemViewSet) to dent mock-blindness."""

    def test_complete_item_registration_schema(self):
        from model_hub.views.annotation_queues import QueueItemViewSet

        fresh = ToolRegistry()
        with patch("ai_tools.registry.registry", fresh):
            expose_to_mcp(
                category="annotation_queues",
                tools={
                    "complete_item": {
                        "name": "complete_queue_item_test",
                        "pk_field": "item_id",
                        "id_source": "list_queue_items",
                        "path_kwargs": {
                            "queue_id": {
                                "description": "UUID of the annotation queue.",
                                "id_source": "list_annotation_queues",
                            }
                        },
                    }
                },
            )(QueueItemViewSet)

        tool = fresh.get("complete_queue_item_test")
        assert tool is not None
        # A1: derived from @action(detail=True, methods=["post"])
        assert tool.binding.method == "POST"
        assert tool.binding.detail is True
        # A7
        assert tool.binding.id_source == "list_queue_items"
        # A5 + pk injection: both URL inputs present and required
        schema = tool.input_schema
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        assert "item_id" in props and "item_id" in required
        assert "queue_id" in props and "queue_id" in required
        # serializer resolved via @validated_request closure (rule 3 passes,
        # and the create-shaped QueueItem serializer did not leak)
        assert "queue" not in props


# --- @viewset_tool decorator tests (backward compat) ---


class TestViewSetToolDecorator:
    def test_registers_with_explicit_pydantic_model(self):
        fresh_reg = ToolRegistry()

        with patch("ai_tools.registry.registry", fresh_reg):

            @viewset_tool(
                name="test_compat_tool",
                description="Test backward compat",
                category="test",
                viewset_class="some.fake.ViewSet",
                action="list",
                method="GET",
            )
            class TestInput(PydanticBaseModel):
                limit: int = Field(default=10)

        tool = fresh_reg.get("test_compat_tool")
        assert tool is not None
        assert isinstance(tool, DRFBridgeTool)
        assert tool.binding.action == "list"


# --- Integration-level tests ---


class TestBridgeToolFullFlow:
    """Test that bridge tools work through BaseTool.run() like hand-written tools."""

    def test_run_validates_input(self, tool_context):
        tool = registry.get("create_trace_project")
        assert tool is not None
        # Missing required fields should fail validation
        result = tool.run({}, tool_context)
        assert result.is_error
        assert "VALIDATION_ERROR" in (result.error_code or "")

    def test_run_with_valid_input_reaches_viewset(self, tool_context):
        tool = registry.get("list_trace_projects")
        assert tool is not None
        # This will try to call the real ProjectView.list
        # which requires a DB — but we can verify it gets past validation
        result = tool.run({"page_number": 0, "page_size": 5}, tool_context)
        # Will likely fail with DB error, but should NOT be a validation error
        if result.is_error:
            assert "VALIDATION_ERROR" not in (result.error_code or "")
