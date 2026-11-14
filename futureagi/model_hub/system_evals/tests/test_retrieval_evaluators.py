"""
Golden tests for mean_average_precision (YAML and SDK).

Loads the YAML, materializes the embedded code body in an isolated namespace,
then exercises the evaluator directly -- the same approach as test_validators.py.
The SDK function runs through the same cases.

Average precision is bounded on [0, 1]. Binary relevance credits each relevant
item at most once, so re-retrieving one must not add a hit.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_mean_average_precision,
)

YAML_DIR = Path(__file__).resolve().parent.parent / "function"


def _load_eval(name: str):
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    ns: dict = {}
    vars(builtins)["exec"](compile(code, str(path), "exec"), ns)
    return ns["evaluate"]


def _yaml_score(hypothesis, reference):
    ev = _load_eval("mean_average_precision")
    return ev(None, json.dumps(hypothesis), json.dumps(reference), None)["score"]


def _sdk_score(hypothesis, reference):
    return calculate_mean_average_precision(
        json.dumps(reference), json.dumps(hypothesis)
    )["result"]


SCORERS = pytest.mark.parametrize(
    "score", [_yaml_score, _sdk_score], ids=["yaml", "sdk"]
)


@SCORERS
@pytest.mark.parametrize(
    "hypothesis, reference",
    [
        (["a", "a"], ["a"]),
        (["a", "a", "a"], ["a"]),
        (["a", "b", "a", "b"], ["a", "b"]),
        (["a"] * 10, ["a"]),
    ],
)
def test_duplicate_retrievals_cannot_push_ap_above_one(score, hypothesis, reference):
    # Each repeat previously incremented `hits` while the denominator stayed at
    # len(set(reference)), so ret=["a","a"] over ref=["a"] scored 2.0.
    s = score(hypothesis, reference)
    assert 0.0 <= s <= 1.0, f"AP out of range for {hypothesis} vs {reference}: {s}"


@SCORERS
def test_repeated_perfect_retrieval_scores_one_not_more(score):
    assert score(["a", "a"], ["a"]) == 1.0


@SCORERS
def test_clean_perfect_retrieval_unchanged(score):
    assert score(["a", "b"], ["a", "b"]) == 1.0


@SCORERS
def test_partial_retrieval_unchanged(score):
    # hits at ranks 1 and 3 -> (1/1 + 2/3) / 2
    assert score(["a", "x", "b"], ["a", "b"]) == pytest.approx(5 / 6)


@SCORERS
def test_duplicate_does_not_improve_a_later_hit(score):
    # The duplicate at rank 2 must not raise the precision credited to "b".
    with_dup = score(["a", "a", "b"], ["a", "b"])
    without = score(["a", "x", "b"], ["a", "b"])
    assert with_dup == pytest.approx(without)


@SCORERS
def test_no_relevant_items_scores_zero(score):
    assert score(["x"], ["a"]) == 0.0


@SCORERS
def test_empty_reference_scores_zero(score):
    assert score(["a"], []) == 0.0


def test_sdk_multi_query_duplicates_stay_within_one():
    # The SDK also takes one list per query; each query's AP must stay on [0, 1].
    r = calculate_mean_average_precision(
        json.dumps([["a"], ["a", "b"]]), json.dumps([["a", "a"], ["a", "x", "b"]])
    )
    assert r["result"] == pytest.approx((1.0 + 5 / 6) / 2)
