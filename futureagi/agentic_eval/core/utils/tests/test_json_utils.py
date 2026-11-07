"""Tests for JSON extraction and Markdown-fence unwrapping."""

import json

import pytest

from agentic_eval.core.utils.json_utils import (
    extract_dict_from_string,
    strip_code_fence,
)


@pytest.mark.unit
@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "Response:\n{}\nDone."])
@pytest.mark.parametrize(
    "text",
    [
        "left\tright",
        "left\rright",
        "left\r\nright",
        r"C:\temp\report.txt",
        r"\r?\t+",
        r"\text{answer}",
        "plain text",
        "left\nright",
    ],
)
def test_extract_dict_preserves_json_strings(text, wrapper):
    expected = {"reason": text, "details": {"values": [text]}, "score": 0.75}
    payload = wrapper.format(json.dumps(expected, indent=2))
    assert extract_dict_from_string(payload) == expected


@pytest.mark.unit
def test_extract_dict_keeps_single_quoted_object_fallback():
    assert extract_dict_from_string("Response: {'score': 0.75}") == {"score": 0.75}


@pytest.mark.unit
def test_extract_dict_rejects_text_without_an_object():
    with pytest.raises(ValueError, match="Unable to generate a response"):
        extract_dict_from_string("No JSON object here")


def test_multiline_json_fence():
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_bare_fence_no_language():
    assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_single_line_fence_is_not_dropped():
    # Regression: a naive line-split returns "" here, dropping the payload.
    assert strip_code_fence('```json{"a": 1}```') == '{"a": 1}'


def test_no_fence_returned_unchanged():
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'


def test_truncated_opening_fence():
    assert strip_code_fence('```json\n{"a": 1}') == '{"a": 1}'


def test_none_and_empty_are_safe():
    assert strip_code_fence(None) == ""
    assert strip_code_fence("") == ""


def test_multiline_body_preserved():
    text = "```\nline1\nline2\n```"
    assert strip_code_fence(text) == "line1\nline2"
