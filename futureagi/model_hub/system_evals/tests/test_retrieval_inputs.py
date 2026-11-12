"""Regression tests for single-item inputs in retrieval system eval bodies."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
import yaml

RETRIEVAL_EVALS = (
    "recall_score",
    "recall_at_k",
    "precision_at_k",
    "mrr",
    "hit_rate",
    "ndcg_at_k",
)
CONTEXT_EVALS = ("non_llm_context_precision", "non_llm_context_recall")
YAML_DIR = Path(__file__).resolve().parent.parent / "function"


def _load_eval(name):
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    namespace: dict = {}
    vars(builtins)["exec"](compile(code, str(path), "exec"), namespace)
    return namespace["evaluate"]


@pytest.fixture(params=RETRIEVAL_EVALS + CONTEXT_EVALS)
def evaluator(request):
    return request.param, _load_eval(request.param)


@pytest.mark.parametrize(
    ("scalar", "items"),
    [
        (42, ["42"]),
        ("42", ["42"]),
        (4.2, ["4.2"]),
        ("4.2", ["4.2"]),
        (0, ["0"]),
        ("0", ["0"]),
        (True, ["True"]),
        ("true", ["True"]),
        (False, ["False"]),
        ("false", ["False"]),
        ('"doc1"', ["doc1"]),
        ("doc1", ["doc1"]),
    ],
)
@pytest.mark.parametrize("scalar_side", ["reference", "hypothesis"])
def test_single_item_matches_list(evaluator, scalar, items, scalar_side):
    _, evaluate = evaluator
    hypothesis, reference = (items, scalar) if scalar_side == "reference" else (scalar, items)
    assert evaluate(None, hypothesis, reference, None)["score"] == 1.0


def test_quoted_strings_are_not_character_sets(evaluator):
    _, evaluate = evaluator
    assert evaluate(None, '"abc"', '"cab"', None)["score"] == 0.0


@pytest.mark.parametrize("reference", [42, "42"])
def test_single_reference_with_two_retrieved_items(evaluator, reference):
    name, evaluate = evaluator
    expected = 0.5 if name in ("precision_at_k", "non_llm_context_precision") else 1.0
    assert evaluate(None, '["42", "7"]', reference, None)["score"] == expected


@pytest.mark.parametrize("eval_name", RETRIEVAL_EVALS)
@pytest.mark.parametrize("scalar", ["'doc1'", '"doc1"'])
@pytest.mark.parametrize("scalar_side", ["reference", "hypothesis"])
def test_single_item_via_required_keys(eval_name, scalar, scalar_side):
    evaluate = _load_eval(eval_name)
    inputs = {"reference": ["doc1"], "hypothesis": ["doc1"]}
    inputs[scalar_side] = scalar
    assert evaluate(None, None, None, None, **inputs)["score"] == 1.0


@pytest.mark.parametrize("eval_name", ["recall_at_k", "precision_at_k", "ndcg_at_k"])
def test_single_reference_respects_k(eval_name):
    evaluate = _load_eval(eval_name)
    assert evaluate(None, '["7", "42"]', "42", None, k=1)["score"] == 0.0
    expected = {"recall_at_k": 1.0, "precision_at_k": 0.5, "ndcg_at_k": 0.6309297535714575}
    assert evaluate(None, '["7", "42"]', "42", None, k=2)["score"] == pytest.approx(
        expected[eval_name]
    )


@pytest.mark.parametrize("items", [["doc1"], ("doc1",), {"doc1"}, '["doc1"]'])
def test_collection_inputs_still_match(evaluator, items):
    _, evaluate = evaluator
    assert evaluate(None, items, ["doc1"], None)["score"] == 1.0
    assert evaluate(None, ["doc1"], items, None)["score"] == 1.0


@pytest.mark.parametrize("eval_name", RETRIEVAL_EVALS)
@pytest.mark.parametrize("items", ["['doc1']", "('doc1',)", "{'doc1'}"])
def test_python_literal_collections_still_match(eval_name, items):
    evaluate = _load_eval(eval_name)
    assert evaluate(None, items, ["doc1"], None)["score"] == 1.0
    assert evaluate(None, ["doc1"], items, None)["score"] == 1.0


@pytest.mark.parametrize("empty", [None, "null", [], "[]"])
def test_empty_inputs_are_not_document_ids(evaluator, empty):
    name, evaluate = evaluator
    expected = 1.0 if name == "non_llm_context_recall" else 0.0
    assert evaluate(None, empty, empty, None)["score"] == expected
    assert evaluate(None, empty, ["doc1"], None)["score"] == 0.0
    assert evaluate(None, ["doc1"], empty, None)["score"] == 0.0


@pytest.mark.parametrize("eval_name", RETRIEVAL_EVALS)
@pytest.mark.parametrize("empty", ["None", "()", "set()"])
def test_empty_python_literals_are_not_document_ids(eval_name, empty):
    evaluate = _load_eval(eval_name)
    assert evaluate(None, empty, ["doc1"], None)["score"] == 0.0
    assert evaluate(None, ["doc1"], empty, None)["score"] == 0.0


def test_json_object_key_matching_is_preserved(evaluator):
    # Object handling is separate from the scalar fix: keep the existing keys.
    _, evaluate = evaluator
    assert evaluate(None, ["doc1"], '{"doc1": "metadata"}', None)["score"] == 1.0


@pytest.mark.parametrize("eval_name", RETRIEVAL_EVALS)
def test_native_object_key_matching_is_preserved(eval_name):
    evaluate = _load_eval(eval_name)
    assert evaluate(None, {"doc1": "metadata"}, ["doc1"], None)["score"] == 1.0
    assert evaluate(None, ["doc1"], {"doc1": "metadata"}, None)["score"] == 1.0


@pytest.mark.parametrize("eval_name", CONTEXT_EVALS)
def test_native_context_objects_stay_single_items(eval_name):
    evaluate = _load_eval(eval_name)
    item = {"doc1": "metadata"}
    assert evaluate(None, item, [item], None)["score"] == 1.0
    assert evaluate(None, [item], item, None)["score"] == 1.0


@pytest.mark.parametrize("eval_name", CONTEXT_EVALS)
def test_plain_contexts_keep_newline_and_whitespace_handling(eval_name):
    evaluate = _load_eval(eval_name)
    assert evaluate(None, " DOC1 \n\n doc2 ", '["doc1", "DOC2"]', None)["score"] == 1.0
    assert evaluate(None, '["doc1", "DOC2"]', " DOC1 \n\n doc2 ", None)["score"] == 1.0
