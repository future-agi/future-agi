"""Unit tests for the runtime GT injection module.

Embedding + retrieval is exercised end-to-end by
``test_ground_truth_service.py`` (with a mocked EmbeddingManager) and
by the live ``gt_roundtrip_test`` management command. This file covers
the pure-Python helpers in ``model_hub/utils/ground_truth_retrieval.py``:
the skip gate, the few-shot formatter, the label-column lookup, and
the output-type validator.
"""

from __future__ import annotations

import pytest

from model_hub.utils.ground_truth_retrieval import (
    _is_empty_value,
    format_few_shot_examples,
    get_label_columns,
    has_usable_inputs_for_gt,
    validate_output_value,
)


# ─────────────────────────────────────────────────────────────────────
# _is_empty_value
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("\n\t  ", True),
        ([], True),
        ({}, True),
        ((), True),
        (set(), True),
        # Falsy-but-legitimate scalars are valid eval inputs, NOT empty.
        (0, False),
        (0.0, False),
        (False, False),
        # Real content
        ("hello", False),
        ("  hello  ", False),
        ([1], False),
        ({"k": "v"}, False),
        (42, False),
    ],
)
def test_is_empty_value(value, expected):
    assert _is_empty_value(value) is expected


# ─────────────────────────────────────────────────────────────────────
# has_usable_inputs_for_gt — the eval-runner skip rule
# ─────────────────────────────────────────────────────────────────────


def test_skip_when_variable_mapping_is_empty():
    """Evals that declare no template variables (or haven't been mapped)
    should never get GT injection — there's nothing to query against."""
    assert has_usable_inputs_for_gt({}, {"question": "hi"}) is False
    assert has_usable_inputs_for_gt(None, {"question": "hi"}) is False


def test_skip_when_runtime_inputs_missing():
    assert has_usable_inputs_for_gt({"q": "col"}, None) is False
    assert has_usable_inputs_for_gt({"q": "col"}, {}) is False
    assert has_usable_inputs_for_gt({"q": "col"}, "not a dict") is False


def test_skip_when_every_mapped_value_is_empty():
    mapping = {"question": "q_col", "context": "ctx_col"}
    assert (
        has_usable_inputs_for_gt(mapping, {"question": "", "context": "   "})
        is False
    )
    assert (
        has_usable_inputs_for_gt(mapping, {"question": None, "context": []})
        is False
    )


def test_proceed_when_any_mapped_value_is_present():
    mapping = {"question": "q_col", "context": "ctx_col"}
    assert (
        has_usable_inputs_for_gt(
            mapping, {"question": "what time is it", "context": ""}
        )
        is True
    )
    # Falsy-but-legitimate scalars still gate-open.
    assert has_usable_inputs_for_gt({"score": "s_col"}, {"score": 0}) is True
    assert has_usable_inputs_for_gt({"flag": "f_col"}, {"flag": False}) is True


def test_accepts_runtime_keyed_by_gt_column_name():
    """Legacy callers sometimes key runtime values by the GT column name
    rather than the template variable. The gate accepts either keying."""
    assert has_usable_inputs_for_gt({"question": "q_col"}, {"q_col": "hi"}) is True


def test_list_mapping_opens_gate_on_any_present_column():
    mapping = {"input": ["text_col", "image_col"]}
    assert (
        has_usable_inputs_for_gt(
            mapping, {"text_col": "", "image_col": "https://x/y.png"}
        )
        is True
    )
    assert (
        has_usable_inputs_for_gt(
            mapping, {"text_col": "", "image_col": ""}
        )
        is False
    )


# ─────────────────────────────────────────────────────────────────────
# get_label_columns
# ─────────────────────────────────────────────────────────────────────


def test_label_cols_empty_mapping():
    assert get_label_columns(None) == ("", "")
    assert get_label_columns({}) == ("", "")


def test_label_cols_canonical_keys():
    assert get_label_columns({"output": "v", "explanation": "r"}) == ("v", "r")


def test_label_cols_legacy_keys_accepted():
    assert get_label_columns(
        {"expected_output": "v", "reasoning": "r"}
    ) == ("v", "r")
    assert get_label_columns({"expected_output": "v", "reason": "r"}) == (
        "v",
        "r",
    )


