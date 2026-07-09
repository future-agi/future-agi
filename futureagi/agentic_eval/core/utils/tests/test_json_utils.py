"""Tests for strip_code_fence — the shared Markdown-fence unwrapper."""

from agentic_eval.core.utils.json_utils import strip_code_fence


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
