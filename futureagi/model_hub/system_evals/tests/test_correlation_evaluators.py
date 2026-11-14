"""Golden tests for spearman_correlation (YAML and SDK); expected rho is from scipy."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_spearman_correlation,
)

YAML_PATH = (
    Path(__file__).resolve().parent.parent / "function" / "spearman_correlation.yaml"
)


def _yaml_eval(output, expected):
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    ns: dict = {}
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), ns)
    return ns["evaluate"](None, output, expected, None)


def _yaml_score(output, expected):
    return _yaml_eval(output, expected)["score"]


def _sdk_score(output, expected):
    return calculate_spearman_correlation(output, expected)["result"]


SCORERS = pytest.mark.parametrize(
    "score", [_yaml_score, _sdk_score], ids=["yaml", "sdk"]
)


@SCORERS
@pytest.mark.parametrize(
    "x, y, expected_rho",
    [
        ("1,2,2,3", "1,2,3,4", 0.9486832980505139),
        ("1,1,2,2", "1,2,3,4", 0.8944271909999159),
        ("1,2,2,4", "4,3,3,1", -1.0),
        ("1,1,2,2,3,3", "1,1,2,2,3,3", 1.0),
        ("1,1,2,3", "1,2,2,3", 0.8333333333333335),
        ("1,1,1,2", "1,2,3,4", 0.7745966692414834),
        ("1,1,1,1,2", "5,4,3,2,1", -0.7071067811865475),
        ("2,2,3,3,2", "2,3,2,2,2", -0.408248290463863),
        ("2,1,1,1,1", "1,2,2,2,2", -1.0),
    ],
)
def test_tied_ranks_match_scipy(score, x, y, expected_rho):
    assert score(x, y) == pytest.approx((expected_rho + 1) / 2, abs=1e-9)


@SCORERS
@pytest.mark.parametrize(
    "x, y",
    [
        ("1,1,1", "5,5,5"),
        ("3,3,3,3", "3,3,3,3"),
        ("1,1,1,1", "4,3,2,1"),
        ([0.7] * 5, [1, 2, 3, 4, 5]),
    ],
)
def test_constant_input_scores_as_no_correlation(score, x, y):
    assert score(x, y) == 0.5


def test_constant_input_reason_names_zero_variance():
    assert "variance" in _yaml_eval("1,2,3,4", "7,7,7,7")["reason"].lower()
    sdk = calculate_spearman_correlation("1,2,3,4", "7,7,7,7")
    assert "variance" in sdk["reason"].lower()


@SCORERS
@pytest.mark.parametrize(
    "x, y, expected_rho",
    [
        ("1,2,3,4", "1,2,3,4", 1.0),
        ("1,2,3,4", "4,3,2,1", -1.0),
        ("1,2,3", "1,3,2", 0.5),
        ("[1, 2, 3, 4]", "[1, 4, 9, 16]", 1.0),
    ],
)
def test_untied_cases_unchanged(score, x, y, expected_rho):
    assert score(x, y) == pytest.approx((expected_rho + 1) / 2, abs=1e-9)


@SCORERS
def test_insufficient_or_mismatched_input_still_rejected(score):
    assert score("1", "1") == 0.0
    assert score("1,2,3", "1,2") == 0.0
    assert score([], []) == 0.0
