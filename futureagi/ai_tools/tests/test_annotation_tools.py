import uuid

import pytest

from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_annotation_label, make_dataset


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


class TestGetAnnotationTool:
    def test_get_annotation_details_alias(self, tool_context):
        from model_hub.models.develop_annotations import Annotations

        dataset = make_dataset(tool_context, name="Alias Dataset")
        annotation = Annotations.objects.create(
            name="Alias Annotation",
            dataset=dataset,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )

        result = run_tool(
            "get_annotation_details",
            {"annotation_id": str(annotation.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["id"] == str(annotation.id)
        assert "Alias Annotation" in result.content

    def test_get_annotation_task_alias_accepts_task_id(self, tool_context):
        from model_hub.models.develop_annotations import Annotations

        dataset = make_dataset(tool_context, name="Task Alias Dataset")
        annotation = Annotations.objects.create(
            name="Task Alias Annotation",
            dataset=dataset,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )

        result = run_tool(
            "get_annotation_task",
            {"annotation_task_id": str(annotation.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["id"] == str(annotation.id)
        assert "Task Alias Annotation" in result.content


class TestGetAnnotateRowTool:
    def test_annotation_without_dataset_returns_recovery(self, tool_context):
        from model_hub.models.develop_annotations import Annotations

        annotation = Annotations.objects.create(
            name="No Dataset Annotation",
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )

        result = run_tool(
            "get_annotate_row",
            {"annotation_id": str(annotation.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_annotation_id"] is True
        assert "Annotation Has No Dataset" in result.content

    def test_annotation_dataset_without_rows_returns_recovery(self, tool_context):
        from model_hub.models.develop_annotations import Annotations

        dataset = make_dataset(tool_context, name="Empty Annotation Dataset")
        annotation = Annotations.objects.create(
            name="Empty Rows Annotation",
            dataset=dataset,
            organization=tool_context.organization,
            workspace=tool_context.workspace,
        )

        result = run_tool(
            "get_annotate_row",
            {"annotation_id": str(annotation.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_rows"] is True
        assert "Annotation Dataset Has No Rows" in result.content


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateAnnotationLabelTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_annotation_label",
            {"name": "Quality", "label_type": "categorical"},
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

    def test_create_categorical_with_options_only(self, tool_context):
        result = run_tool(
            "create_annotation_label",
            {
                "name": "Correctness",
                "label_type": "categorical",
                "settings": {"options": [{"label": "Correct"}, {"label": "Wrong"}]},
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
            {"name": "Shared Name", "label_type": "star"},
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
