"""The library RougeScore returns a numeric metric and a rounded explanation."""

from unittest.mock import patch

import pytest

from agentic_eval.core_evals.fi_evals import RougeScore
from agentic_eval.core_evals.fi_evals.function.functions import calculate_rouge
from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.models.evals_metric import EvalTemplate


def test_rouge_result_is_numeric():
    out = calculate_rouge("the cat sat on the mat", "the cat sat on the mat")
    assert isinstance(out["result"], float)
    assert out["result"] == 1.0
    assert out["reason"] == "ROUGE score: ROUGE-1: P=1.000, R=1.000, F=1.000"


@pytest.mark.django_db
def test_saved_rouge_template_marks_zero_as_failed():
    template = EvalTemplate.objects.create(
        name="rouge_score",
        config={
            "eval_type_id": "RougeScore",
            "output": "score",
            "required_keys": ["reference", "hypothesis"],
            "config": {},
        },
    )
    template.refresh_from_db()
    request = EvalRequest(
        eval_template=template,
        inputs={"reference": "completely different", "hypothesis": "xyz qrs tuv"},
    )
    # Exercise dispatch, the public run method and formatting without sending usage.
    with (
        patch("agentic_eval.core_evals.fi_evals.base_evaluator.FiApiService.log_usage"),
        patch.object(RougeScore, "_log_evaluation_request", return_value=None),
    ):
        result = run_eval(request)
    assert result.failure is True
    assert isinstance(result.value, float)
    assert result.value == 0.0
    assert result.reason == "ROUGE score: ROUGE-1: P=0.000, R=0.000, F=0.000"


def test_partial_rouge_metric_retains_precision():
    result = RougeScore()._evaluate(reference="cats sleep", hypothesis="cat")
    assert result["failure"] is False
    assert result["metrics"] == [{"id": "passed", "value": pytest.approx(2 / 3)}]
    assert result["reason"] == "ROUGE score: ROUGE-1: P=1.000, R=0.500, F=0.667"
