"""Contract tests for the YAML and SDK word-information metrics."""

from __future__ import annotations

import builtins
import random
from functools import cache
from pathlib import Path

import Levenshtein
import pytest
import yaml
from django.conf import settings

from agentic_eval.core_evals.fi_evals.function.functions import (
    _align_counts,
    calculate_word_info_lost,
    calculate_word_info_preserved,
    custom_code_eval,
)
from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.management.commands.seed_system_evals import load_yaml_evals

FUNCTION_DIR = Path(__file__).resolve().parent.parent / "function"


@cache
def _load_eval(name: str):
    """Load one YAML body the same way the system-eval tests do."""
    path = FUNCTION_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    namespace: dict = {}
    builtins.exec(compile(code, str(path), "exec"), namespace)
    return namespace["evaluate"]


def _yaml_score(name: str, hypothesis: str, reference: str) -> float:
    return _load_eval(name)(None, hypothesis, reference, None)["score"]


def _yaml_kwargs_score(name: str, **kwargs) -> float:
    return _load_eval(name)(None, None, None, None, **kwargs)["score"]


def _opcode_counts(
    reference: list[str], hypothesis: list[str]
) -> tuple[int, int, int, int]:
    """Count the public Levenshtein opcode stream for test expectations."""
    hits = substitutions = deletions = insertions = 0
    for operation, ref_start, ref_end, hyp_start, hyp_end in Levenshtein.opcodes(
        reference, hypothesis
    ):
        ref_count = ref_end - ref_start
        hyp_count = hyp_end - hyp_start
        if operation == "equal":
            hits += ref_count
        elif operation == "replace":
            substitutions += min(ref_count, hyp_count)
            deletions += max(ref_count - hyp_count, 0)
            insertions += max(hyp_count - ref_count, 0)
        elif operation == "delete":
            deletions += ref_count
        elif operation == "insert":
            insertions += hyp_count
    return hits, substitutions, deletions, insertions


def _expected_score(reference: list[str], hypothesis: list[str], hits: int) -> float:
    if not reference and not hypothesis:
        return 1.0
    if not reference or not hypothesis:
        return 0.0
    return (hits / len(reference)) * (hits / len(hypothesis))


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [
        ("the cat sat", "the cat sat", 1.0),
        ("the cat sat", "the cat sat on the mat", 0.5),
        ("the cat sat", "the cat", 2 / 3),
        ("a b", "a c", 0.25),
        ("the cat sat down", "the big cat sat", 0.5625),
        ("a b", "c d", 0.0),
        ("a b a", "a a b", 4 / 9),
        ("", "", 1.0),
        ("", "extra words", 0.0),
        ("  A\n B  ", "a b", 1.0),
    ],
)
def test_yaml_and_sdk_scores_match_contract(reference, hypothesis, expected):
    """Both supported implementations must expose the same score."""
    assert _yaml_score("word_info_preserved", hypothesis, reference) == pytest.approx(
        expected
    )
    assert _yaml_score("word_info_lost", hypothesis, reference) == pytest.approx(
        expected
    )
    assert calculate_word_info_preserved(reference, hypothesis)[
        "result"
    ] == pytest.approx(expected)
    assert calculate_word_info_lost(reference, hypothesis)["result"] == pytest.approx(
        expected
    )


def test_system_eval_seeder_loads_both_word_information_templates():
    evals = {definition["name"]: definition for definition in load_yaml_evals()}

    for name, eval_id in (("word_info_lost", 190), ("word_info_preserved", 191)):
        definition = evals[name]
        assert definition["eval_id"] == eval_id
        assert definition["_track"] == "function"
        assert definition["config"]["eval_type_id"] == "CustomCodeEval"
        assert definition["config"]["required_keys"] == ["reference", "hypothesis"]


@pytest.mark.parametrize("name", ["word_info_lost", "word_info_preserved"])
def test_none_inputs_are_treated_as_empty(name):
    assert _yaml_kwargs_score(name, reference=None, hypothesis=None) == 1.0
    assert _yaml_kwargs_score(name, reference=None, hypothesis="word") == 0.0
    assert _yaml_kwargs_score(name, reference="word", hypothesis=None) == 0.0
    assert calculate_word_info_lost(None, None)["result"] == 1.0
    assert calculate_word_info_preserved(None, None)["result"] == 1.0
    assert calculate_word_info_lost(None, "word")["result"] == 0.0
    assert calculate_word_info_preserved("word", None)["result"] == 0.0


def test_yaml_bodies_run_through_the_local_code_eval_sandbox(monkeypatch):
    """Exercise the production code-eval path, not only direct ``exec``."""
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings, "CODE_EXECUTOR_LOCAL_FALLBACK", True)
    monkeypatch.setattr(settings, "CLOUD_DEPLOYMENT", "")

    for name in ("word_info_lost", "word_info_preserved"):
        path = FUNCTION_DIR / f"{name}.yaml"
        code = yaml.safe_load(path.read_text())["config"]["code"]
        result = custom_code_eval(
            code,
            reference="the cat sat",
            hypothesis="the cat sat on the mat",
        )
        assert result["result"] == pytest.approx(0.5)
        empty_result = custom_code_eval(code, reference=None, hypothesis=None)
        assert empty_result["result"] == pytest.approx(1.0)


def test_alignment_matches_levenshtein_opcode_contract():
    rng = random.Random(3228)
    vocabulary = ("a", "b", "c")

    for _ in range(500):
        reference = [rng.choice(vocabulary) for _ in range(rng.randrange(8))]
        hypothesis = [rng.choice(vocabulary) for _ in range(rng.randrange(8))]
        expected_counts = _opcode_counts(reference, hypothesis)
        assert _align_counts(reference, hypothesis) == expected_counts

        reference_text = " ".join(reference)
        hypothesis_text = " ".join(hypothesis)
        expected_score = _expected_score(reference, hypothesis, expected_counts[0])
        assert _yaml_score(
            "word_info_preserved", hypothesis_text, reference_text
        ) == pytest.approx(expected_score)
        assert _yaml_score(
            "word_info_lost", hypothesis_text, reference_text
        ) == pytest.approx(expected_score)
        assert calculate_word_info_preserved(reference_text, hypothesis_text)[
            "result"
        ] == pytest.approx(expected_score)
        assert calculate_word_info_lost(reference_text, hypothesis_text)[
            "result"
        ] == pytest.approx(expected_score)


def test_long_transcripts_use_the_compact_alignment_path():
    reference = [f"reference-{index}" for index in range(2_000)]
    hypothesis = reference + [f"insertion-{index}" for index in range(1_000)]
    reference_text = " ".join(reference)
    hypothesis_text = " ".join(hypothesis)
    expected_score = 2 / 3

    assert _align_counts(reference, hypothesis) == (2_000, 0, 0, 1_000)
    assert _yaml_score(
        "word_info_preserved", hypothesis_text, reference_text
    ) == pytest.approx(expected_score)
    assert _yaml_score(
        "word_info_lost", hypothesis_text, reference_text
    ) == pytest.approx(expected_score)
    assert calculate_word_info_preserved(reference_text, hypothesis_text)[
        "result"
    ] == pytest.approx(expected_score)
    assert calculate_word_info_lost(reference_text, hypothesis_text)[
        "result"
    ] == pytest.approx(expected_score)
