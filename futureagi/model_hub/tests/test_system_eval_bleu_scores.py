"""BLEU system evals must not zero out short hypotheses.

The previous implementations always averaged over n-gram orders 1..4 and
substituted ``1e-9`` for orders the hypothesis was too short to form, so an
exact single-word match scored ``~2e-07`` (bleu_score) and an identical
two-token snippet scored ``0.30`` (code_bleu) — every short answer failed the
default 0.5 pass threshold even when byte-identical to the reference.
"""

from pathlib import Path

import yaml

SYSTEM_EVALS_DIR = Path(__file__).resolve().parents[1] / "system_evals" / "function"


def _load_evaluate(yaml_name):
    definition = yaml.safe_load(
        (SYSTEM_EVALS_DIR / yaml_name).read_text(encoding="utf-8")
    )
    namespace = {}
    exec(definition["config"]["code"], namespace)  # noqa: S102 — repo-owned YAML
    return namespace["evaluate"]


class TestBleuScore:
    def _score(self, hypothesis, reference):
        evaluate = _load_evaluate("bleu_score.yaml")
        return evaluate(None, hypothesis, reference, None)["score"]

    def test_exact_single_word_match_scores_one(self):
        assert self._score("Paris", "Paris") == 1.0

    def test_exact_short_sentence_scores_one(self):
        assert self._score("the cat sat", "the cat sat") == 1.0

    def test_exact_long_sentence_still_scores_one(self):
        text = "the quick brown fox jumps over the lazy dog near the river bank"
        assert self._score(text, text) == 1.0

    def test_disjoint_single_word_fails_threshold(self):
        assert self._score("London", "Paris") < 0.5

    def test_disjoint_sentences_score_low(self):
        score = self._score("dogs bark loudly at night", "the cat sat on the mat")
        assert score < 0.5

    def test_partial_overlap_scores_between_wrong_and_exact(self):
        partial = self._score("the cat sat on a rug", "the cat sat on the mat")
        wrong = self._score("dogs bark loudly at night", "the cat sat on the mat")
        assert wrong < partial < 1.0

    def test_brevity_penalty_still_applies(self):
        truncated = self._score("the cat sat", "the cat sat on the mat")
        assert truncated < 1.0
        assert truncated > 0.0

    def test_empty_hypothesis_scores_zero(self):
        assert self._score("", "the cat sat") == 0.0


class TestCodeBleu:
    def _score(self, hypothesis, reference):
        evaluate = _load_evaluate("code_bleu.yaml")
        return evaluate(None, hypothesis, reference, None)["score"]

    def test_identical_short_snippet_scores_one(self):
        assert self._score("return x", "return x") == 1.0

    def test_identical_single_keyword_scores_one(self):
        assert self._score("return", "return") == 1.0

    def test_identical_long_snippet_still_scores_one(self):
        snippet = "def add(a, b): return a + b if a and b else None"
        assert self._score(snippet, snippet) == 1.0

    def test_disjoint_snippets_score_low(self):
        score = self._score("SELECT * FROM users", "def add(a, b): return a + b")
        assert score < 0.5

    def test_keyword_component_still_contributes(self):
        # Same keywords, different identifiers: keyword score is 1.0, BLEU
        # partial — combined score must stay above the pure-BLEU component.
        score = self._score("def sub(x, y): return x - y", "def add(a, b): return a + b")
        assert 0.0 < score < 1.0
