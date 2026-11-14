"""Python/catalog contracts for lexical diagnostics, not semantic judgments."""

import json
from pathlib import Path

import pytest
import yaml
from django.conf import settings

from agentic_eval.core_evals.fi_evals.function import functions
from agentic_eval.core_evals.fi_evals.function.wrapper import ContextRecallScore
from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    load_yaml_evals,
)
from model_hub.system_evals.render_lexical_scores import render

ROOT = Path(__file__).resolve().parent.parent
NAMES = (
    "context_precision_score",
    "context_recall_score",
    "faithfulness_score",
    "noise_sensitivity",
)


def definition(name):
    return yaml.safe_load((ROOT / "function" / f"{name}.yaml").read_text())


def catalog(name, **kwargs):
    namespace = {}
    exec(compile(definition(name)["config"]["code"], name, "exec"), namespace)
    return namespace["evaluate"](
        kwargs.pop("input", None),
        kwargs.pop("output", None),
        kwargs.pop("expected", None),
        kwargs.pop("context", None),
        **kwargs,
    )


def python_score(name, **kwargs):
    return getattr(functions, f"calculate_{name}")(**kwargs)


@pytest.mark.parametrize("encoding", [list, json.dumps, "\n".join, repr])
def test_default_recall_threshold_parity(encoding):
    kwargs = {
        "hypothesis": encoding(["Berlin is capital"]),
        "reference": encoding(["Paris is capital"]),
    }
    assert python_score("context_recall_score", **kwargs)["result"] == 0.0
    assert catalog("context_recall_score", **kwargs)["score"] == 0.0
    evaluator = ContextRecallScore()
    assert evaluator.to_config()["min_token_overlap"] == 0.7
    assert evaluator._evaluate(**kwargs)["metrics"][0]["value"] == 0.0


@pytest.mark.parametrize("name", NAMES[:2])
@pytest.mark.parametrize("threshold, expected", [(0.6, 1.0), (0.7, 0.0), (1.0, 0.0)])
def test_configured_overlap_threshold_parity(name, threshold, expected):
    kwargs = {
        "hypothesis": ["Berlin is capital"],
        "reference": ["Paris is capital"],
        "min_token_overlap": threshold,
    }
    assert python_score(name, **kwargs)["result"] == expected
    assert catalog(name, **kwargs)["score"] == expected
    schema = definition(name)["config"]["function_params_schema"]
    assert "min_token_overlap" in schema


@pytest.mark.parametrize("name", NAMES[:2])
@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), "bad"])
def test_invalid_overlap_threshold_rejected(name, threshold):
    kwargs = {
        "hypothesis": ["cat"],
        "reference": ["cat"],
        "min_token_overlap": threshold,
    }
    with pytest.raises(ValueError, match="min_token_overlap"):
        python_score(name, **kwargs)
    with pytest.raises(ValueError, match="min_token_overlap"):
        catalog(name, **kwargs)


@pytest.mark.parametrize("name", NAMES[:2])
def test_named_inputs_take_precedence_over_legacy_aliases(name):
    kwargs = {
        "hypothesis": ["cat"],
        "reference": ["cat"],
        "output": ["dog"],
        "expected": ["bird"],
    }
    assert (
        python_score(name, **kwargs)["result"]
        == catalog(name, **kwargs)["score"]
        == 1.0
    )


@pytest.mark.parametrize("name", NAMES[:2])
def test_legacy_aliases_remain_supported(name):
    kwargs = {"output": ["cat"], "expected": ["cat"]}
    assert (
        python_score(name, **kwargs)["result"]
        == catalog(name, **kwargs)["score"]
        == 1.0
    )


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value", [None, "", [], "[]"])
def test_empty_inputs_parity(name, value):
    kwargs = {"output": value, "expected": value, "context": value}
    assert (
        python_score(name, **kwargs)["result"]
        == catalog(name, **kwargs)["score"]
        == 1.0
    )


def test_contradiction_is_explicitly_only_lexical_overlap():
    kwargs = {
        "output": "Paris is not the capital of France.",
        "context": "Paris is the capital of France.",
    }
    for result, key in [
        (python_score("faithfulness_score", **kwargs), "result"),
        (catalog("faithfulness_score", **kwargs), "score"),
    ]:
        # Negation still shares 7/8 tokens: do not pretend this checks entailment.
        assert result[key] == 1.0
        assert "lexical" in result["reason"].lower()
        assert "semantic" in result["reason"].lower()
    desc = definition("faithfulness_score")["description"].lower()
    assert "contradict" in desc
    assert "groundedness" in desc


def test_noise_uses_higher_is_better_score_and_reports_raw_ratio():
    kwargs = {"expected": "cat", "context": "cat dog"}
    for output, expected_score in [("cat", 1.0), ("dog", 0.0), ("cat dog", 0.5)]:
        for result, key in [
            (python_score("noise_sensitivity", output=output, **kwargs), "result"),
            (catalog("noise_sensitivity", output=output, **kwargs), "score"),
        ]:
            assert result[key] == expected_score
            assert "lexical" in result["reason"].lower()
            assert "sensitivity=" in result["reason"]
    assert "expected" in definition("noise_sensitivity")["config"]["required_keys"]


def test_catalog_ids_do_not_replace_existing_evaluators():
    definitions = load_yaml_evals()
    for name in NAMES:
        entry = next(d for d in definitions if d["name"] == name)
        assert sum(d["eval_id"] == entry["eval_id"] for d in definitions) == 1
    assert SYSTEM_EVALS_VERSION > 30


def test_reuse_existing_model_judges():
    names = {d["name"] for d in load_yaml_evals()}
    assert {"groundedness", "context_relevance"} <= names
    assert not {"rag_faithfulness", "rag_context_precision"} & names


@pytest.mark.parametrize("name", NAMES)
def test_catalog_body_is_generated_from_shared_implementation(name):
    assert definition(name)["config"]["code"] == render(name)


@pytest.mark.parametrize(
    "name, kwargs, expected",
    [
        (
            "context_precision_score",
            {"hypothesis": ["dog", "cat"], "reference": ["cat"]},
            0.5,
        ),
        (
            "context_recall_score",
            {"hypothesis": ["Berlin is capital"], "reference": ["Paris is capital"]},
            0.0,
        ),
        (
            "faithfulness_score",
            {
                "output": "Paris is not the capital of France.",
                "context": "Paris is the capital of France.",
            },
            1.0,
        ),
        (
            "noise_sensitivity",
            {"output": "cat dog", "expected": "cat", "context": "cat dog"},
            0.5,
        ),
    ],
)
def test_real_local_sandbox_matches_python(name, kwargs, expected, monkeypatch):
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings, "CODE_EXECUTOR_LOCAL_FALLBACK", True)
    monkeypatch.setattr(settings, "CLOUD_DEPLOYMENT", "")
    result = functions.custom_code_eval(definition(name)["config"]["code"], **kwargs)
    assert result["result"] == expected
    assert python_score(name, **kwargs)["result"] == expected


@pytest.mark.parametrize("k, expected", [(None, 0.5), (1, 0.0), (2, 0.5)])
def test_rank_cutoff_parity(k, expected):
    kwargs = {"hypothesis": ["dog", "cat"], "reference": ["cat"], "k": k}
    assert python_score("context_precision_score", **kwargs)["result"] == expected
    assert catalog("context_precision_score", **kwargs)["score"] == expected
