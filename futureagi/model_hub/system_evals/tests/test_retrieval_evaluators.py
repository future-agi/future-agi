"""Golden tests for the live mean_average_precision system template.

Binary relevance credits each relevant item at most once. The SDK function is
outside this template change and deliberately not used as an oracle.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest
import yaml

YAML_PATH = (
    Path(__file__).resolve().parent.parent / "function/mean_average_precision.yaml"
)


def _yaml_score(hypothesis, reference):
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    namespace: dict = {}
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), namespace)
    return namespace["evaluate"](
        None, json.dumps(hypothesis), json.dumps(reference), None
    )["score"]


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
    score = _yaml_score(hypothesis, reference)
    assert 0.0 <= score <= 1.0


def test_repeated_perfect_retrieval_scores_one_not_more():
    assert _yaml_score(["a", "a"], ["a"]) == 1.0


def test_clean_perfect_retrieval_unchanged():
    assert _yaml_score(["a", "b"], ["a", "b"]) == 1.0


def test_partial_retrieval_unchanged():
    assert _yaml_score(["a", "x", "b"], ["a", "b"]) == pytest.approx(5 / 6)


def test_duplicate_does_not_improve_a_later_hit():
    with_duplicate = _yaml_score(["a", "a", "b"], ["a", "b"])
    without = _yaml_score(["a", "x", "b"], ["a", "b"])
    assert with_duplicate == pytest.approx(without)


def test_no_relevant_items_scores_zero():
    assert _yaml_score(["x"], ["a"]) == 0.0


def test_empty_reference_scores_zero():
    assert _yaml_score(["a"], []) == 0.0


def test_empty_retrieval_scores_zero():
    assert _yaml_score([], ["a"]) == 0.0


def test_duplicate_reference_does_not_change_denominator():
    assert _yaml_score(["a", "b"], ["a", "a", "b"]) == 1.0
