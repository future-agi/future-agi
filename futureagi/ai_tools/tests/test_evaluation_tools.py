import uuid

import pytest

from ai_tools.registry import registry
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_eval_template, make_evaluation

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_template(tool_context):
    return make_eval_template(tool_context)


@pytest.fixture
def user_eval_template(tool_context):
    """User-owned template (editable/deletable)."""
    return make_eval_template(tool_context, name="my-custom-eval", owner="user")


@pytest.fixture
def evaluation(tool_context, eval_template):
    return make_evaluation(tool_context, eval_template=eval_template)


# ===================================================================
# READ TOOLS
# ===================================================================


class TestListEvaluationsTool:
    def test_list_empty(self, tool_context):
        result = run_tool("list_evaluations", {}, tool_context)

        assert not result.is_error
        assert "Evaluations (0)" in result.content
        assert result.data["total"] == 0

    def test_list_with_data(self, tool_context, evaluation):
        result = run_tool("list_evaluations", {}, tool_context)

        assert not result.is_error
        assert "Evaluations (1)" in result.content
        assert "test-eval" in result.content
        assert "completed" in result.content
        assert result.data["total"] == 1

    def test_list_filter_by_status(self, tool_context, evaluation):
        result = run_tool("list_evaluations", {"status": "completed"}, tool_context)
        assert result.data["total"] == 1

        result = run_tool("list_evaluations", {"status": "failed"}, tool_context)
        assert result.data["total"] == 0

    def test_list_pagination(self, tool_context, evaluation):
        result = run_tool("list_evaluations", {"limit": 1, "offset": 0}, tool_context)

        assert not result.is_error
        assert len(result.data["evaluations"]) <= 1


class TestGetEvaluationTool:
    def test_get_existing(self, tool_context, evaluation):
        result = run_tool(
            "get_evaluation", {"evaluation_id": str(evaluation.id)}, tool_context
        )

        assert not result.is_error
        assert "test-eval" in result.content
        assert "completed" in result.content
        assert result.data["id"] == str(evaluation.id)

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_evaluation", {"evaluation_id": str(uuid.uuid4())}, tool_context
        )

        assert result.is_error
        assert "Not Found" in result.content

    def test_get_shows_metrics(self, tool_context, evaluation):
        result = run_tool(
            "get_evaluation", {"evaluation_id": str(evaluation.id)}, tool_context
        )

        assert "accuracy" in result.content

    def test_get_invalid_uuid(self, tool_context):
        result = run_tool(
            "get_evaluation", {"evaluation_id": "not-a-uuid"}, tool_context
        )

        assert result.is_error


class TestListEvalTemplates:
    def test_list_empty(self, tool_context):
        result = run_tool("list_eval_templates", {}, tool_context)
        assert not result.is_error

    def test_list_with_template(self, tool_context, eval_template):
        result = run_tool("list_eval_templates", {}, tool_context)
        assert not result.is_error
        assert "test-eval" in result.content

    def test_list_filter_by_owner(
        self, tool_context, eval_template, user_eval_template
    ):
        result = run_tool("list_eval_templates", {"owner": "user"}, tool_context)
        assert not result.is_error
        # Should find the user-owned template
        assert "my-custom-eval" in result.content


class TestGetEvalTemplate:
    def test_get_existing(self, tool_context, eval_template):
        result = run_tool(
            "get_eval_template",
            {"eval_template_id": str(eval_template.id)},
            tool_context,
        )
        assert not result.is_error
        assert "test-eval" in result.content

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_eval_template", {"eval_template_id": str(uuid.uuid4())}, tool_context
        )
        assert result.is_error


# ===================================================================
# WRITE TOOLS
# ===================================================================


class TestCreateEvalTemplateTool:
    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "new-eval",
                "description": "Test eval",
                "criteria": "Evaluate {{response}}",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert not result.is_error
        assert "Eval Template Created" in result.content
        assert result.data["name"] == "new-eval"
        assert result.data["id"]

    def test_create_with_criteria(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "criteria-eval",
                "criteria": "Check if {{response}} is helpful",
                "model": "gpt-4o",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert not result.is_error
        assert "criteria-eval" in result.content

    def test_create_without_criteria(self, tool_context):
        """LLM-as-judge eval without criteria/instructions should fail."""
        result = run_tool(
            "create_eval_template",
            {
                "name": "no-criteria-eval",
                "eval_type": "llm",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert result.is_error
        assert "instructions are required" in result.content.lower()

    def test_create_duplicate_user_name(self, tool_context):
        run_tool(
            "create_eval_template",
            {
                "name": "dup-eval",
                "criteria": "Evaluate {{response}}",
                "required_keys": ["response"],
            },
            tool_context,
        )
        result = run_tool(
            "create_eval_template",
            {
                "name": "dup-eval",
                "criteria": "Evaluate {{response}}",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert result.is_error
        assert "already exists" in result.content

    def test_create_duplicate_system_name(self, tool_context, eval_template):
        """Cannot create user template with same name as system template."""
        result = run_tool(
            "create_eval_template",
            {
                "name": eval_template.name,
                "criteria": "Evaluate {{response}}",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert result.is_error
        assert "already exists" in result.content


class TestUpdateEvalTemplateTool:
    def test_update_name(self, tool_context, user_eval_template):
        result = run_tool(
            "update_eval_template",
            {
                "eval_template_id": str(user_eval_template.id),
                "name": "renamed-eval",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "renamed-eval"

    def test_update_criteria(self, tool_context, user_eval_template):
        result = run_tool(
            "update_eval_template",
            {
                "eval_template_id": str(user_eval_template.id),
                "criteria": "New {{response}} criteria text",
            },
            tool_context,
        )

        assert not result.is_error

    def test_update_system_template_fails(self, tool_context, eval_template):
        """Cannot update system-owned templates."""
        result = run_tool(
            "update_eval_template",
            {
                "eval_template_id": str(eval_template.id),
                "name": "Cannot Rename",
            },
            tool_context,
        )

        assert result.is_error

    def test_update_nonexistent(self, tool_context):
        result = run_tool(
            "update_eval_template",
            {"eval_template_id": str(uuid.uuid4()), "name": "Nope"},
            tool_context,
        )

        assert result.is_error


class TestDeleteEvalTemplateTool:
    def test_delete_user_template(self, tool_context, user_eval_template):
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(user_eval_template.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["name"] == "my-custom-eval"

    def test_delete_system_template_fails(self, tool_context, eval_template):
        """Cannot delete system-owned templates."""
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(eval_template.id)},
            tool_context,
        )

        assert result.is_error

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(uuid.uuid4())},
            tool_context,
        )

        assert result.is_error

    def test_delete_already_deleted(self, tool_context, user_eval_template):
        run_tool(
            "delete_eval_template",
            {"eval_template_id": str(user_eval_template.id)},
            tool_context,
        )
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(user_eval_template.id)},
            tool_context,
        )

        assert result.is_error
