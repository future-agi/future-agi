import uuid
from unittest.mock import patch

import pytest

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
        # Bridged tool (EvalTemplateDetailView): the template payload lives in
        # result.data; the markdown content rendering is generic.
        assert "Test Eval" in str(result.data)

    def test_get_nonexistent(self, tool_context):
        result = run_tool(
            "get_eval_template", {"eval_template_id": str(uuid.uuid4())}, tool_context
        )
        assert result.is_error


class TestTestEvalTemplateTool:
    # Phase 2A conversion note: test_eval_template is now the DRF bridge onto
    # TestEvaluationTemplateAPIView, which DRY-RUNS an eval template
    # DEFINITION (name/config/template_type) without saving it. The old
    # hand-written tool's {eval_template_id, mapping} shape (and its target-
    # template passthrough) was deleted with the HW module.
    def test_dry_run_llm_definition(self, tool_context):
        with patch(
            "model_hub.views.separate_evals.run_eval_func",
            return_value={"data": "Passed", "reason": "ok"},
        ) as mock_run:
            result = run_tool(
                "test_eval_template",
                {
                    "name": "dry-run-eval",
                    "template_type": "Llm",
                    "model": "gpt-4o-mini",
                    "criteria": "Judge {{input}}",
                    "required_keys": ["input"],
                    "output": "Pass/Fail",
                    "output_type": "Pass/Fail",
                    "config": {
                        "mapping": {"input": "hello"},
                        "rule_prompt": "Judge {{input}}",
                        "required_keys": ["input"],
                        "output": "Pass/Fail",
                        "model": "gpt-4o-mini",
                    },
                },
                tool_context,
            )

        assert not result.is_error, result.content
        assert mock_run.called

    def test_dry_run_requires_template_type(self, tool_context):
        result = run_tool(
            "test_eval_template",
            {
                "name": "dry-run-eval",
                "config": {"mapping": {"input": "hello"}},
            },
            tool_context,
        )
        assert result.is_error


# ===================================================================
# WRITE TOOLS
# ===================================================================


# Phase 2A conversion note: create_eval_template is now the DRF bridge onto
# EvalTemplateCreateV2View. The hand-written tool's criteria->instructions
# mapping, template-variable validation, and TH-5254 output_type inference
# heuristics were deleted with the HW module — the V2 view takes explicit
# `instructions` and an explicit `output_type` (default pass_fail).
class TestCreateEvalTemplateTool:
    def _get_template(self, name):
        from model_hub.models.evals_metric import EvalTemplate

        return EvalTemplate.objects.filter(name=name, deleted=False).first()

    def test_create_basic(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "new-eval",
                "description": "Test eval",
                "instructions": "Evaluate {{response}}",
            },
            tool_context,
        )

        assert not result.is_error, result.content
        row = self._get_template("new-eval")
        assert row is not None
        assert "new-eval" in str(result.data)

    def test_create_with_model(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "criteria-eval",
                "instructions": "Check if {{response}} is helpful",
                "model": "gpt-4o",
            },
            tool_context,
        )

        assert not result.is_error, result.content
        assert self._get_template("criteria-eval") is not None

    def test_create_without_instructions_fails(self, tool_context):
        """The V2 view rejects llm-type templates with no instructions."""
        result = run_tool(
            "create_eval_template",
            {"name": "no-instructions-eval"},
            tool_context,
        )

        assert result.is_error
        assert "instructions" in result.content.lower()

    def test_create_duplicate_user_name(self, tool_context):
        first = run_tool(
            "create_eval_template",
            {"name": "dup-eval", "instructions": "Evaluate {{response}}"},
            tool_context,
        )
        assert not first.is_error, first.content
        result = run_tool(
            "create_eval_template",
            {"name": "dup-eval", "instructions": "Evaluate {{response}}"},
            tool_context,
        )

        assert result.is_error

    def test_explicit_output_type_is_respected(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "relevance-score-v2",
                "instructions": "Rate how relevant {{output}} is on a scale of 0 to 1.",
                "output_type": "percentage",
            },
            tool_context,
        )
        assert not result.is_error, result.content
        row = self._get_template("relevance-score-v2")
        assert row is not None
        assert row.output_type_normalized == "percentage"

    def test_default_output_type_pass_fail(self, tool_context):
        result = run_tool(
            "create_eval_template",
            {
                "name": "valid-json-check-v2",
                "instructions": "Determine whether {{output}} is valid JSON.",
            },
            tool_context,
        )
        assert not result.is_error, result.content
        row = self._get_template("valid-json-check-v2")
        assert row is not None
        assert row.output_type_normalized == "pass_fail"


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
        # Bridged tool (DeleteEvalTemplateView): assert the ORM side effect.
        user_eval_template.refresh_from_db()
        assert user_eval_template.deleted is True

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


class TestCreateEvalGroupTool:
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
