import uuid
from unittest.mock import patch

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
        assert "Test Eval" in result.content
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


class TestCompareEvaluationsTool:
    def test_compare_missing_ids_returns_candidates(self, tool_context, evaluation):
        result = run_tool("compare_evaluations", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert "Evaluation IDs Required" in result.content
        assert str(evaluation.id) in result.content
        assert result.data["candidate_evaluations"][0]["id"] == str(evaluation.id)

    def test_compare_existing(self, tool_context, evaluation):
        second_template = make_eval_template(tool_context, name="Second Eval")
        second = make_evaluation(
            tool_context,
            eval_template=second_template,
            value="Fail",
            metrics={"accuracy": 0.2},
        )

        result = run_tool(
            "compare_evaluations",
            {"evaluation_ids": [str(evaluation.id), str(second.id)]},
            tool_context,
        )

        assert not result.is_error
        assert "Evaluation Comparison (2 evaluations)" in result.content
        assert str(evaluation.id) in result.content
        assert str(second.id) in result.content


class TestGetEvalLogDetailTool:
    def test_invalid_log_id_returns_candidates(self, tool_context):
        with patch("tfc.ee_gating.is_oss", return_value=False):
            result = run_tool(
                "get_eval_log_detail",
                {"log_id": "INVALID-ID-FORMAT"},
                tool_context,
            )

        assert not result.is_error
        assert result.data["requires_log_id"] is True
        assert "Eval Log Not Found" in result.content


class TestGetEvaluationTool:
    def test_get_existing(self, tool_context, evaluation):
        result = run_tool(
            "get_evaluation", {"evaluation_id": str(evaluation.id)}, tool_context
        )

        assert not result.is_error
        assert "Test Eval" in result.content
        assert "completed" in result.content
        assert result.data["id"] == str(evaluation.id)

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_evaluation", {"evaluation_id": str(uuid.uuid4())}, tool_context
        )

        assert not result.is_error
        assert result.data["requires_evaluation_id"] is True
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

        assert not result.is_error
        assert result.data["requires_evaluation_id"] is True


