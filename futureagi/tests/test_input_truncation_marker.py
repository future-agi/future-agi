"""String-budget regressions for #317, collected by the backend CI suite.

The marker must fit inside the budget, not be appended to a fixed-size prefix.
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
    assert (
        len(result) <= max_chars
    ), f"overflow at max_chars={max_chars}: len={len(result)}"


@pytest.mark.unit
def test_truncate_string_short_input_returned_verbatim():
    assert _truncate_string("short", 50) == "short"


@pytest.mark.parametrize("max_chars", range(37))
def test_tiny_limits_keep_a_bounded_prefix_or_complete_marker(max_chars):
    value = "synthetic input " * 400
    marker = f"\n... [truncated, {len(value)} total chars]"
    result = _truncate_string(value, max_chars)
    assert len(result) <= max_chars
    if max_chars < len(marker):
        assert result == value[:max_chars]
    else:
        assert result == value[: max_chars - len(marker)] + marker


@pytest.mark.parametrize("length", [99, 100, 999, 1000, 9999, 10000])
def test_complete_marker_uses_actual_digit_count(length):
    value = "é" * length
    marker = f"\n... [truncated, {length} total chars]"
    assert _truncate_string(value, 50) == value[: 50 - len(marker)] + marker


@pytest.mark.parametrize("value,limit", [("", 0), ("short", 5), ("é🙂", 2)])
def test_input_at_limit_is_unchanged(value, limit):
    assert _truncate_string(value, limit) == value


def test_negative_limit_is_rejected():
    with pytest.raises(ValueError, match="max_chars must be non-negative"):
        _truncate_string("input", -1)
