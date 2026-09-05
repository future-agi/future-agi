"""
Golden tests for spearman_correlation.

Loads the YAML, materializes the embedded code body in an isolated namespace,
then exercises the evaluator directly -- the same approach as test_validators.py.

Spearman's rho is Pearson's r over the ranks. The 1 - 6*sum(d^2)/(n*(n^2-1))
shortcut only holds when both rank vectors are permutations of 1..n, which ties
break. Expected values below were cross-checked against scipy.stats.spearmanr;
scipy is not imported here so the tests carry no extra dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

YAML_DIR = Path(__file__).resolve().parent.parent / "function"


def _load_eval(name: str):
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    ns: dict = {}
    runner = __builtins__["exec"] if isinstance(__builtins__, dict) else getattr(__builtins__, "exec")
    runner(compile(code, str(path), "exec"), ns)
    return ns["evaluate"]


def _rho(a, b):
    """Return rho, undoing the [-1,1] -> [0,1] normalization applied to score."""
    ev = _load_eval("spearman_correlation")
    return ev(None, a, b, None)["score"] * 2 - 1


@pytest.mark.parametrize(
    "x, y, expected_rho",
    [
        # scipy.stats.spearmanr values.
        ("1,2,2,3", "1,2,3,4", 0.9486832980505139),
        ("1,1,2,2", "1,2,3,4", 0.8944271909999159),
        ("1,2,2,4", "4,3,3,1", -1.0),
        ("1,1,2,2,3,3", "1,1,2,2,3,3", 1.0),
    ],
)
def test_tied_ranks_use_pearson_over_ranks(x, y, expected_rho):
    # The d^2 shortcut assumes untied ranks. With midranks it drifts: the first
    # case previously reported 0.9500 rather than 0.9487.
    assert _rho(x, y) == pytest.approx(expected_rho, abs=1e-9)


def test_constant_input_is_undefined_not_perfect():
    # Every d is zero when a vector is constant, so the shortcut reported a
    # perfect rho=1.0 for an input whose correlation is undefined.
    ev = _load_eval("spearman_correlation")
    for x, y in (("1,1,1", "5,5,5"), ("1,1,1,1", "4,3,2,1"), ("1,2,3,4", "7,7,7,7")):
        r = ev(None, x, y, None)
        assert r["score"] == 0.5, f"{x} vs {y} must not report a correlation, got {r}"
        assert "variance" in r["reason"].lower()


@pytest.mark.parametrize(
    "x, y, expected_rho",
    [
        ("1,2,3,4", "1,2,3,4", 1.0),
        ("1,2,3,4", "4,3,2,1", -1.0),
        ("1,2,3", "1,3,2", 0.5),
    ],
)
def test_untied_cases_unchanged(x, y, expected_rho):
    # Without ties the shortcut and Pearson-over-ranks agree, so these must not move.
    assert _rho(x, y) == pytest.approx(expected_rho, abs=1e-9)


def test_score_stays_in_range():
    ev = _load_eval("spearman_correlation")
    for x, y in (("1,2,2,3", "1,2,3,4"), ("1,2,3,4", "4,3,2,1"), ("1,1,1", "1,2,3")):
        s = ev(None, x, y, None)["score"]
        assert 0.0 <= s <= 1.0


def test_insufficient_or_mismatched_input_still_rejected():
    ev = _load_eval("spearman_correlation")
    assert ev(None, "1", "1", None)["score"] == 0.0
    assert ev(None, "1,2,3", "1,2", None)["score"] == 0.0
