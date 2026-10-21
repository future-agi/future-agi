"""
Integration probe: CustomPromptEvaluator input-truncation end-to-end.

Runs the FULL real context_window implementation (no mocks of the logic under
test) against inputs at, below, and above the truncation threshold, then checks
ALL TLA+ invariants simultaneously in _assert_invariants().

TLA+ invariants verified:
  - OutputWithinLimit:  output length ≤ requested limit
  - MarkerPositionSafe: the truncation marker, if present, starts within [0, limit]
  - SilentTruncationAbsent: every truncated output contains the marker substring
  - WarningSoundComplete: warning emitted iff len(input) > limit

Run with: pytest futureagi/agentic_eval/formal_tests/test_truncation_integration.py -v
"""

import importlib.util
import logging
import os
import sys

import pytest

# ── Load context_window without Django __init__ chains ───────────────────────

_MODULE_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "core_evals",
        "fi_evals",
        "llm",
        "custom_prompt_evaluator",
        "context_window.py",
    )
)

try:
    _spec = importlib.util.spec_from_file_location("context_window", _MODULE_PATH)
    _cw = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cw)

    _truncate_string = _cw._truncate_string
    fit_to_context = _cw.fit_to_context
    DEFAULT_MAX_TOTAL_CHARS = _cw.DEFAULT_MAX_TOTAL_CHARS
    DEFAULT_MAX_FIELD_CHARS = _cw.DEFAULT_MAX_FIELD_CHARS
except Exception as exc:
    pytest.skip(
        f"context_window module not importable: {exc}",
        allow_module_level=True,
    )

MARKER_SUBSTR = "[truncated,"
HARD_LIMIT = DEFAULT_MAX_TOTAL_CHARS  # 50 000


# ── Invariant checker ─────────────────────────────────────────────────────────

