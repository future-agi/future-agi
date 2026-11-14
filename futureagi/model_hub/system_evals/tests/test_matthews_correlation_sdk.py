"""MCC agrees with sklearn while retaining the evaluator's normalized contract."""

import json
import random

import pytest
from sklearn.metrics import matthews_corrcoef

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_matthews_correlation,
)
from agentic_eval.core_evals.fi_evals.function.wrapper import MatthewsCorrelation

MULTICLASS_CASES = [
    pytest.param(["a"] * 10, ["a"] * 8 + ["b", "c"], id="majority-prediction"),
    pytest.param(["a"] * 8 + ["b", "c"], ["a"] * 10, id="constant-truth"),
    pytest.param(["z"] * 3, ["a", "b", "c"], id="constant-unseen-prediction"),
    pytest.param(
        ["a", "a", "a", "b", "c"],
        ["a", "b", "c", "b", "c"],
        id="unequal-marginals",
    ),
    pytest.param(["x", "y", "z", "x"], ["x", "y", "z", "y"], id="repeated-labels"),
    pytest.param(
        ["a", "b", "c", "a", "a"],
        ["b", "c", "a", "b", "c"],
        id="negative-correlation",
    ),
    pytest.param(["a", "b", "d", "a"], ["a", "b", "c", "b"], id="predicted-only-class"),
]


def _assert_reference(output, expected, result):
    raw = float(matthews_corrcoef(expected, output))
    score = (raw + 1) / 2
    assert result["result"] == pytest.approx(score, abs=1e-12)
    assert result["reason"] == f"MCC: {raw:.4f}, normalized={score:.4f}"
    assert 0.0 <= result["result"] <= 1.0


@pytest.mark.parametrize("output, expected", MULTICLASS_CASES)
@pytest.mark.parametrize("as_json", [False, True], ids=["list", "json"])
def test_multiclass_matches_sklearn(output, expected, as_json):
    result = calculate_matthews_correlation(
        json.dumps(output) if as_json else output,
        json.dumps(expected) if as_json else expected,
    )
    _assert_reference(output, expected, result)


def test_multiclass_reference_for_deterministic_samples():
    rng = random.Random(2672)
    for n_classes in range(3, 8):
        for _ in range(20):
            labels = [str(i) for i in range(n_classes)]
            expected = labels + rng.choices(
                labels, weights=range(1, n_classes + 1), k=50
            )
            output = rng.choices(labels, k=len(expected))
            _assert_reference(
                output, expected, calculate_matthews_correlation(output, expected)
            )


@pytest.mark.parametrize("output, expected", MULTICLASS_CASES)
def test_wrapper_preserves_normalized_metric_and_reason(output, expected):
    result = MatthewsCorrelation()._evaluate(output=output, expected=expected)
    assert result["name"] == "MatthewsCorrelation"
    assert len(result["metrics"]) == 1
    assert result["metrics"][0]["id"] == "passed"
    _assert_reference(
        output,
        expected,
        {"result": result["metrics"][0]["value"], "reason": result["reason"]},
    )


# Compatibility controls: these intentionally continue to pass without the fix.
@pytest.mark.parametrize(
    "output, expected",
    [
        pytest.param(["0", "1"], ["0", "1"], id="binary-perfect"),
        pytest.param(["0", "1"], ["1", "0"], id="binary-inverse"),
        pytest.param(["0", "1", "0", "1"], ["0", "0", "1", "1"], id="binary-zero"),
        pytest.param(
            ["0", "0", "1", "1", "1"], ["0", "1", "1", "1", "1"], id="binary-imbalanced"
        ),
        pytest.param(["0"] * 3, ["0", "1", "1"], id="binary-constant-prediction"),
        pytest.param(["0", "1", "1"], ["0"] * 3, id="binary-constant-truth"),
        pytest.param(["a"] * 3, ["a"] * 3, id="both-constant"),
        pytest.param(["a", "b", "c"], ["a", "b", "c"], id="multiclass-perfect"),
        pytest.param(["a", "b", "c"], ["b", "c", "a"], id="multiclass-balanced"),
    ],
)
def test_unchanged_cases_match_sklearn(output, expected):
    _assert_reference(
        output, expected, calculate_matthews_correlation(output, expected)
    )


@pytest.mark.parametrize(
    "output, expected", [([], []), (None, None), (["a"], ["a", "b"])]
)
def test_invalid_input_contract_unchanged(output, expected):
    assert calculate_matthews_correlation(output, expected) == {
        "result": 0.0,
        "reason": "Invalid input for MCC",
    }
