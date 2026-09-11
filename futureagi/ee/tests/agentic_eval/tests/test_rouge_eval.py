"""Regression tests for calculate_rouge (RougeScore).

`result` must be the numeric ROUGE-1 fmeasure, like every other evaluator.
The framework decides failure with `not eval_response["result"]` and records
the metric with `float(result_value)` (function_evaluator.py). A formatted
string result is always truthy, so a string-returning RougeScore could never
be flagged as a failure — even at ROUGE 0.0.
"""


def _calculate_rouge():
    from agentic_eval.core_evals.fi_evals.function.functions import calculate_rouge

    return calculate_rouge


def test_rouge_result_is_numeric():
    calculate_rouge = _calculate_rouge()
    out = calculate_rouge("the cat sat on the mat", "the cat sat on the mat")
    assert isinstance(out["result"], float)
    assert out["result"] == 1.0


def test_rouge_zero_score_is_falsy_so_failure_can_fire():
    calculate_rouge = _calculate_rouge()
    out = calculate_rouge("completely different", "xyz qrs tuv")
    assert isinstance(out["result"], float)
    assert out["result"] == 0.0
    # The whole point: `not result` must be True for a zero score so the
    # framework's is_failure() can flag it. A "0.000" string would be truthy.
    assert not out["result"]


def test_rouge_partial_score_is_float_and_bounded():
    calculate_rouge = _calculate_rouge()
    out = calculate_rouge("the cat sat on the mat", "the cat is on a mat")
    assert isinstance(out["result"], float)
    assert 0.0 < out["result"] < 1.0
