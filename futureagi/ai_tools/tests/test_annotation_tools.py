import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_annotation_label


@pytest.fixture
def annotation_label(tool_context):
    return make_annotation_label(tool_context)


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListAnnotationLabelsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_annotation_labels", {}, tool_context)
        assert not result.is_error
        assert result.data["total"] == 0

    def test_list_with_label(self, tool_context, annotation_label):
        result = run_tool("list_annotation_labels", {}, tool_context)
        assert not result.is_error
        assert result.data["total"] == 1
        assert "Test Label" in result.content

    def test_list_filter_by_type(self, tool_context, annotation_label):
        result = run_tool(
            "list_annotation_labels", {"label_type": "categorical"}, tool_context
        )
        assert result.data["total"] == 1

        result = run_tool(
            "list_annotation_labels", {"label_type": "text"}, tool_context
        )
        assert result.data["total"] == 0

    def test_list_pagination(self, tool_context, annotation_label):
        result = run_tool(
            "list_annotation_labels", {"limit": 1, "offset": 0}, tool_context
        )
        assert not result.is_error
        assert len(result.data["labels"]) <= 1


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateAnnotationLabelTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_annotation_label",
            {
                "name": "Quality",
                "label_type": "categorical",
                "settings": {
                    "options": [{"label": "Good"}, {"label": "Bad"}],
                    "multi_choice": False,
                    "rule_prompt": "",
                    "auto_annotate": False,
                    "strategy": None,
                },
            },
            tool_context,
        )
        assert not result.is_error
        assert "Annotation Label Created" in result.content
        assert result.data["name"] == "Quality"
        assert result.data["type"] == "categorical"

    def test_create_with_settings(self, tool_context):
        result = run_tool(
            "create_annotation_label",
            {
                "name": "Score",
                "label_type": "numeric",
                "settings": {
                    "min": 0,
                    "max": 10,
                    "step_size": 1,
                    "display_type": "slider",
                },
            },
            tool_context,
        )
        assert not result.is_error

    def test_create_invalid_type(self, tool_context):
        result = run_tool(
            "create_annotation_label",
            {"name": "Bad", "label_type": "invalid_type"},
            tool_context,
        )
        assert result.is_error
        assert "Invalid label type" in result.content

    def test_create_duplicate(self, tool_context):
        run_tool(
            "create_annotation_label",
            {"name": "Dup Label", "label_type": "text"},
            tool_context,
        )
        result = run_tool(
            "create_annotation_label",
            {"name": "Dup Label", "label_type": "text"},
            tool_context,
        )
        assert result.is_error
        assert "already exists" in result.content

    def test_create_same_name_different_type(self, tool_context):
        run_tool(
            "create_annotation_label",
            {"name": "Shared Name", "label_type": "text"},
            tool_context,
        )
        result = run_tool(
            "create_annotation_label",
            {
                "name": "Shared Name",
                "label_type": "star",
                "settings": {"no_of_stars": 5},
            },
            tool_context,
        )
        # Same name but different type should succeed
        assert not result.is_error

    def test_create_all_types(self, tool_context):
        # Some types require specific settings
        type_settings = {
            "text": {},
            "numeric": {"min": 0, "max": 10, "step_size": 1, "display_type": "slider"},
            "categorical": {
                "options": [{"label": "A"}, {"label": "B"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
            "star": {"no_of_stars": 5},
            "thumbs_up_down": {},
        }
        for label_type, settings in type_settings.items():
            params = {"name": f"label-{label_type}", "label_type": label_type}
            if settings:
                params["settings"] = settings
            result = run_tool("create_annotation_label", params, tool_context)
            assert not result.is_error, f"Failed for type: {label_type}"


class TestDeleteAnnotationLabelTool:
    def test_delete_existing(self, tool_context, annotation_label):
        result = run_tool(
            "delete_annotation_label",
            {"label_id": str(annotation_label.id)},
            tool_context,
        )
        assert not result.is_error
        assert result.data["label_name"] == "Test Label"

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_annotation_label",
            {"label_id": str(uuid.uuid4())},
            tool_context,
        )
        assert result.is_error
        assert "Not Found" in result.content


class TestUpdateAnnotationLabelTool:
    def test_update_name(self, tool_context, annotation_label):
        result = run_tool(
            "update_annotation_label",
            {"label_id": str(annotation_label.id), "name": "Renamed Label"},
            tool_context,
        )
        assert not result.is_error
        assert "Renamed Label" in result.content

    def test_update_description(self, tool_context, annotation_label):
        result = run_tool(
            "update_annotation_label",
            {"label_id": str(annotation_label.id), "description": "New desc"},
            tool_context,
        )
        assert not result.is_error
        assert "Description updated" in result.content

    def test_update_settings(self, tool_context, annotation_label):
        # annotation_label is categorical, so update with valid categorical settings
        result = run_tool(
            "update_annotation_label",
            {
                "label_id": str(annotation_label.id),
                "settings": {
                    "options": [{"label": "Good"}, {"label": "Bad"}],
                    "multi_choice": False,
                    "rule_prompt": "",
                    "auto_annotate": False,
                    "strategy": None,
                },
            },
            tool_context,
        )
        assert not result.is_error
        assert "Settings updated" in result.content

    def test_update_nonexistent(self, tool_context):
        result = run_tool(
            "update_annotation_label",
            {"label_id": str(uuid.uuid4()), "name": "Nope"},
            tool_context,
        )
        assert result.is_error
