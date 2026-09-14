"""The OpenAPI-generated MCP catalog exposed through ai_tools.registry."""

import json
import uuid

import pytest

from ai_tools.generated import (
    GeneratedAPITool,
    category_for_group,
    register_generated_tools,
    render_result,
)
from ai_tools.registry import ToolRegistry, registry
from mcp_server.api_executor import APIExecutionError
from mcp_server.generated_registry import registry as generated_registry

pytestmark = pytest.mark.django_db


class TestRegistration:
    def test_every_catalog_tool_is_registered_as_a_generated_tool(self):
        for generated in generated_registry.list_all():
            tool = registry.get(generated.name)
            assert isinstance(tool, GeneratedAPITool), generated.name
            assert tool.generated is generated

    def test_native_and_catalog_names_do_not_collide(self):
        native = [t for t in registry.list_all() if not isinstance(t, GeneratedAPITool)]
        catalog_names = {t.name for t in generated_registry.list_all()}
        assert not catalog_names & {t.name for t in native}
        assert registry.count() == len(native) + generated_registry.count()

    def test_registration_is_idempotent(self):
        fresh = ToolRegistry()
        register_generated_tools(target=fresh)
        register_generated_tools(target=fresh)
        assert fresh.count() == generated_registry.count()

    def test_catalog_groups_map_onto_falcon_categories(self):
        assert category_for_group("observability") == "tracing"
        assert category_for_group("dashboards") == "dashboards"
        assert registry.get("search_traces").category == "tracing"
        assert registry.get("list_dashboards").category == "dashboards"
        assert "observability" not in registry.categories()


class TestSchema:
    def test_input_schema_is_the_catalog_schema(self):
        tool = registry.get("get_dataset")
        assert tool.input_schema is tool.generated.input_schema
        assert "dataset_id" in tool.input_schema["properties"]

    def test_to_dict_carries_annotations(self):
        payload = registry.get("create_dataset").to_dict()
        assert payload["category"] == "datasets"
        assert payload["annotations"]["readOnlyHint"] is False
        assert payload["input_schema"]["properties"]

    def test_stringified_json_is_parsed_only_for_structured_fields(self):
        tool = registry.get("add_dataset_rows")
        props = tool.input_schema["properties"]
        structured = next(
            name for name, spec in props.items() if spec.get("type") == "array"
        )
        cleaned = tool._coerce_params(
            {structured: '[{"a": 1}]', "dataset_id": "[not json, keep me]"}
        )
        assert cleaned[structured] == [{"a": 1}]
        assert cleaned["dataset_id"] == "[not json, keep me]"


class TestExecution:
    def test_read_tool_returns_api_json(self, tool_context):
        result = registry.get("whoami").run({}, tool_context)

        assert not result.is_error, result.content
        assert isinstance(result.data, dict)
        assert json.loads(result.content) == result.data
        assert tool_context.user.email in result.content

    def test_list_tool_scopes_to_the_caller_workspace(self, tool_context):
        result = registry.get("list_datasets").run({}, tool_context)

        assert not result.is_error, result.content
        assert isinstance(result.data, dict)

    def test_missing_resource_is_reported_as_an_error(self, tool_context):
        result = registry.get("get_dataset").run(
            {"dataset_id": str(uuid.uuid4())}, tool_context
        )

        assert result.is_error
        assert "does not exist" in result.content

    def test_api_status_codes_map_to_tool_error_codes(self, tool_context):
        class FailingExecutor:
            def __init__(self, status_code):
                self.status_code = status_code

            def execute_sync(self, tool, arguments, context):
                raise APIExecutionError(
                    "boom", status_code=self.status_code, data={"detail": "boom"}
                )

        generated = generated_registry.get("get_dataset")
        expectations = {
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            400: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
        }
        for status_code, code in expectations.items():
            tool = GeneratedAPITool(generated, FailingExecutor(status_code))
            result = tool.run({"dataset_id": str(uuid.uuid4())}, tool_context)
            assert result.is_error
            assert result.error_code == code, status_code
            assert result.data == {"detail": "boom"}

    def test_schema_violation_maps_to_validation_error(self, tool_context):
        result = registry.get("get_dataset").run({}, tool_context)

        assert result.is_error
        assert result.error_code == "VALIDATION_ERROR"
        assert "dataset_id" in result.content

    def test_execute_accepts_plain_dicts(self, tool_context):
        result = registry.get("list_workspaces").execute({}, tool_context)

        assert not result.is_error, result.content


def test_render_result_is_compact_json():
    assert render_result({"id": "x", "n": 1}) == '{"id": "x", "n": 1}'
    assert render_result(None) == "Done."
