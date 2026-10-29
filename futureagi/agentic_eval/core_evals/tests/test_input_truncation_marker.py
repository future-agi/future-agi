"""Behavioral regression test for #317 / PR #342.

The marker-overflow bug: _truncate_string appended a "... [truncated]" marker onto a
prefix of length max_chars, so the returned string EXCEEDED max_chars by the marker's
length. The fix reserves room for the marker (prefix_len = max_chars - len(marker)),
guaranteeing the output length is <= max_chars.

Exercises the real _truncate_string at realistic context limits (well above the marker
length): the truncated output length must never exceed max_chars. Fails on the pre-fix
version, which appended the marker to a full max_chars-length prefix and overflowed.
"""
import pytest

from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.context_window import (
    _truncate_string,
)


@pytest.mark.unit
@pytest.mark.parametrize("max_chars", [50, 100, 500, 2000])
def test_truncate_string_never_exceeds_max_chars(max_chars):
    long_input = "x" * 5000
    result = _truncate_string(long_input, max_chars)
    assert len(result) <= max_chars, f"overflow at max_chars={max_chars}: len={len(result)}"


@pytest.mark.unit
def test_truncate_string_short_input_returned_verbatim():
    # The identity half of the contract: input within the limit passes through untouched.
    assert _truncate_string("short", 50) == "short"
