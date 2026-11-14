"""Regression tests for DirectEvaluator scoring integration with score_eval_output.

Covers the review fixes on TH-6388:
- Labeled ``choices`` templates with ``choice_scores`` populated now flow
  through the scorer instead of hedging at ``0.5`` (both in the
  ``run_eval_func`` path and the direct-eval-class fallback path).
- Nested-list ``eval_results`` shapes ``[[{...}]]`` no longer crash the
  reason extraction after scoring.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class _RunResult:
    def __init__(self, eval_results):
        self.eval_results = eval_results


def _eval_template(
    output="choices",
    choices=None,
    choice_scores=None,
    output_type_normalized="deterministic",
    template_id="00000000-0000-0000-0000-000000000001",
    name="test_eval",
    model="gpt-4o",
):
    return SimpleNamespace(
        id=template_id,
        name=name,
        model=model,
        config={
            "output": output,
            "config": {"choices": choices or []},
            "eval_type_id": "LlmEvaluator",
        },
        choice_scores=choice_scores,
        choices=choices or [],
        multi_choice=False,
        output_type_normalized=output_type_normalized,
    )


def _direct_evaluator():
    from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator

    ev = DirectEvaluator.__new__(DirectEvaluator)
    ev.organization = None
    ev.workspace = None
    ev.eval_source = "test"
    ev.execution_model = "gpt-4o"
    return ev


class _EvalConfig:
    def __init__(self, eval_template):
        self.eval_template = eval_template
        self.mapping = {"output_text": "output"}
        self.config = {"eval_type_id": "LlmEvaluator"}


@pytest.mark.unit
class TestChoicesSkipGateWithConfig:
    """Labeled choices with choice_scores should reach the scorer via the
    ``run_eval_func`` path in ``_run_eval_with_config``.
    """

    def test_labeled_choices_with_choice_scores_bypass_skip(self):
        template = _eval_template(
            output="choices",
            choices=["good", "bad", "ok"],
            choice_scores={"good": 1.0, "bad": 0.0, "ok": 0.5},
        )
        # run_eval_func returns the post-format_eval_value payload.
        fake_eval_result = {"output": {"score": 1.0, "choice": "good"}, "reason": "r"}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            return_value=fake_eval_result,
        ), patch(
            "model_hub.models.evals_metric.EvalTemplate.objects.get",
            side_effect=Exception("skip DB fetch"),
        ):
            score, reason = _direct_evaluator()._run_eval_with_config(
                _EvalConfig(template), {}, "output_text", "prompt"
            )
        assert score == 1.0
        assert reason == "r"

    def test_labeled_choices_without_choice_scores_still_hedges(self):
        template = _eval_template(
            output="choices",
            choices=["good", "bad"],
            choice_scores=None,
        )
        score, reason = _direct_evaluator()._run_eval_with_config(
            _EvalConfig(template), {}, "output_text", "prompt"
        )
        assert score == 0.5
        assert reason == "skipped_choices"

    def test_numeric_choices_still_bypass_skip(self):
        template = _eval_template(
            output="choices",
            choices=["0.0", "0.5", "1.0"],
            choice_scores=None,
        )
        fake_eval_result = {"output": "0.5", "reason": "r"}
        with patch(
            "model_hub.views.utils.evals.run_eval_func",
            return_value=fake_eval_result,
        ), patch(
            "model_hub.models.evals_metric.EvalTemplate.objects.get",
            side_effect=Exception("skip DB fetch"),
        ):
            score, _ = _direct_evaluator()._run_eval_with_config(
                _EvalConfig(template), {}, "output_text", "prompt"
            )
        assert score == 0.5


@pytest.mark.unit
class TestNestedListResultReasonExtraction:
    """``_run_eval_direct`` must not crash extracting the reason when the
    evaluator returns a nested-list ``eval_results`` shape.
    """

    def test_nested_list_result_reason_unwrapped(self):
        template = _eval_template(
            output="score", output_type_normalized="percentage"
        )
        result = _RunResult(
            eval_results=[
                [
                    {
                        "data": None,
                        "failure": False,
                        "reason": "nested_reason",
                        "runtime": 0,
                        "model": "gpt-4o",
                        "metrics": [{"value": 0.72}],
                        "metadata": {},
                    }
                ]
            ]
        )
        fake_eval_class = MagicMock()
        fake_eval_class.return_value.run.return_value = result

        # Route _run_eval_direct's getattr(fi_evals, eval_type_id) lookup onto
        # our fake by adding the id to the fi_evals namespace for the call.
        import agentic_eval.core_evals.fi_evals as fi_evals

        original = getattr(fi_evals, "LlmEvaluator", None)
        fi_evals.LlmEvaluator = fake_eval_class
        try:
            score, reason = _direct_evaluator()._run_eval_direct(
                template, {"output_text": "x"}
            )
        finally:
            if original is None:
                del fi_evals.LlmEvaluator
            else:
                fi_evals.LlmEvaluator = original

        assert score == 0.72
        assert reason == "nested_reason"
