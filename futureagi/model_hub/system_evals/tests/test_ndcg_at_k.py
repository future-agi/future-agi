"""
Golden tests for ndcg_at_k.

Loads the YAML, materializes the embedded code body in an isolated namespace,
then exercises the evaluator directly -- the same approach as test_validators.py.

NDCG is bounded on [0, 1]. Binary relevance credits each relevant item at most
once, so re-retrieving one must not add gain.
"""

from __future__ import annotations

import builtins
import json
import math
from pathlib import Path

import pytest
import yaml

YAML_DIR = Path(__file__).resolve().parent.parent / "function"


def _load_eval(name: str):
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    ns: dict = {}
    runner = vars(builtins)["exec"]
    runner(compile(code, str(path), "exec"), ns)
    return ns["evaluate"]


def _ndcg(hypothesis, reference, **kwargs):
    ev = _load_eval("ndcg_at_k")
    return ev(None, json.dumps(hypothesis), json.dumps(reference), None, **kwargs)


@pytest.mark.parametrize(
    "hypothesis, reference",
    [
        (["a", "a"], ["a"]),
        (["a", "a", "a"], ["a"]),
        (["a", "b", "a", "b"], ["a", "b"]),
        (["a"] * 10, ["a"]),
    ],
)
def test_duplicate_retrievals_cannot_push_ndcg_above_one(hypothesis, reference):
    # Each repeat previously added gain to DCG while IDCG counts distinct
    # ground-truth items, so hyp=["a","a"] over ref=["a"] scored 1.631.
    r = _ndcg(hypothesis, reference)
    assert 0.0 <= r["score"] <= 1.0, (
        f"NDCG out of range for {hypothesis} vs {reference}: {r}"
    )


def test_repeated_perfect_retrieval_scores_one_not_more():
    assert _ndcg(["a", "a"], ["a"])["score"] == 1.0


def test_duplicate_scores_like_an_irrelevant_item():
    # A repeat at rank 2 earns nothing, exactly like a miss at rank 2.
    with_dup = _ndcg(["a", "a", "b"], ["a", "b"])["score"]
    with_miss = _ndcg(["a", "x", "b"], ["a", "b"])["score"]
    assert with_dup == pytest.approx(with_miss)


def test_clean_perfect_retrieval_unchanged():
    assert _ndcg(["a", "b"], ["a", "b"])["score"] == 1.0


def test_partial_retrieval_unchanged():
    # hits at ranks 1 and 3 -> (1 + 1/log2(4)) / (1 + 1/log2(3))
    expected = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert _ndcg(["a", "x", "b"], ["a", "b"])["score"] == pytest.approx(expected)


def test_k_cutoff_still_applies():
    # Only rank 1 is inside k=1, and it is a miss.
    assert _ndcg(["x", "a"], ["a"], k=1)["score"] == 0.0