def test_label_cols_canonical_wins_over_legacy():
    assert get_label_columns(
        {"output": "new", "expected_output": "old"}
    ) == ("new", "")


def test_label_cols_explanation_is_optional():
    assert get_label_columns({"output": "v"}) == ("v", "")


def test_label_cols_list_value_picks_first():
    assert get_label_columns({"output": ["first", "second"]}) == (
        "first",
        "",
    )


# ─────────────────────────────────────────────────────────────────────
# validate_output_value
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["Pass", "FAIL", "True", "false", "0", "1", "yes", "No"],
)
def test_validate_pass_fail_accepts_canonical(value):
    ok, err = validate_output_value(value, "pass_fail")
    assert ok is True
    assert err is None


def test_validate_pass_fail_rejects_garbage():
    ok, err = validate_output_value("maybe", "pass_fail")
    assert ok is False
    assert err == "Expected one of: Pass / Fail / True / False / Yes / No."


@pytest.mark.parametrize("value", [0, "0", 0.0, "0.5", 1, "1", "0.75"])
def test_validate_percentage_accepts_range(value):
    ok, _ = validate_output_value(value, "percentage")
    assert ok is True


@pytest.mark.parametrize("value", [-0.1, 1.5, "abc", "  "])
def test_validate_percentage_rejects_out_of_range_or_garbage(value):
    ok, _ = validate_output_value(value, "percentage")
    assert ok is False


def test_validate_deterministic_accepts_known_choice():
    ok, err = validate_output_value(
        "good", "deterministic", {"good": 1.0, "bad": 0.0}
    )
    assert ok is True
    assert err is None


def test_validate_deterministic_rejects_unknown_choice():
    ok, err = validate_output_value(
        "meh", "deterministic", {"good": 1.0, "bad": 0.0}
    )
    assert ok is False
    assert err == "Expected one of: good, bad."


def test_validate_unknown_output_type_is_permissive():
    ok, err = validate_output_value("anything", "")
    assert ok is True
    assert err is None
    ok, err = validate_output_value("anything", "future_output_type")
    assert ok is True
    assert err is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_validate_rejects_empty(value):
    ok, err = validate_output_value(value, "pass_fail")
    assert ok is False
    assert err == "Value is empty."


# ─────────────────────────────────────────────────────────────────────
# format_few_shot_examples
# ─────────────────────────────────────────────────────────────────────


def test_format_empty_returns_empty_string():
    assert format_few_shot_examples([], variable_mapping=None) == ""
    assert format_few_shot_examples([], variable_mapping={"q": "col"}) == ""


def test_format_structured_includes_inputs_and_labels():
    text = format_few_shot_examples(
        [{"q": "hi", "verdict": "Pass", "reason": "polite"}],
        variable_mapping={"question": "q"},
        output_column="verdict",
        explanation_column="reason",
    )
    assert "Question: hi" in text
    assert "Eval Output: Pass" in text
    assert "Eval Output Explanation: polite" in text


def test_format_structured_omits_explanation_when_not_mapped():
    text = format_few_shot_examples(
        [{"q": "hi", "verdict": "Pass"}],
        variable_mapping={"question": "q"},
        output_column="verdict",
        explanation_column="",
    )
    assert "Eval Output: Pass" in text
    assert "Explanation" not in text


def test_format_conversational_shape():
    text = format_few_shot_examples(
        [{"q": "hi", "verdict": "Pass"}],
        variable_mapping={"question": "q"},
        output_column="verdict",
        explanation_column="",
        injection_format="conversational",
    )
    assert "Example 1: Question: hi" in text
    assert "Expert judgment: Eval Output: Pass" in text


def test_format_xml_shape():
    text = format_few_shot_examples(
        [{"q": "hi", "verdict": "Pass", "r": "ok"}],
        variable_mapping={"question": "q"},
        output_column="verdict",
        explanation_column="r",
        injection_format="xml",
    )
    assert "<reference_examples>" in text
    assert '<example eval_output="Pass">' in text
    assert "<question>hi</question>" in text
    assert "<eval_output_explanation>ok</eval_output_explanation>" in text
