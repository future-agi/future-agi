"""Tests for ``evaluations.engine.normalize``.

Covers the four extractors, the central ``resolve_eval_axes`` dispatch,
``empty_axes`` defaults, ``build_simulate_eval_payload`` payload shape, and
the config-output / multi-choice accessors. Each assertion is keyed on the
**stored** ``config_output`` so the strict gating contract is exercised
directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evaluations.engine.normalize import (
    AXIS_KEYS,
    build_simulate_eval_payload,
    dedupe_preserve_order,
    empty_axes,
    eval_config_multi_choice,
    eval_config_output,
    extract_choice,
    extract_choices,
    extract_pass,
    extract_score,
    resolve_eval_axes,
)


def _custom_eval_config(*, stored_output=None, multi_choice=None):
    config = {"output": stored_output} if stored_output is not None else {}
    template = SimpleNamespace(config=config)
    if multi_choice is not None:
        template.multi_choice = multi_choice
    return SimpleNamespace(eval_template=template)


# ── dedupe_preserve_order ────────────────────────────────────────────────


def test_dedupe_preserves_first_seen_order():
    assert dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_handles_empty():
    assert dedupe_preserve_order([]) == []


# ── AXIS_KEYS + empty_axes ───────────────────────────────────────────────


def test_axis_keys_pinned():
    assert AXIS_KEYS == (
        "output_pass",
        "output_score",
        "output_choice",
        "output_choices",
    )


def test_empty_axes_returns_all_none():
    assert empty_axes() == {
        "output_pass": None,
        "output_score": None,
        "output_choice": None,
        "output_choices": None,
    }


def test_empty_axes_returns_fresh_dict_each_call():
    a = empty_axes()
    a["output_score"] = 1.0
    assert empty_axes()["output_score"] is None


# ── eval_config_output ───────────────────────────────────────────────────


def test_eval_config_output_reads_stored_value():
    assert eval_config_output(_custom_eval_config(stored_output="choices")) == "choices"


def test_eval_config_output_defaults_to_score_when_missing():
    assert eval_config_output(_custom_eval_config()) == "score"


def test_eval_config_output_defaults_when_no_template():
    assert eval_config_output(SimpleNamespace()) == "score"


# ── eval_config_multi_choice ─────────────────────────────────────────────


def test_eval_config_multi_choice_reads_flag():
    assert eval_config_multi_choice(_custom_eval_config(multi_choice=True)) is True
    assert eval_config_multi_choice(_custom_eval_config(multi_choice=False)) is False


def test_eval_config_multi_choice_defaults_false_when_missing():
    assert eval_config_multi_choice(_custom_eval_config()) is False


def test_eval_config_multi_choice_defaults_when_no_template():
    assert eval_config_multi_choice(SimpleNamespace()) is False


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
    "value",
    [
        None,
        "0.7",
        True,
        False,
        {"choice": "always"},
        {"score": "not-a-number"},
        ["A"],
    ],
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
    [
        None,
        0.7,
        True,
        {"score": 1.0, "choices": ["A", "B"]},
        ["A"],
    ],
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
    "value",
    [
        None,
        "always",
        {"score": 1.0, "choice": "always"},
        [],
        {"choices": []},
    ],
)
def test_extract_choices_yields_none_for_unfilterable(value):
    assert extract_choices(value) is None


# ── extract_pass ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("Passed", True),
        ("Failed", False),
    ],
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
    assert axes["output_pass"] is None
    assert axes["output_choice"] is None
    assert axes["output_choices"] is None


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
    assert axes["output_score"] is None


def test_resolve_axes_choices_single_dict():
    axes = resolve_eval_axes(
        {"score": 1.0, "choice": "always"}, "choices", multi_choice=False
    )
    assert axes["output_choice"] == "always"
    assert axes["output_score"] is None


def test_resolve_axes_choices_multi_plain_list():
    axes = resolve_eval_axes(["A", "B"], "choices", multi_choice=True)
    assert axes["output_choices"] == ["A", "B"]
    assert axes["output_choice"] is None


def test_resolve_axes_choices_multi_dict_shape():
    axes = resolve_eval_axes(
        {"score": 0.5, "choices": ["polite", "concise"]},
        "choices",
        multi_choice=True,
    )
    assert axes["output_choices"] == ["polite", "concise"]
    assert axes["output_score"] is None


def test_resolve_axes_reason_yields_all_none():
    assert resolve_eval_axes("free-form text", "reason") == empty_axes()


def test_resolve_axes_none_value_yields_all_none():
    assert resolve_eval_axes(None, "score") == empty_axes()
    assert resolve_eval_axes(None, "choices", multi_choice=True) == empty_axes()


def test_resolve_axes_strict_gating_score_dict_with_choice_does_not_set_choice():
    """A score-typed eval with choice_scores returns a dict containing both
    score and choice. Strict gating routes only output_score, never
    output_choice, regardless of what's in the dict."""
    axes = resolve_eval_axes({"score": 0.7, "choice": "always"}, "score")
    assert axes["output_choice"] is None
    assert axes["output_choices"] is None


