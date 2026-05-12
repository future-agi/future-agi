import pytest

from ai_tools.base import BaseTool, EmptyInput, ToolContext, ToolResult
from ai_tools.registry import ToolRegistry, registry


class DummyTool(BaseTool):
    name = "dummy_tool"
    description = "A dummy tool for testing"
    category = "test"
    input_model = EmptyInput

    def execute(self, params, context):
        return ToolResult(content="dummy result")


class AnotherDummyTool(BaseTool):
    name = "another_dummy"
    description = "Another dummy tool"
    category = "test"
    input_model = EmptyInput

    def execute(self, params, context):
        return ToolResult(content="another result")


class DifferentCategoryTool(BaseTool):
    name = "different_cat"
    description = "Tool in different category"
    category = "other"
    input_model = EmptyInput

    def execute(self, params, context):
        return ToolResult(content="different category")


class TestToolRegistry:
    def test_register_and_get(self, fresh_registry):
        tool = DummyTool()
        fresh_registry.register(tool)
        assert fresh_registry.get("dummy_tool") is tool

    def test_get_nonexistent(self, fresh_registry):
        assert fresh_registry.get("nonexistent") is None

    def test_register_duplicate_raises(self, fresh_registry):
        fresh_registry.register(DummyTool())
        with pytest.raises(ValueError, match="already registered"):
            fresh_registry.register(DummyTool())

    def test_list_all(self, fresh_registry):
        fresh_registry.register(DummyTool())
        fresh_registry.register(AnotherDummyTool())
        tools = fresh_registry.list_all()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"dummy_tool", "another_dummy"}

    def test_list_by_category(self, fresh_registry):
        fresh_registry.register(DummyTool())
        fresh_registry.register(AnotherDummyTool())
        fresh_registry.register(DifferentCategoryTool())

        test_tools = fresh_registry.list_by_category("test")
        assert len(test_tools) == 2

        other_tools = fresh_registry.list_by_category("other")
        assert len(other_tools) == 1
        assert other_tools[0].name == "different_cat"

    def test_list_by_category_empty(self, fresh_registry):
        assert fresh_registry.list_by_category("nonexistent") == []

    def test_categories(self, fresh_registry):
        fresh_registry.register(DummyTool())
        fresh_registry.register(DifferentCategoryTool())
        cats = fresh_registry.categories()
        assert set(cats) == {"test", "other"}

    def test_count(self, fresh_registry):
        assert fresh_registry.count() == 0
        fresh_registry.register(DummyTool())
        assert fresh_registry.count() == 1

    def test_clear(self, fresh_registry):
        fresh_registry.register(DummyTool())
        assert fresh_registry.count() == 1
        fresh_registry.clear()
        assert fresh_registry.count() == 0
        assert fresh_registry.categories() == []

    def test_global_registry_has_tools(self):
        """The global registry should have tools registered via @register_tool."""
        assert registry.count() >= 10
        assert registry.get("whoami") is not None
        assert registry.get("list_workspaces") is not None
        assert registry.get("list_evaluations") is not None
        assert registry.get("list_datasets") is not None
        assert registry.get("search_traces") is not None
        assert registry.get("create_dataset_from_file") is not None
        assert registry.get("create_dataset_from_huggingface") is not None
        assert registry.get("trigger_error_localization") is not None
        assert registry.get("get_error_localization_status") is not None
        assert registry.get("get_error_localization_results") is not None
        assert registry.get("web_search") is not None
        assert registry.get("render_widget") is not None

    def test_global_registry_categories(self):
        cats = registry.categories()
        assert "context" in cats
        assert "evaluations" in cats
        assert "datasets" in cats
        assert "tracing" in cats
        assert "web" in cats
        assert "visualization" in cats

    def test_global_registry_categories_have_mcp_groups(self):
        from mcp_server.constants import CATEGORY_TO_GROUP, TOOL_GROUPS

        missing = sorted(
            category
            for category in registry.categories()
            if category not in CATEGORY_TO_GROUP
        )
        dangling = sorted(
            group
            for group in CATEGORY_TO_GROUP.values()
            if group not in TOOL_GROUPS
        )

        assert missing == []
        assert dangling == []


class TestBaseTool:
    def test_tool_to_dict(self):
        tool = DummyTool()
        d = tool.to_dict()
        assert d["name"] == "dummy_tool"
        assert d["description"] == "A dummy tool for testing"
        assert d["category"] == "test"
        assert (
            "properties" in d["input_schema"]
            or d["input_schema"].get("type") == "object"
        )

    def test_input_schema(self):
        tool = DummyTool()
        schema = tool.input_schema
        assert isinstance(schema, dict)

    def test_run_catches_exceptions(self, tool_context):
        class FailingTool(BaseTool):
            name = "failing"
            description = "Fails"
            category = "test"
            input_model = EmptyInput

            def execute(self, params, context):
                raise RuntimeError("intentional error")

        tool = FailingTool()
        result = tool.run({}, tool_context)
        assert result.is_error
        assert "intentional error" in result.content

    def test_run_validates_input(self, tool_context):
        from pydantic import BaseModel, Field

        class StrictInput(BaseModel):
            required_field: str = Field(description="Required")

        class StrictTool(BaseTool):
            name = "strict"
            description = "Strict"
            category = "test"
            input_model = StrictInput

            def execute(self, params, context):
                return ToolResult(content="ok")

        tool = StrictTool()
        # Missing required field
        result = tool.run({}, tool_context)
        assert result.is_error
        assert "Invalid parameters" in result.content


class TestToolResult:
    def test_error_factory(self):
        result = ToolResult.error("something went wrong")
        assert result.is_error
        assert "something went wrong" in result.content

    def test_not_found_factory(self):
        result = ToolResult.not_found("Dataset", "abc-123")
        assert result.is_error
        assert "Dataset" in result.content
        assert "abc-123" in result.content
