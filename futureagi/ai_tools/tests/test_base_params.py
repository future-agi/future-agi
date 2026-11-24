from unittest import TestCase

from pydantic import BaseModel, ValidationError

from ai_tools.base import BaseTool, ToolResult


class WidgetInput(BaseModel):
    title: str


class TextOrStructureInput(BaseModel):
    query: str | dict[str, str] | list[str]


class ToolInput(BaseModel):
    query: str
    description: str | None = None
    labels: list[str] | None = None
    options: dict[str, str] | None = None
    widget: WidgetInput | None = None


class EchoTool(BaseTool):
    input_model = ToolInput

    def execute(self, params, context):
        return ToolResult(content=params.query)


class TestCleanParams(TestCase):
    def setUp(self):
        self.tool = EchoTool()

    def test_json_text_is_preserved(self):
        for query in ('{"status":"FAILED"}', '["ERROR"]', '  {"a": 1}  '):
            with self.subTest(query=query):
                cleaned = self.tool._clean_params({"query": query})
                self.assertEqual(ToolInput.model_validate(cleaned).query, query)

    def test_nullable_string_is_preserved_alongside_structured_fields(self):
        raw = {
            "query": "error",
            "description": '["literal text"]',
            "labels": '["ERROR"]',
            "options": '{"status":"FAILED"}',
            "widget": '{"title":"Errors"}',
        }
        cleaned = self.tool._clean_params(raw)
        params = ToolInput.model_validate(cleaned)
        self.assertEqual(params.description, '["literal text"]')
        self.assertEqual(params.labels, ["ERROR"])
        self.assertEqual(params.options, {"status": "FAILED"})
        self.assertEqual(params.widget, WidgetInput(title="Errors"))
        self.assertEqual(raw["labels"], '["ERROR"]')

    def test_string_or_structure_keeps_the_supplied_value_type(self):
        tool = EchoTool()
        tool.input_model = TextOrStructureInput
        for query in (
            '{"status":"FAILED"}',
            '["ERROR"]',
            {"status": "FAILED"},
            ["ERROR"],
        ):
            with self.subTest(query=query):
                cleaned = tool._clean_params({"query": query})
                params = TextOrStructureInput.model_validate(cleaned)
                self.assertEqual(params.query, query)
                self.assertIs(type(params.query), type(query))

    def test_native_values_and_input_mapping_are_unchanged(self):
        raw = {"query": "error", "description": None, "labels": ["ERROR"]}
        cleaned = self.tool._clean_params(raw)
        self.assertEqual(cleaned, raw)
        self.assertIsNot(cleaned, raw)
        self.assertEqual(raw["labels"], ["ERROR"])

    def test_malformed_structured_json_is_left_for_validation(self):
        raw = {"query": "error", "labels": "[not json]"}
        cleaned = self.tool._clean_params(raw)
        self.assertEqual(cleaned, raw)
        with self.assertRaises(ValidationError):
            ToolInput.model_validate(cleaned)
