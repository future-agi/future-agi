"""
Golden tests for mean_average_precision.

Loads the YAML, materializes the embedded code body in an isolated namespace,
then exercises the evaluator directly -- the same approach as test_validators.py.

Average precision is bounded on [0, 1]. Binary relevance credits each relevant
item at most once, so re-retrieving one must not add a hit.
"""

from __future__ import annotations

import json
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


def _map(hypothesis, reference):
    ev = _load_eval("mean_average_precision")
    return ev(None, json.dumps(hypothesis), json.dumps(reference), None)


@pytest.mark.parametrize(
    "hypothesis, reference",
    [
        (["a", "a"], ["a"]),
        (["a", "a", "a"], ["a"]),
        (["a", "b", "a", "b"], ["a", "b"]),
        (["a"] * 10, ["a"]),
    ],
)
def test_duplicate_retrievals_cannot_push_ap_above_one(hypothesis, reference):
    # Each repeat previously incremented `hits` while the denominator stayed at
    # len(set(reference)), so ret=["a","a"] over ref=["a"] scored 2.0.
    r = _map(hypothesis, reference)
    assert 0.0 <= r["score"] <= 1.0, f"AP out of range for {hypothesis} vs {reference}: {r}"


def test_repeated_perfect_retrieval_scores_one_not_more():
    assert _map(["a", "a"], ["a"])["score"] == 1.0


def test_clean_perfect_retrieval_unchanged():
    assert _map(["a", "b"], ["a", "b"])["score"] == 1.0


def test_partial_retrieval_unchanged():
    # hits at ranks 1 and 3 -> (1/1 + 2/3) / 2
    assert _map(["a", "x", "b"], ["a", "b"])["score"] == pytest.approx(5 / 6)


def test_duplicate_does_not_improve_a_later_hit():
    # The duplicate at rank 2 must not raise the precision credited to "b".
    with_dup = _map(["a", "a", "b"], ["a", "b"])["score"]
    without = _map(["a", "x", "b"], ["a", "b"])["score"]
    assert with_dup == pytest.approx(without)


def test_no_relevant_items_scores_zero():
    assert _map(["x"], ["a"])["score"] == 0.0


def test_empty_reference_scores_zero():
    assert _map(["a"], [])["score"] == 0.0
