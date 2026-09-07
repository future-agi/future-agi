"""Tests for the terminal status the evaluation activity writes back."""

import uuid

import pytest
from django.db import DatabaseError

from model_hub.models.evaluation import Evaluation, StatusChoices


@pytest.fixture
def eval_template(db):
    from model_hub.models.evals_metric import EvalTemplate

    template, _ = EvalTemplate.objects.get_or_create(
        name="Test Activity Eval Template",
        defaults={
            "description": "A test evaluation template for the activity",
            "eval_id": 999004,
            "eval_tags": ["test"],
            "config": {
                "eval_type_id": "TestEval",
                "required_keys": ["input", "output"],
                "optional_keys": [],
            },
            "owner": "system",
            "organization": None,
        },
    )
    return template


@pytest.fixture
def evaluation(user, workspace, eval_template):
    return Evaluation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        eval_template=eval_template,
        input_data={"input": "a question", "output": "an answer"},
        eval_config={},
    )


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestRunSingleEvaluationSync:
    def test_unknown_evaluation_id_returns_failed_without_raising(self):
        from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

        unknown_id = str(uuid.uuid4())

        result = _run_single_evaluation_sync(unknown_id)

        assert result["evaluation_id"] == unknown_id
        assert result["status"] == StatusChoices.FAILED
        assert result["error"]

    def test_row_that_cannot_be_loaded_is_left_failed(self, evaluation, monkeypatch):
        from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

        def _not_visible(*args, **kwargs):
            raise Evaluation.DoesNotExist("Evaluation matching query does not exist.")

        with monkeypatch.context() as patched:
            patched.setattr(Evaluation.objects, "select_related", _not_visible)
            result = _run_single_evaluation_sync(str(evaluation.id))

        assert result["status"] == StatusChoices.FAILED

        evaluation.refresh_from_db()
        assert evaluation.status == StatusChoices.FAILED
        assert evaluation.error_message

    def test_row_whose_terminal_save_fails_is_left_failed(
        self, evaluation, monkeypatch
    ):
        from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

        original_save = Evaluation.save

        def _reject_terminal_save(self, *args, **kwargs):
            if kwargs.get("update_fields"):
                return original_save(self, *args, **kwargs)
            raise DatabaseError("terminal status write rejected")

        def _engine_unavailable(*args, **kwargs):
            raise RuntimeError("eval engine unavailable")

        with monkeypatch.context() as patched:
            patched.setattr("sdk.utils.evaluations._run_eval", _engine_unavailable)
            patched.setattr(Evaluation, "save", _reject_terminal_save)
            result = _run_single_evaluation_sync(str(evaluation.id))

        assert result["status"] == StatusChoices.FAILED

        evaluation.refresh_from_db()
        assert evaluation.status == StatusChoices.FAILED
        assert "eval engine unavailable" in evaluation.error_message

    def test_malformed_evaluation_id_returns_failed_without_raising(self):
        from tfc.temporal.evaluations.activities import _run_single_evaluation_sync

        result = _run_single_evaluation_sync("not-a-uuid")

        assert result["evaluation_id"] == "not-a-uuid"
        assert result["status"] == StatusChoices.FAILED
        assert result["error"]
