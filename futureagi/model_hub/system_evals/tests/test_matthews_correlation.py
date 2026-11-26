"""Golden tests for matthews_correlation; expected values are from sklearn."""

import builtins
from pathlib import Path

import pytest
import yaml

YAML_PATH = (
    Path(__file__).resolve().parent.parent / "function" / "matthews_correlation.yaml"
)


def _mcc(output, expected):
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    ns: dict = {}
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), ns)
    return ns["evaluate"](None, output, expected, None)["score"] * 2 - 1


def test_multiclass_matches_sklearn():
    assert _mcc(["a", "a", "a", "b", "c"], ["a", "b", "c", "b", "c"]) == pytest.approx(
        0.534522, abs=1e-6
    )


def test_multiclass_with_repeated_labels_matches_sklearn():
    assert _mcc(["x", "y", "z", "x"], ["x", "y", "z", "y"]) == pytest.approx(0.7)


def test_perfect_multiclass_prediction_is_one():
    assert _mcc(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)


def test_constant_multiclass_prediction_is_zero():
    assert _mcc(["a", "a", "a"], ["a", "b", "c"]) == pytest.approx(0.0)


def test_binary_branch_unchanged():
    assert _mcc(["1", "1", "0", "0"], ["1", "0", "1", "0"]) == pytest.approx(0.0)
