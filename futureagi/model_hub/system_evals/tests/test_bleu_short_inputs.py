"""Regression tests for the BLEU code embedded in the system eval YAMLs."""

import ast
import builtins
import math
from pathlib import Path

import pytest
import yaml

YAML_DIR = Path(__file__).resolve().parent.parent / "function"
SEED_COMMAND = YAML_DIR.parent.parent / "management" / "commands" / "seed_system_evals.py"


@pytest.fixture(params=["bleu_score", "code_bleu"])
def metric(request):
    path = YAML_DIR / f"{request.param}.yaml"
    definition = yaml.safe_load(path.read_text())
    ns: dict = {}
    vars(builtins)["exec"](compile(definition["config"]["code"], str(path), "exec"), ns)
    return request.param, ns["evaluate"], definition["pass_threshold"]


def _expected_score(name, bleu, kw_score=1.0):
    # code_bleu is 0.7 * BLEU + 0.3 * keyword recall; recall is 1 with no keywords.
    return 0.7 * bleu + 0.3 * kw_score if name == "code_bleu" else bleu


@pytest.mark.parametrize(
    "text",
    ["Paris", "return x", "the cat sat", "the cat sat here", "the cat sat here today"],
)
def test_exact_matches_score_one_at_any_length(metric, text):
    _, evaluate, threshold = metric
    result = evaluate(None, text, text, None)
    assert result["score"] == pytest.approx(1.0)
    assert result["score"] >= threshold
    assert isinstance(result["reason"], str)


@pytest.mark.parametrize(
    "hypothesis,reference",
    [
        ("London", "Paris"),
        ("London Berlin", "Paris Rome"),
        ("London Berlin Madrid", "Paris Rome Tokyo"),
        ("London Berlin Madrid Oslo", "Paris Rome Tokyo Dublin"),
    ],
)
def test_disjoint_answers_stay_below_threshold(metric, hypothesis, reference):
    _, evaluate, threshold = metric
    score = evaluate(None, hypothesis, reference, None)["score"]
    assert 0 <= score < threshold


def test_shorter_hypothesis_keeps_brevity_penalty(metric):
    name, evaluate, _ = metric
    score = evaluate(None, "Paris", "Paris is beautiful", None)["score"]
    # The reference keyword "is" is missing from the hypothesis, so kw_score is 0.
    assert score == pytest.approx(_expected_score(name, math.exp(-2), kw_score=0.0))


def test_orders_are_limited_by_hypothesis_not_reference(metric):
    name, evaluate, threshold = metric
    score = evaluate(None, "Paris extra tokens", "Paris", None)["score"]
    # Unigram overlap is 1/3 (+1 smoothing); higher orders have no matches.
    bleu = (0.5 * (0.1 / 2) * 0.1) ** (1 / 3)
    assert score == pytest.approx(_expected_score(name, bleu))
    assert score < threshold


def test_repeated_tokens_still_use_clipped_counts(metric):
    name, evaluate, _ = metric
    score = evaluate(None, "Paris Paris", "Paris", None)["score"]
    assert score == pytest.approx(_expected_score(name, math.sqrt((2 / 3) * 0.1)))


def test_longer_partial_matches_preserve_existing_nonzero_smoothing(metric):
    name, evaluate, _ = metric
    score = evaluate(None, "a b c d e x", "a b c d e f", None)["score"]
    assert score == pytest.approx(_expected_score(name, (3 / 7) ** 0.25))


@pytest.mark.parametrize(
    "hypothesis,reference", [("", "Paris"), ("Paris", ""), (None, None), ("  ", "Paris")]
)
def test_missing_or_empty_hypothesis_still_scores_zero(metric, hypothesis, reference):
    _, evaluate, _ = metric
    assert evaluate(None, hypothesis, reference, None)["score"] == 0.0


def test_explicit_reference_and_hypothesis_overrides(metric):
    _, evaluate, _ = metric
    result = evaluate(
        None, "London", "Berlin", None, hypothesis="Paris", reference="Paris"
    )
    assert result["score"] == pytest.approx(1.0)


def test_seed_version_advances_past_the_broken_templates():
    tree = ast.parse(SEED_COMMAND.read_text())
    version = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SYSTEM_EVALS_VERSION"
            for target in node.targets
        )
    )
    assert version > 28
