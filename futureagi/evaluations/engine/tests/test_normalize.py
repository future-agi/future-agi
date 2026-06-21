"""Tests for ``evaluations.engine.normalize``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evaluations.engine.normalize import (
    AXIS_KEYS,
    build_simulate_eval_payload,
    empty_axes,
    eval_config_multi_choice,
    eval_config_output,
    resolve_eval_axes,
)


def _custom_eval_config(*, stored_output=None, multi_choice=None):
    config = {"output": stored_output} if stored_output is not None else {}
    template = SimpleNamespace(config=config)
    if multi_choice is not None:
        template.multi_choice = multi_choice
    return SimpleNamespace(eval_template=template)


# AXIS_KEYS + empty_axes

def test_axis_keys_pinned():
    assert AXIS_KEYS == ("output_pass", "output_score", "output_choices")


def test_empty_axes_returns_all_none():
    assert empty_axes() == {
        "output_pass": None,
        "output_score": None,
        "output_choices": None,
    }


def test_empty_axes_returns_fresh_dict_each_call():
    a = empty_axes()
    a["output_score"] = 1.0
    assert empty_axes()["output_score"] is None


# eval_config_output

def test_eval_config_output_reads_stored_value():
    assert eval_config_output(_custom_eval_config(stored_output="choices")) == "choices"


def test_eval_config_output_defaults_to_score_when_missing():
    assert eval_config_output(_custom_eval_config()) == "score"


def test_eval_config_output_defaults_when_no_template():
    assert eval_config_output(SimpleNamespace()) == "score"


# eval_config_multi_choice

def test_eval_config_multi_choice_reads_flag():
    assert eval_config_multi_choice(_custom_eval_config(multi_choice=True)) is True
    assert eval_config_multi_choice(_custom_eval_config(multi_choice=False)) is False


def test_eval_config_multi_choice_defaults_false_when_missing():
    assert eval_config_multi_choice(_custom_eval_config()) is False


def test_eval_config_multi_choice_defaults_when_no_template():
    assert eval_config_multi_choice(SimpleNamespace()) is False


# resolve_eval_axes: primary axis routing

def test_resolve_axes_pass_fail_routes_to_output_pass_only():
    axes = resolve_eval_axes("Passed", "Pass/Fail")
    assert axes == {
        "output_pass": True,
        "output_score": None,
        "output_choices": None,
    }


def test_resolve_axes_score_plain_float():
    axes = resolve_eval_axes(0.7, "score")
    assert axes["output_score"] == pytest.approx(0.7)
    assert axes["output_pass"] is None
    assert axes["output_choices"] is None


def test_resolve_axes_numeric_routes_to_output_score():
    axes = resolve_eval_axes(0.42, "numeric")
    assert axes["output_score"] == pytest.approx(0.42)
    assert axes["output_choices"] is None


def test_resolve_axes_choices_single_plain_string_lands_as_one_element_list():
    axes = resolve_eval_axes("always", "choices", multi_choice=False)
    assert axes["output_choices"] == ["always"]
    assert axes["output_score"] is None
    assert axes["output_pass"] is None


def test_resolve_axes_choices_single_dict_lands_as_one_element_list():
    axes = resolve_eval_axes(
        {"score": 1.0, "choice": "always"}, "choices", multi_choice=False
    )
    assert axes["output_choices"] == ["always"]
    assert axes["output_score"] == pytest.approx(1.0)


def test_resolve_axes_choices_multi_plain_list():
    axes = resolve_eval_axes(["A", "B"], "choices", multi_choice=True)
    assert axes["output_choices"] == ["A", "B"]
    assert axes["output_score"] is None


def test_resolve_axes_choices_multi_dict():
    axes = resolve_eval_axes(
        {"score": 0.5, "choices": ["polite", "concise"]},
        "choices",
        multi_choice=True,
    )
    assert axes["output_choices"] == ["polite", "concise"]
    assert axes["output_score"] == pytest.approx(0.5)


def test_resolve_axes_legacy_single_choice_as_one_element_list():
    axes = resolve_eval_axes(["frequently"], "choices", multi_choice=False)
    assert axes["output_choices"] == ["frequently"]


def test_resolve_axes_one_element_list_multi_choice_true():
    axes = resolve_eval_axes(["frequently"], "choices", multi_choice=True)
    assert axes["output_choices"] == ["frequently"]


# resolve_eval_axes: permissive secondary axis

def test_resolve_axes_permissive_score_config_dict_populates_both_axes():
    axes = resolve_eval_axes({"score": 0.7, "choice": "always"}, "score")
    assert axes["output_score"] == pytest.approx(0.7)
    assert axes["output_choices"] == ["always"]
    assert axes["output_pass"] is None


def test_resolve_axes_permissive_score_config_dict_with_choices_list():
    axes = resolve_eval_axes(
        {"score": 0.7, "choices": ["a", "b"]}, "score"
    )
    assert axes["output_score"] == pytest.approx(0.7)
    assert axes["output_choices"] == ["a", "b"]


def test_resolve_axes_plain_score_does_not_invent_choice():
    axes = resolve_eval_axes(0.42, "score")
    assert axes["output_score"] == pytest.approx(0.42)
    assert axes["output_choices"] is None


def test_resolve_axes_plain_choice_does_not_invent_score():
    axes = resolve_eval_axes("always", "choices", multi_choice=False)
    assert axes["output_choices"] == ["always"]
    assert axes["output_score"] is None


def test_resolve_axes_score_config_dict_with_only_choice():
    axes = resolve_eval_axes({"choice": "always"}, "score")
    assert axes["output_score"] is None
    assert axes["output_choices"] == ["always"]


# resolve_eval_axes: edge cases

def test_resolve_axes_reason_yields_all_none():
    assert resolve_eval_axes("free-form text", "reason") == empty_axes()


def test_resolve_axes_pass_fail_does_not_bleed_score_or_choice():
    axes = resolve_eval_axes({"score": 0.7, "choice": "x"}, "Pass/Fail")
    assert axes["output_pass"] is None
    assert axes["output_score"] is None
    assert axes["output_choices"] is None


def test_resolve_axes_none_value_yields_all_none():
    assert resolve_eval_axes(None, "score") == empty_axes()
    assert resolve_eval_axes(None, "choices", multi_choice=True) == empty_axes()


def test_resolve_axes_empty_dict_value():
    assert resolve_eval_axes({}, "score") == empty_axes()
    assert resolve_eval_axes({}, "choices", multi_choice=True) == empty_axes()
    assert resolve_eval_axes({}, "choices", multi_choice=False) == empty_axes()


def test_resolve_axes_score_zero_distinguishable_from_none():
    axes = resolve_eval_axes(0.0, "score")
    assert axes["output_score"] == 0.0
    assert axes["output_score"] is not None


def test_resolve_axes_idempotent():
    value = {"score": 0.7, "choice": "x"}
    assert resolve_eval_axes(value, "score") == resolve_eval_axes(value, "score")


# build_simulate_eval_payload

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
    assert payload["output_choices"] is None
    assert payload["output"] == "Passed"


def test_payload_success_choices_single_dict():
    payload = build_simulate_eval_payload(
        value={"score": 1.0, "choice": "always"},
        config_output="choices",
        multi_choice=False,
        name="eval-c",
        output_type="choices",
    )
    assert payload["output_choices"] == ["always"]
    assert payload["output_score"] == pytest.approx(1.0)
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
    assert payload["output_score"] == pytest.approx(0.5)


def test_payload_error_path_all_axes_none():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="score",
        reason="boom",
        name="eval-e",
        output_type="score",
        error="something failed",
        status="error",
    )
    assert payload["error"] == "something failed"
    assert payload["status"] == "error"
    for key in AXIS_KEYS:
        assert payload[key] is None, key


def test_payload_skipped_path_carries_skipped_flag_and_null_axes():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="choices",
        multi_choice=True,
        skipped=True,
    )
    assert payload["skipped"] is True
    for key in AXIS_KEYS:
        assert key in payload
        assert payload[key] is None


def test_payload_carries_timestamp_when_supplied():
    payload = build_simulate_eval_payload(
        value=0.5,
        config_output="score",
        timestamp="2026-06-19T12:00:00Z",
    )
    assert payload["timestamp"] == "2026-06-19T12:00:00Z"


def test_payload_omits_optional_fields_when_unset():
    payload = build_simulate_eval_payload(value=0.5, config_output="score")
    assert "error" not in payload
    assert "status" not in payload
    assert "skipped" not in payload
    assert "timestamp" not in payload


def test_payload_does_not_mutate_input_value():
    value = {"score": 0.5, "choice": "x"}
    snapshot = dict(value)
    build_simulate_eval_payload(value=value, config_output="score")
    assert value == snapshot


def test_payload_always_carries_canonical_keys():
    payload = build_simulate_eval_payload(
        value=None,
        config_output="score",
    )
    base_keys = {"output", "reason", "output_type", "name", *AXIS_KEYS}
    assert base_keys.issubset(payload.keys())