def test_resolve_axes_strict_gating_choices_dict_with_score_does_not_set_score():
    """Mirror case: a choices-typed eval's dict carries a score, but
    gating routes only output_choice."""
    axes = resolve_eval_axes(
        {"score": 0.7, "choice": "frequently"}, "choices", multi_choice=False
    )
    assert axes["output_score"] is None


# ── build_simulate_eval_payload ──────────────────────────────────────────


def test_payload_success_score():
    payload = build_simulate_eval_payload(
        value=0.75,
        config_output="score",
        reason="ok",
        name="eval-a",
        output_type="score",
    )
    assert payload["output"] == 0.75
    assert payload["output_score"] == pytest.approx(0.75)
    assert payload["output_pass"] is None
    assert payload["output_choice"] is None
    assert payload["output_choices"] is None
    assert payload["reason"] == "ok"
    assert payload["name"] == "eval-a"
    assert payload["output_type"] == "score"
    assert "error" not in payload
    assert "status" not in payload
    assert "skipped" not in payload


def test_payload_success_pass_fail():
    payload = build_simulate_eval_payload(
        value="Passed",
        config_output="Pass/Fail",
        name="eval-b",
        output_type="Pass/Fail",
    )
    assert payload["output_pass"] is True
    assert payload["output_score"] is None
    assert payload["output"] == "Passed"


def test_payload_success_choices_single_dict():
    payload = build_simulate_eval_payload(
        value={"score": 1.0, "choice": "always"},
        config_output="choices",
        multi_choice=False,
        name="eval-c",
        output_type="choices",
    )
    assert payload["output_choice"] == "always"
    assert payload["output_choices"] is None
    assert payload["output_score"] is None
    assert payload["output"] == {"score": 1.0, "choice": "always"}


def test_payload_success_choices_multi_dict():
    payload = build_simulate_eval_payload(
        value={"score": 0.5, "choices": ["polite", "concise"]},
        config_output="choices",
        multi_choice=True,
        name="eval-d",
        output_type="choices",
    )
    assert payload["output_choices"] == ["polite", "concise"]
    assert payload["output_choice"] is None
    assert payload["output_score"] is None


def test_payload_error_path_all_axes_none():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="score",
        reason="boom",
        name="eval-e",
        output_type="score",
        error="error",
        timestamp="2026-06-19T00:00:00",
    )
    assert payload["output"] is None
    for key in AXIS_KEYS:
        assert payload[key] is None, key
    assert payload["error"] == "error"
    assert payload["timestamp"] == "2026-06-19T00:00:00"


def test_payload_skipped_path_emits_skipped_flag():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="score",
        reason="processing skipped",
        name="eval-f",
        output_type=None,
        status="skipped",
        skipped=True,
    )
    assert payload["status"] == "skipped"
    assert payload["skipped"] is True
    for key in AXIS_KEYS:
        assert payload[key] is None, key


def test_payload_always_carries_canonical_keys():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="score",
    )
    base_keys = {"output", "reason", "output_type", "name", *AXIS_KEYS}
    assert base_keys.issubset(payload.keys())
