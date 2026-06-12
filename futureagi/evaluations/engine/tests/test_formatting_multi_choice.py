"""Tests for choices score formatting in ``format_eval_value``.

Pins the multi-choice score formula: when the LLM returns a list of picks
and ``choice_scores`` are declared, the formatted score is the mean of
the per-pick scores (unknown labels skipped). This must match the
pass/fail formula in ``compute_choices_failure`` so the displayed score
and the failure verdict are derived from the same numbers.
"""

from types import SimpleNamespace

from evaluations.engine.formatting import format_eval_value


def _template(choice_scores, choices=None):
    return SimpleNamespace(
        config={"output": "choices"},
        choice_scores=choice_scores,
        choices=choices or list(choice_scores.keys()),
        multi_choice=True,
    )


CHOICE_SCORES = {"Love": 1.0, "Anger": 0.0, "Sadness": 0.0, "Neutral": 0.5}


def _result(picks):
    return {"data": picks, "output": "choices"}


def test_multi_choice_score_is_mean_of_picks():
    value = format_eval_value(_result(["Love", "Anger", "Sadness"]), _template(CHOICE_SCORES))
    assert value["choices"] == ["Love", "Anger", "Sadness"]
    assert value["score"] == (1.0 + 0.0 + 0.0) / 3


def test_multi_choice_score_all_pass_labels_is_one():
    value = format_eval_value(_result(["Love"]), _template(CHOICE_SCORES))
    assert value["score"] == 1.0
    assert value["choices"] == ["Love"]


def test_multi_choice_score_unknown_label_is_skipped():
    value = format_eval_value(_result(["Love", "Foo"]), _template(CHOICE_SCORES))
    # "Foo" not in choice_scores → skipped; mean over remaining = 1.0
    assert value["score"] == 1.0


def test_multi_choice_score_all_unknown_is_zero():
    value = format_eval_value(_result(["Foo", "Bar"]), _template(CHOICE_SCORES))
    assert value["score"] == 0.0
    assert value["choices"] == ["Foo", "Bar"]


def test_multi_choice_score_case_insensitive():
    value = format_eval_value(_result(["love", "anger"]), _template(CHOICE_SCORES))
    assert value["score"] == 0.5


def test_single_choice_string_path_unchanged():
    value = format_eval_value(_result("Love"), _template(CHOICE_SCORES))
    assert value == {"score": 1.0, "choice": "Love"}
