"""Tests for ``evaluations.engine.normalize``."""

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
        (-0.5, -0.5),
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
        {"score": None},
        {"score": True},
        {},
        ["A"],
    ],
)
def test_extract_score_yields_none(value):
    assert extract_score(value) is None


def test_extract_score_nan_and_infinity_pass_through():
    import math

    assert math.isnan(extract_score(float("nan")))
    assert math.isinf(extract_score(float("inf")))


def test_extract_score_int_coerced_to_float():
    result = extract_score(1)
    assert isinstance(result, float)


# ── extract_choices ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        # Single-pick string → one-element list (canonical contract)
        ("always", ["always"]),
        # Plain multi-element list
        (["polite", "concise"], ["polite", "concise"]),
        # Single-element list (legacy / tracer shape)
        (["frequently"], ["frequently"]),
        # choice_scores dict — single label
        ({"score": 1.0, "choice": "always"}, ["always"]),
        ({"choice": "x"}, ["x"]),
        # choice_scores dict — multi labels
        ({"score": 0.5, "choices": ["polite", "concise"]}, ["polite", "concise"]),
        ({"choices": ["a"]}, ["a"]),
        # Dedupe preserves order
        (["A", "B", "A", "C"], ["A", "B", "C"]),
        # Mixed list keeps strings only
        (["A", 1, "B", None], ["A", "B"]),
        # Unicode and edge strings preserved
        ("你好", ["你好"]),
        ("", [""]),
    ],
)
def test_extract_choices_extracts(value, expected):
    assert extract_choices(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        0.5,
        0,
        True,
        False,
        [],
        [1, 2, 3],
        [None, None],
        {"score": 0.5},
        {"choice": None},
        {"choice": 42},
        {"choice": True},
        {"choice": ["A"]},
        {"choices": []},
        {"choices": "polite"},
        {"choices": None},
        {},
    ],
)
def test_extract_choices_yields_none(value):
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


@pytest.mark.parametrize(
    "value",
    [
        None,
        0,
        1,
        "passed",
        "PASSED",
        "pass",
        "fail",
        "",
        {"output": "Passed"},
    ],
)
def test_extract_pass_yields_none(value):
    """Strict canonical-form matching: only exact bool / 'Passed' / 'Failed'
    convert. Lowercase / uppercase / int variants stay None to surface
    upstream label drift."""
    assert extract_pass(value) is None


# ── resolve_eval_axes — primary axis routing ─────────────────────────────


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
    """Single-pick string lands as ``["always"]`` — same list shape as
    multi-pick. Matches tracer + experiment contract."""
    axes = resolve_eval_axes("always", "choices", multi_choice=False)
    assert axes["output_choices"] == ["always"]
    assert axes["output_score"] is None
    assert axes["output_pass"] is None


def test_resolve_axes_choices_single_dict_lands_as_one_element_list():
    """choice_scores single-pick dict carries both score and choice."""
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
    """Legacy / tracer wrote single-pick as ``["frequently"]`` even when
    multi_choice=False. multi_choice is only an FE rendering hint, so
    output_choices is still the right axis."""
    axes = resolve_eval_axes(["frequently"], "choices", multi_choice=False)
    assert axes["output_choices"] == ["frequently"]


def test_resolve_axes_one_element_list_multi_choice_true():
    """Multi-pick with only one pick is still a list of one."""
    axes = resolve_eval_axes(["frequently"], "choices", multi_choice=True)
    assert axes["output_choices"] == ["frequently"]


# ── resolve_eval_axes — permissive secondary axis ───────────────────────


def test_resolve_axes_permissive_score_config_dict_populates_both_axes():
    """``choice_scores`` template with score config: dict carries both
    a score and a chosen label. Both axes populate so FE can colour the
    chosen label by the underlying score."""
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
    """Plain numeric value carries no choice — output_choices stays None."""
    axes = resolve_eval_axes(0.42, "score")
    assert axes["output_score"] == pytest.approx(0.42)
    assert axes["output_choices"] is None


def test_resolve_axes_plain_choice_does_not_invent_score():
    """Plain string value carries no score — output_score stays None."""
    axes = resolve_eval_axes("always", "choices", multi_choice=False)
    assert axes["output_choices"] == ["always"]
    assert axes["output_score"] is None


def test_resolve_axes_score_config_dict_with_only_choice():
    axes = resolve_eval_axes({"choice": "always"}, "score")
    assert axes["output_score"] is None
    assert axes["output_choices"] == ["always"]


# ── resolve_eval_axes — edge cases ──────────────────────────────────────


def test_resolve_axes_reason_yields_all_none():
    assert resolve_eval_axes("free-form text", "reason") == empty_axes()


def test_resolve_axes_unknown_config_output_yields_all_none():
    """Unknown / future output types must not panic and must not invent
    axes — strict no-op."""
    assert resolve_eval_axes(0.5, "future_type") == empty_axes()
    assert resolve_eval_axes({"score": 0.5}, "") == empty_axes()


def test_resolve_axes_pass_fail_does_not_bleed_score_or_choice():
    """Pass/Fail surface only emits Pass/Fail. Even if a dict arrives,
    score and choice must NOT bleed through."""
    axes = resolve_eval_axes({"score": 0.7, "choice": "x"}, "Pass/Fail")
    assert axes["output_pass"] is None
    assert axes["output_score"] is None
    assert axes["output_choices"] is None


def test_resolve_axes_none_value_yields_all_none():
    assert resolve_eval_axes(None, "score") == empty_axes()
    assert resolve_eval_axes(None, "choices", multi_choice=True) == empty_axes()


def test_resolve_axes_empty_dict_value():
    """Dict carrying neither score nor choice nor choices — all axes
    null."""
    assert resolve_eval_axes({}, "score") == empty_axes()
    assert resolve_eval_axes({}, "choices", multi_choice=True) == empty_axes()
    assert resolve_eval_axes({}, "choices", multi_choice=False) == empty_axes()


def test_resolve_axes_score_zero_distinguishable_from_none():
    """A score of exactly 0.0 must surface as 0.0, not None — filter UI
    treats them differently."""
    axes = resolve_eval_axes(0.0, "score")
    assert axes["output_score"] == 0.0
    assert axes["output_score"] is not None


def test_resolve_axes_idempotent():
    """Running resolve twice with the same inputs gives the same dict."""
    value = {"score": 0.7, "choice": "x"}
    assert resolve_eval_axes(value, "score") == resolve_eval_axes(value, "score")


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
