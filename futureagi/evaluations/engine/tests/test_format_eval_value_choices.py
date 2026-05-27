"""Tests for ``format_eval_value`` choices aggregation.

Multi-pick choices evals aggregate per-label scores via mean — matching the
convention in ``tfc/utils/functions._calculate_numeric_choices_average``.
Single-pick keeps the legacy behaviour of using the first label's score.
"""

from __future__ import annotations

from types import SimpleNamespace

from evaluations.engine.formatting import format_eval_value


def _template(choice_scores, multi_choice=False, output="choices"):
    """Minimal eval-template stand-in for the function under test."""
    return SimpleNamespace(
        choice_scores=choice_scores,
        multi_choice=multi_choice,
        choices=list(choice_scores.keys()) if choice_scores else [],
        config={"eval_type_id": "AgentEvaluator", "output": output},
    )


def _result(data, output="choices"):
    return {"data": data, "failure": False, "metrics": [], "output": output}


class TestSingleChoice:
    def test_single_string_result_returns_dict_with_score(self):
        tmpl = _template({"High": 1.0, "Medium": 0.5, "Low": 0.0})
        out = format_eval_value(_result({"result": "Medium"}), tmpl)
        assert out == {"score": 0.5, "choice": "Medium"}

    def test_unknown_label_falls_back_to_zero(self):
        tmpl = _template({"High": 1.0, "Medium": 0.5, "Low": 0.0})
        out = format_eval_value(_result({"result": "Catastrophic"}), tmpl)
        assert out["score"] == 0.0
        assert out["choice"] == "Catastrophic"


class TestMultiChoiceMean:
    def test_mean_of_two_labels(self):
        tmpl = _template(
            {"joy": 1.0, "fear": 0.0, "neutral": 0.5}, multi_choice=True
        )
        out = format_eval_value(_result({"result": ["joy", "fear"]}), tmpl)
        # (1.0 + 0.0) / 2 = 0.5
        assert out == {"score": 0.5, "choices": ["joy", "fear"]}

    def test_mean_of_three_labels(self):
        tmpl = _template(
            {"joy": 1.0, "love": 1.0, "neutral": 0.5, "fear": 0.0},
            multi_choice=True,
        )
        out = format_eval_value(
            _result({"result": ["joy", "love", "neutral"]}), tmpl
        )
        # (1.0 + 1.0 + 0.5) / 3 ≈ 0.833
        assert abs(out["score"] - (2.5 / 3)) < 1e-9
        assert out["choices"] == ["joy", "love", "neutral"]

    def test_unknown_labels_skipped_from_mean(self):
        tmpl = _template(
            {"joy": 1.0, "fear": 0.0}, multi_choice=True
        )
        out = format_eval_value(
            _result({"result": ["joy", "mystery", "fear"]}), tmpl
        )
        # mystery is unknown → skipped. Mean of joy + fear = 0.5
        assert out["score"] == 0.5

    def test_all_unknown_returns_zero(self):
        tmpl = _template({"joy": 1.0}, multi_choice=True)
        out = format_eval_value(
            _result({"result": ["alpha", "beta"]}), tmpl
        )
        assert out["score"] == 0.0
        assert out["choices"] == ["alpha", "beta"]


class TestSingleChoiceLegacyFallback:
    """When multi_choice is False but the result is a list (e.g. model
    misbehaved), the eval keeps the legacy ``first-label`` behaviour so
    no existing template silently changes its score."""

    def test_list_on_single_choice_template_uses_first(self):
        tmpl = _template(
            {"High": 1.0, "Medium": 0.5, "Low": 0.0}, multi_choice=False
        )
        out = format_eval_value(
            _result({"result": ["High", "Low"]}), tmpl
        )
        # Legacy: first only. Mean would have been 0.5; first is 1.0.
        assert out["score"] == 1.0
        assert out["choices"] == ["High", "Low"]


class TestNoChoiceScores:
    """Pure-choices evals without an explicit score mapping return labels
    only — no dict, no score. Same as today's behaviour."""

    def test_bare_string_passes_through(self):
        tmpl = _template({})  # empty/falsy choice_scores
        out = format_eval_value(_result({"result": "love"}), tmpl)
        assert out == "love"

    def test_bare_list_passes_through(self):
        tmpl = _template(None, multi_choice=True)
        out = format_eval_value(
            _result({"result": ["joy", "fear"]}), tmpl
        )
        assert out == ["joy", "fear"]