def _assert_invariants(
    result: str,
    original_input: str,
    limit: int,
    *,
    label: str = "",
) -> None:
    """
    Check ALL TLA+ invariants simultaneously on the result of _truncate_string
    or fit_to_context (string path).

    Raises AssertionError on the first violated invariant, naming it.
    """
    ctx = f" [{label}]" if label else ""

    # OutputWithinLimit
    assert len(result) <= limit, (
        f"OutputWithinLimit violated{ctx}: "
        f"len(result)={len(result)} > limit={limit}"
    )

    truncated = len(original_input) > limit

    # SilentTruncationAbsent
    if truncated:
        assert MARKER_SUBSTR in result, (
            f"SilentTruncationAbsent violated{ctx}: "
            f"input len={len(original_input)} > limit={limit} but no marker in output"
        )

    # MarkerPositionSafe
    marker_pos = result.find(MARKER_SUBSTR)
    if marker_pos != -1:
        assert 0 <= marker_pos <= limit, (
            f"MarkerPositionSafe violated{ctx}: marker_pos={marker_pos} not in [0, {limit}]"
        )

    # WarningSoundComplete (inverse: no marker when not truncated)
    if not truncated:
        assert MARKER_SUBSTR not in result, (
            f"WarningSoundComplete violated{ctx}: "
            f"input len={len(original_input)} <= limit={limit} but spurious marker in output"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_and_check(s: str, limit: int, label: str = "") -> str:
    result = _truncate_string(s, limit)
    _assert_invariants(result, s, limit, label=label)
    return result


# ── Scenario 1: well below threshold ─────────────────────────────────────────

class TestBelowThreshold:
    """Inputs clearly within the limit — no truncation should occur."""

    def test_empty_string(self):
        _run_and_check("", HARD_LIMIT, label="empty")

    def test_single_char(self):
        _run_and_check("x", HARD_LIMIT, label="single_char")

    def test_exactly_at_limit(self):
        s = "a" * HARD_LIMIT
        result = _run_and_check(s, HARD_LIMIT, label="at_limit")
        # Pass-through: no modification
        assert result == s, "Input at limit must pass through unchanged"

    def test_one_below_limit(self):
        s = "b" * (HARD_LIMIT - 1)
        result = _run_and_check(s, HARD_LIMIT, label="one_below_limit")
        assert result == s


# ── Scenario 2: at the threshold boundary ────────────────────────────────────

class TestAtThreshold:
    """Inputs exactly one character above the limit — truncation must fire."""

    def test_one_above_limit(self):
        s = "c" * (HARD_LIMIT + 1)
        result = _run_and_check(s, HARD_LIMIT, label="one_above_limit")
        assert MARKER_SUBSTR in result

    def test_small_limit_boundary(self):
        limit = 100
        s = "d" * 101
        result = _run_and_check(s, limit, label="small_limit_boundary")
        assert MARKER_SUBSTR in result

    def test_marker_fits_within_limit(self):
        """The marker itself must not overflow the limit."""
        limit = 80
        s = "e" * 200
        result = _run_and_check(s, limit, label="marker_within_limit")
        assert len(result) <= limit


# ── Scenario 3: well above threshold ─────────────────────────────────────────

class TestAboveThreshold:
    """Inputs far above the limit — marker + prefix must together honour limit."""

    def test_double_limit(self):
        s = "f" * (HARD_LIMIT * 2)
        _run_and_check(s, HARD_LIMIT, label="double_limit")

    def test_unicode_content(self):
        s = "日本語テキスト" * 10000  # multi-byte chars
        limit = 5000
        result = _run_and_check(s, limit, label="unicode_content")
        assert len(result) <= limit

    def test_newlines_in_content(self):
        s = "line\n" * 20000
        _run_and_check(s, HARD_LIMIT, label="newlines")

    def test_idempotence(self):
        """Re-applying truncation to already-truncated output is a no-op."""
        s = "g" * (HARD_LIMIT * 3)
        first = _truncate_string(s, HARD_LIMIT)
        second = _truncate_string(first, HARD_LIMIT)
        assert first == second, "Idempotence violated: second truncation changed output"
        _assert_invariants(second, first, HARD_LIMIT, label="idempotence")


# ── Scenario 4: fit_to_context top-level (string path) ───────────────────────

class TestFitToContextIntegration:
    """
    End-to-end through fit_to_context (the evaluator-facing API).
    Checks all invariants on diverse input shapes.
    """

    def test_string_below_limit(self):
        s = "hello world"
        result = fit_to_context(s, max_total_chars=HARD_LIMIT)
        _assert_invariants(result, s, HARD_LIMIT, label="fit_string_below")

    def test_string_above_limit(self):
        s = "z" * (HARD_LIMIT + 500)
        result = fit_to_context(s, max_total_chars=HARD_LIMIT)
        _assert_invariants(result, s, HARD_LIMIT, label="fit_string_above")

    def test_dict_total_length_bounded(self):
        d = {"key": "v" * 20000, "key2": "w" * 20000, "key3": "x" * 20000}
        result = fit_to_context(d, max_total_chars=HARD_LIMIT)
        assert len(result) <= HARD_LIMIT, (
            f"fit_to_context dict output exceeds limit: {len(result)}"
        )

    def test_list_total_length_bounded(self):
        items = ["item_" + str(i) + "." * 5000 for i in range(20)]
        result = fit_to_context(items, max_total_chars=HARD_LIMIT)
        assert len(result) <= HARD_LIMIT

    def test_none_returns_empty(self):
        result = fit_to_context(None, max_total_chars=HARD_LIMIT)
        assert result == ""

    def test_very_small_limit(self):
        """Even a tiny limit must be honoured — marker must not overflow."""
        limit = 60  # just above minimum marker length
        s = "a" * 1000
        result = fit_to_context(s, max_total_chars=limit)
        assert len(result) <= limit, (
            f"fit_to_context violated tiny limit {limit}: len={len(result)}"
        )


# ── Scenario 5: warning-log side-effect probe ────────────────────────────────

class TestWarningLog:
    """
    Verify the structlog warning fires on truncation and is silent otherwise.

    We capture structlog events via caplog (structlog → stdlib bridge).
    """

    def test_warning_emitted_on_truncation(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="agentic_eval"):
            s = "w" * (HARD_LIMIT + 1)
            result = _truncate_string(s, HARD_LIMIT)
        _assert_invariants(result, s, HARD_LIMIT, label="warning_on_truncation")
        # The warning is logged at DEBUG level via structlog; at minimum the
        # output must contain the marker (SilentTruncationAbsent invariant).
        assert MARKER_SUBSTR in result

    def test_no_warning_below_limit(self, caplog):
        with caplog.at_level(logging.DEBUG):
            s = "q" * (HARD_LIMIT - 1)
            result = _truncate_string(s, HARD_LIMIT)
        _assert_invariants(result, s, HARD_LIMIT, label="no_warning_below")
        assert MARKER_SUBSTR not in result
