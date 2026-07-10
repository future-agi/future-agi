"""Turing model rejected with a clear error when the ee/ client is stripped (TH-6725)."""

import sys

import pytest


@pytest.fixture
def turing_evaluator():
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator,
    )

    return CustomPromptEvaluator(
        rule_prompt="Rate {{output}}",
        model="turing_large",
        output_type="Pass/Fail",
    )


def test_turing_model_raises_actionable_error_when_ee_client_missing(
    monkeypatch, turing_evaluator
):
    # None entry in sys.modules makes the `from ee.turing.client import X`
    # raise ImportError deterministically.
    monkeypatch.setitem(sys.modules, "ee.turing.client", None)

    with pytest.raises(ValueError, match=r"Turing model 'turing_large' is not available"):
        turing_evaluator._evaluate(input="hello", output="hi")
