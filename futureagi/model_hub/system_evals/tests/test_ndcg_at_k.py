"""Golden tests for ndcg_at_k, loaded the same way as test_validators.py."""

from __future__ import annotations

import builtins
import json
import math
from pathlib import Path

import pytest
import yaml

YAML_PATH = Path(__file__).resolve().parent.parent / "function" / "ndcg_at_k.yaml"


def _ndcg(hypothesis, reference, **kwargs):
    code = yaml.safe_load(YAML_PATH.read_text())["config"]["code"]
    ns: dict = {}
    vars(builtins)["exec"](compile(code, str(YAML_PATH), "exec"), ns)
    hyp, ref = json.dumps(hypothesis), json.dumps(reference)
    return ns["evaluate"](None, hyp, ref, None, **kwargs)["score"]


def test_repeated_relevant_item_does_not_push_ndcg_above_one():
    # Used to score 1.631: every copy added DCG, IDCG counts distinct items.
    assert _ndcg(["a", "a"], ["a"]) == 1.0
    assert _ndcg(["a"] * 10, ["a"]) == 1.0
    assert _ndcg(["a", "b", "a", "b"], ["a", "b"]) == 1.0


def test_repeat_scores_like_a_miss_at_that_rank():
    assert _ndcg(["a", "a", "b"], ["a", "b"]) == _ndcg(["a", "x", "b"], ["a", "b"])


def test_partial_retrieval_unchanged():
    expected = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert _ndcg(["a", "x", "b"], ["a", "b"]) == pytest.approx(expected)


def test_k_cutoff_still_applies():
    assert _ndcg(["x", "a"], ["a"], k=1) == 0.0
