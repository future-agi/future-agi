"""Behavioral regression test for #315 / PR #347.

The bug: CustomPromptEvaluator did not inherit BaseEvaluator and left abstract members
unimplemented -- an LSP violation (it could not be substituted for a BaseEvaluator). The
fix makes it subclass BaseEvaluator and implement the abstract properties.

Uses the REAL classes (not a stub). Imports are deferred into the test body on purpose:
importing agentic_eval.core_evals.fi_evals at module top-level fails under pytest's
collection-time package rooting, though it imports cleanly standalone / at run-time.
"""
import pytest


@pytest.mark.unit
def test_custom_prompt_evaluator_satisfies_base_evaluator_lsp():
    from agentic_eval.core_evals.fi_evals.base_evaluator import BaseEvaluator
    from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
        CustomPromptEvaluator,
    )

    # LSP: a CustomPromptEvaluator IS-A BaseEvaluator (pre-fix: it was not).
    assert issubclass(CustomPromptEvaluator, BaseEvaluator)
    # Every abstract member is implemented -> concrete and substitutable.
    assert CustomPromptEvaluator.__abstractmethods__ == frozenset()
