"""Tests for the DRF ViewSet → MCP bridge.

Validates:
  1. @expose_to_mcp decorator registers tools with correct schemas.
  2. Serializer introspection produces correct Pydantic models.
  3. DRFBridgeTool.execute() constructs DRF requests and calls ViewSets.
  4. Response unwrapping handles success/error envelopes.
  5. Bridge tools are discoverable in the global registry.
"""

from unittest.mock import MagicMock, patch

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from rest_framework import serializers as drf_serializers

from ai_tools.drf_bridge import (
    DRFBridgeTool,
    ViewSetBinding,
    _build_drf_request,
    _format_result_for_llm,
    _serializer_to_pydantic,
    _unwrap_response,
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
