"""Tests for ``evaluations.engine.normalize``."""

from __future__ import annotations

import pytest

from evaluations.engine.normalize import (
    AXIS_KEYS,
    extract_choice,
    extract_choices,
    extract_pass,
    extract_score,
    resolve_eval_axes,
)


def test_axis_keys_pinned():
    assert AXIS_KEYS == (
        "output_pass",
        "output_score",
        "output_choice",
        "output_choices",
    )


# ── extract_score ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.7, 0.7),
        (1, 1.0),
        (0, 0.0),
        ({"score": 0.66, "choice": "always"}, 0.66),
        ({"score": 0, "choice": "x"}, 0.0),
    ],
)
def test_extract_score_extracts(value, expected):
    assert extract_score(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value", [None, "0.7", True, False, {"choice": "x"}, {"score": "y"}, ["A"]]
)
def test_extract_score_yields_none(value):
    assert extract_score(value) is None


# ── extract_choice ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("always", "always"),
        ({"score": 1.0, "choice": "always"}, "always"),
        ({"choice": "x"}, "x"),
    ],
)
def test_extract_choice_extracts(value, expected):
    assert extract_choice(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, 0.7, True, {"score": 1.0, "choices": ["A", "B"]}, ["A"]],
)
def test_extract_choice_yields_none(value):
    assert extract_choice(value) is None


# ── extract_choices ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (["A", "B"], ["A", "B"]),
        (["A", "B", "A"], ["A", "B"]),
        ({"score": 0.5, "choices": ["polite", "concise"]}, ["polite", "concise"]),
        ({"choices": ["A", "B", "A"]}, ["A", "B"]),
    ],
)
def test_extract_choices_extracts(value, expected):
    assert extract_choices(value) == expected


@pytest.mark.parametrize(
    "value", [None, "always", {"score": 1.0, "choice": "x"}, [], {"choices": []}]
)
def test_extract_choices_yields_none(value):
    assert extract_choices(value) is None


# ── extract_pass ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("Passed", True), ("Failed", False)],
)
def test_extract_pass_extracts(value, expected):
    assert extract_pass(value) is expected


@pytest.mark.parametrize("value", [None, 0, 0.7, "yes", "no", ["A"]])
def test_extract_pass_yields_none(value):
    assert extract_pass(value) is None


# ── resolve_eval_axes ────────────────────────────────────────────────────


def test_resolve_axes_pass_fail_routes_to_output_pass_only():
    axes = resolve_eval_axes("Passed", "Pass/Fail")
    assert axes == {
        "output_pass": True,
        "output_score": None,
        "output_choice": None,
        "output_choices": None,
    }


def test_resolve_axes_score_plain_float():
    axes = resolve_eval_axes(0.7, "score")
    assert axes["output_score"] == pytest.approx(0.7)


def test_resolve_axes_score_dict_with_choice_scores():
    axes = resolve_eval_axes({"score": 0.66, "choice": "frequently"}, "score")
    assert axes["output_score"] == pytest.approx(0.66)
    assert axes["output_choice"] is None


def test_resolve_axes_numeric_routes_to_output_score():
    axes = resolve_eval_axes(0.42, "numeric")
    assert axes["output_score"] == pytest.approx(0.42)


def test_resolve_axes_choices_single_plain_string():
    axes = resolve_eval_axes("always", "choices", multi_choice=False)
    assert axes["output_choice"] == "always"
    assert axes["output_choices"] is None


def test_resolve_axes_choices_single_dict():
    axes = resolve_eval_axes(
        {"score": 1.0, "choice": "always"}, "choices", multi_choice=False
    )
    assert axes["output_choice"] == "always"
    assert axes["output_score"] is None


def test_resolve_axes_choices_multi_plain_list():
    axes = resolve_eval_axes(["A", "B"], "choices", multi_choice=True)
    assert axes["output_choices"] == ["A", "B"]


def test_resolve_axes_choices_multi_dict_shape():
    axes = resolve_eval_axes(
        {"score": 0.5, "choices": ["polite", "concise"]},
        "choices",
        multi_choice=True,
    )
    assert axes["output_choices"] == ["polite", "concise"]


def test_resolve_axes_reason_yields_all_none():
    assert resolve_eval_axes("free-form text", "reason") == {
        "output_pass": None,
        "output_score": None,
        "output_choice": None,
        "output_choices": None,
    }


def test_resolve_axes_none_value_yields_all_none():
    assert resolve_eval_axes(None, "score") == {
        "output_pass": None,
        "output_score": None,
        "output_choice": None,
        "output_choices": None,
    }


def test_resolve_axes_strict_gating_score_dict_with_choice_does_not_set_choice():
    axes = resolve_eval_axes({"score": 0.7, "choice": "always"}, "score")
    assert axes["output_choice"] is None
    assert axes["output_choices"] is None


def test_resolve_axes_strict_gating_choices_dict_with_score_does_not_set_score():
    axes = resolve_eval_axes(
        {"score": 0.7, "choice": "frequently"}, "choices", multi_choice=False
    )
    assert axes["output_score"] is None
