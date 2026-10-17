"""
Hypothesis property tests for CustomPromptEvaluator truncation behaviour.

Exercises _truncate_string (the lowest-level primitive) and the evaluator-level
decision logic (fit_to_context threshold) against arbitrary generated inputs.
"""

import sys
import os
import importlib.util
import pytest

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# Load context_window directly to avoid Django-dependent package __init__ chains.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "core_evals", "fi_evals", "llm",
    "custom_prompt_evaluator", "context_window.py",
)
_spec = importlib.util.spec_from_file_location("context_window", os.path.abspath(_MODULE_PATH))
_cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cw)

_truncate_string = _cw._truncate_string
fit_to_context = _cw.fit_to_context
DEFAULT_MAX_TOTAL_CHARS = _cw.DEFAULT_MAX_TOTAL_CHARS
DEFAULT_MAX_FIELD_CHARS = _cw.DEFAULT_MAX_FIELD_CHARS

MARKER_SUBSTR = "[truncated,"

# ── _truncate_string properties ──────────────────────────────────────────────

@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=0, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_output_never_exceeds_limit(s, limit):
    result = _truncate_string(s, limit)
    assert len(result) <= limit


@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=0, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_no_change_at_or_below_limit(s, limit):
    if len(s) <= limit:
        assert _truncate_string(s, limit) == s


@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=0, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_truncation_marker_present_when_truncated(s, limit):
    result = _truncate_string(s, limit)
    if len(s) > limit:
        assert MARKER_SUBSTR in result


@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=0, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_no_marker_when_not_truncated(s, limit):
    result = _truncate_string(s, limit)
    if len(s) <= limit:
        assert MARKER_SUBSTR not in result


@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=50, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_idempotent(s, limit):
    """Applying truncation twice is the same as applying it once."""
    once = _truncate_string(s, limit)
    twice = _truncate_string(once, limit)
    assert once == twice


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(
    s=st.text(min_size=0, max_size=60000),
    lo=st.integers(min_value=50, max_value=30000),
    delta=st.integers(min_value=1, max_value=30000),
)
def test_monotone_in_limit(s, lo, delta):
    """Higher limit never produces shorter output."""
    hi = lo + delta
    out_lo = _truncate_string(s, lo)
    out_hi = _truncate_string(s, hi)
    assert len(out_hi) >= len(out_lo)


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(s=st.text(min_size=50, max_size=60000), limit=st.integers(min_value=50, max_value=60000))
def test_output_is_prefix_of_original_when_truncated(s, limit):
    """The non-marker prefix of a truncated result is a prefix of the original."""
    if len(s) > limit:
        result = _truncate_string(s, limit)
        marker_pos = result.find(f"\n... {MARKER_SUBSTR}")
        if marker_pos > 0:
            assert s.startswith(result[:marker_pos])


# ── fit_to_context top-level properties ──────────────────────────────────────

@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(
    s=st.text(min_size=0, max_size=60000),
    limit=st.integers(min_value=50, max_value=60000),
)
def test_fit_to_context_string_within_limit(s, limit):
    result = fit_to_context(s, max_total_chars=limit)
    assert len(result) <= limit


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(
    d=st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.text(min_size=0, max_size=5000),
        min_size=0,
        max_size=20,
    ),
    limit=st.integers(min_value=100, max_value=60000),
)
def test_fit_to_context_dict_within_limit(d, limit):
    result = fit_to_context(d, max_total_chars=limit)
    assert len(result) <= limit


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(
    items=st.lists(st.text(min_size=0, max_size=1000), min_size=0, max_size=50),
    limit=st.integers(min_value=100, max_value=60000),
)
def test_fit_to_context_list_within_limit(items, limit):
    result = fit_to_context(items, max_total_chars=limit)
    assert len(result) <= limit