class TestSubmitEvalFeedbackTool:
    def test_missing_template_returns_candidates(self, tool_context, eval_template):
        result = run_tool("submit_eval_feedback", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_eval_template_id"] is True
        assert str(eval_template.id) in result.content

    def test_missing_feedback_fields_returns_needs_input(
        self, tool_context, eval_template
    ):
        result = run_tool(
            "submit_eval_feedback",
            {"eval_template_id": eval_template.name},
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_source_id"] is True
        assert result.data["requires_feedback_value"] is True

    def test_submit_feedback(self, tool_context, eval_template):
        result = run_tool(
            "submit_eval_feedback",
            {
                "eval_template_id": str(eval_template.id),
                "source_id": "falcon_feedback_source",
                "feedback_value": "passed",
                "source": "eval_playground",
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["eval_template_id"] == str(eval_template.id)
        assert result.data["value"] == "passed"


class TestListEvalTemplates:
    def test_list_empty(self, tool_context):
        result = run_tool("list_eval_templates", {}, tool_context)
        assert not result.is_error

    def test_list_with_template(self, tool_context, eval_template):
        result = run_tool("list_eval_templates", {}, tool_context)
        assert not result.is_error
        assert "Test Eval" in result.content

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
        assert "Test Eval" in result.content

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_eval_template", {"eval_template_id": str(uuid.uuid4())}, tool_context
        )
        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True


class TestDuplicateEvalTemplateTool:
    def test_missing_template_returns_candidates(self, tool_context, user_eval_template):
        result = run_tool(
            "duplicate_eval_template",
            {"eval_template_id": str(uuid.uuid4()), "name": "copy_eval"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True
        assert str(user_eval_template.id) in result.content

    def test_system_template_returns_user_owned_candidates(
        self, tool_context, eval_template, user_eval_template
    ):
        result = run_tool(
            "duplicate_eval_template",
            {"eval_template_id": str(eval_template.id), "name": "copy_eval"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True
        assert str(user_eval_template.id) in result.content

    def test_existing_name_returns_recoverable_suggestion(
        self, tool_context, user_eval_template
    ):
        result = run_tool(
            "duplicate_eval_template",
            {
                "eval_template_id": str(user_eval_template.id),
                "name": user_eval_template.name,
            },
            tool_context,
        )

        assert not result.is_error
        assert result.status == "needs_input"
        assert result.data["requires_name"] is True
        assert result.data["existing_template_id"] == str(user_eval_template.id)
        assert result.data["suggested_name"] == f"{user_eval_template.name}_2"


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

    def test_create_without_variable_in_criteria(self, tool_context):
        """Falcon repairs criteria without variables by adding safe default variables."""
        result = run_tool(
            "create_eval_template",
            {
                "name": "no-var-eval",
                "criteria": "Check if the response is helpful",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert not result.is_error
        assert "{{input}}" in result.data["criteria"]
        assert "{{output}}" in result.data["criteria"]

    def test_create_without_criteria(self, tool_context):
        """Falcon supplies default criteria when criteria is omitted."""
        result = run_tool(
            "create_eval_template",
            {
                "name": "no-criteria-eval",
                "required_keys": ["response"],
            },
            tool_context,
        )

        assert not result.is_error
        assert "{{input}}" in result.data["criteria"]
        assert "{{output}}" in result.data["criteria"]

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

        assert not result.is_error
        assert result.data["already_exists"] is True
        assert "already exists" in result.content.lower()

    def test_create_duplicate_system_name(self, tool_context, eval_template):
        """Cannot create user template with same name as system template."""
        result = run_tool(
            "create_eval_template",
            {"name": eval_template.name},
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

        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True

    def test_delete_nonexistent(self, tool_context):
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(uuid.uuid4())},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True

    def test_delete_already_deleted(self, tool_context, user_eval_template):
        run_tool(
            "delete_eval_template",
            {
                "eval_template_id": str(user_eval_template.id),
                "confirm_delete": True,
            },
            tool_context,
        )
        result = run_tool(
            "delete_eval_template",
            {"eval_template_id": str(user_eval_template.id)},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_template_id"] is True


class TestCreateEvalGroupTool:
    def test_create_group_missing_inputs_returns_candidates(self, tool_context, eval_template):
        result = run_tool("create_eval_group", {}, tool_context)

        assert not result.is_error
        assert result.status == "needs_input"
        assert "Eval Group Inputs Required" in result.content
        assert str(eval_template.id) in result.content
        assert result.data["candidate_templates"][0]["id"] == str(eval_template.id)

    def test_create_group(self, tool_context, eval_template):
        result = run_tool(
            "create_eval_group",
            {
                "name": "Test Group",
                "eval_template_ids": [str(eval_template.id)],
            },
            tool_context,
        )

        assert not result.is_error
        assert "Eval Group Created" in result.content
        assert result.data["template_count"] == 1

    def test_create_group_missing_templates(self, tool_context):
        result = run_tool(
            "create_eval_group",
            {
                "name": "Bad Group",
                "eval_template_ids": [str(uuid.uuid4())],
            },
            tool_context,
        )

        assert result.is_error
        assert "not found" in result.content.lower()

    def test_create_group_multiple_templates(self, tool_context):
        t1 = make_eval_template(tool_context, name="Eval A")
        t2 = make_eval_template(tool_context, name="Eval B")

        result = run_tool(
            "create_eval_group",
            {
                "name": "Multi Group",
                "eval_template_ids": [str(t1.id), str(t2.id)],
            },
            tool_context,
        )

        assert not result.is_error
        assert result.data["template_count"] == 2


class TestTriggerErrorLocalizationTool:
    def test_invalid_eval_log_id_returns_candidates(self, tool_context):
        result = run_tool(
            "trigger_error_localization",
            {"eval_log_id": "log_text_sentence_001"},
            tool_context,
        )

        assert not result.is_error
        assert result.data["requires_eval_log_id_or_evaluation_id"] is True
