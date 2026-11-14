"""Unit tests for native tool parameter cleaning; no database required."""

from unittest.mock import patch

import pytest
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, ValidationError

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.tools.context.search import SearchTool


class TextInput(PydanticBaseModel):
    text: str = ""
    nullable_text: str | None = None
    text_or_object: str | dict = ""
    object_or_text: dict | str = ""
    text_or_array: str | list = ""
    array_or_text: list | str = ""
    aliased_text: str = Field(default="", alias="query")


class StructuredInput(PydanticBaseModel):
    items: list[str] | None = None
    metadata: dict | None = None


class TextTool(BaseTool):
    name = "test_text"
    description = "Return validated parameters"
    category = "test"
    input_model = TextInput

    def execute(self, params, context):
        return ToolResult(content="ok", data=params.model_dump(by_alias=True))


class StructuredTool(TextTool):
    input_model = StructuredInput


class TestCleanParams:
    @pytest.mark.parametrize(
        "field_name",
        [
            "text",
            "nullable_text",
            "text_or_object",
            "object_or_text",
            "text_or_array",
            "array_or_text",
            "query",
        ],
    )
    @pytest.mark.parametrize(
        "value",
        ['{"status":"FAILED"}', '["FAILED"]', '  {"status": "FAILED"}\n'],
    )
    def test_preserves_literal_json_for_string_fields(self, field_name, value):
        tool = TextTool()
        raw = {field_name: value}

        cleaned = tool._clean_params(raw)

        assert cleaned == raw
        params = tool.input_model.model_validate(cleaned)
        assert params.model_dump(by_alias=True)[field_name] == value

    def test_decodes_stringified_structures(self):
        tool = StructuredTool()
        raw = {"items": '["datasets"]', "metadata": '{"status":"FAILED"}'}

        cleaned = tool._clean_params(raw)

        assert cleaned == {"items": ["datasets"], "metadata": {"status": "FAILED"}}
        assert tool.input_model.model_validate(cleaned).model_dump() == cleaned
        assert raw == {"items": '["datasets"]', "metadata": '{"status":"FAILED"}'}

    @pytest.mark.parametrize(
        "raw",
        [
            {"items": ["datasets"], "metadata": {"status": "FAILED"}},
            {"items": None, "metadata": None},
        ],
    )
    def test_preserves_native_structures_and_null(self, raw):
        tool = StructuredTool()

        cleaned = tool._clean_params(raw)

        assert cleaned == raw
        assert tool.input_model.model_validate(cleaned).model_dump() == raw
        for key in raw:
            assert cleaned[key] is raw[key]

    def test_preserves_native_values_in_string_unions(self):
        tool = TextTool()
        raw = {
            "nullable_text": None,
            "text_or_object": {"status": "FAILED"},
            "object_or_text": {"status": "FAILED"},
            "text_or_array": ["FAILED"],
            "array_or_text": ["FAILED"],
        }

        cleaned = tool._clean_params(raw)
        params = tool.input_model.model_validate(cleaned)

        assert cleaned == raw
        for key, value in raw.items():
            assert cleaned[key] is value
            assert getattr(params, key) == value

    @pytest.mark.parametrize("raw", [{"items": "[broken"}, {"metadata": "{broken"}])
    def test_leaves_malformed_structures_for_validation(self, raw):
        tool = StructuredTool()

        cleaned = tool._clean_params(raw)

        assert cleaned == raw
        with pytest.raises(ValidationError):
            tool.input_model.model_validate(cleaned)


class TestSearchLiteralQuery:
    @pytest.mark.parametrize("query", ['{"status":"FAILED"}', '["FAILED"]'])
    def test_run_passes_literal_query_to_search(self, query):
        tool = SearchTool()
        context = ToolContext(user=None, organization=None, workspace=None)

        with patch.object(tool, "_search_datasets", return_value=[]) as search:
            result = tool.run(
                {"query": query, "entity_types": '["datasets"]'}, context
            )

        assert not result.is_error, result.content
        assert result.data["query"] == query
        search.assert_called_once_with(query, 10)
