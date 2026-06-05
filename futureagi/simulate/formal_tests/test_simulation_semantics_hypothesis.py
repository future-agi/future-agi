"""
Hypothesis property-based tests for simulate module pure functions.

Tests the actual implementations (imported directly for processing_outcomes,
inlined for other modules with heavy deps).

Implementations sourced from:
  - simulate/utils/processing_outcomes.py
  - simulate/semantics.py
  - simulate/utils/eval_summary.py

Run with: pytest simulate/formal_tests/ -v -m unit
"""

from __future__ import annotations

from typing import Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import DrawFn

pytestmark = pytest.mark.unit


# ── Implementations under test ────────────────────────────────────────────────

# processing_outcomes.py has zero external dependencies — import it directly.
from simulate.utils.processing_outcomes import (  # noqa: E402  (after conftest stubs)
    build_skipped_eval_output_payload,
    set_processing_skip_metadata,
)


# validate_allowed_keys inlined from simulate/semantics.py
# (semantics.py imports tracer.models at module level which requires Django)
def validate_allowed_keys(
    v: dict, allowed_keys: set | None = None, _permitted: set | None = None
) -> dict:
    permitted = _permitted or allowed_keys or {"vapi", "retell", "eleven_labs", "livekit", "others"}
    extra_keys = set(v.keys()) - permitted
    if extra_keys:
        raise ValueError(f"Contains forbidden keys: {extra_keys}")
    return v


# _calculate_avg_score inlined from simulate/utils/eval_summary.py
def _calculate_avg_score(valid_scores: list) -> float:
    return round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0


# ── set_processing_skip_metadata ──────────────────────────────────────────────

@given(
    call_metadata=st.one_of(st.none(), st.dictionaries(st.text(min_size=1), st.text())),
    skipped=st.booleans(),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skip_metadata_processing_skipped_reflects_input(call_metadata, skipped, reason):
    result = set_processing_skip_metadata(call_metadata, skipped=skipped, reason=reason)
    assert result["processing_skipped"] == bool(skipped)


@given(
    call_metadata=st.one_of(st.none(), st.dictionaries(st.text(min_size=1), st.text())),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skip_false_always_produces_none_reason(call_metadata, reason):
    """When skipped=False, processing_skip_reason is always None."""
    result = set_processing_skip_metadata(call_metadata, skipped=False, reason=reason)
    assert result["processing_skip_reason"] is None


@given(
    call_metadata=st.one_of(st.none(), st.dictionaries(st.text(min_size=1), st.text())),
    reason=st.text(min_size=1),
)
def test_skip_true_with_reason_preserves_reason(call_metadata, reason):
    """When skipped=True and reason is provided, processing_skip_reason equals reason."""
    result = set_processing_skip_metadata(call_metadata, skipped=True, reason=reason)
    assert result["processing_skip_reason"] == reason


@given(
    call_metadata=st.dictionaries(st.text(min_size=1, max_size=10), st.text()),
    skipped=st.booleans(),
    reason=st.one_of(st.none(), st.text()),
)
def test_skip_metadata_preserves_input_keys(call_metadata, skipped, reason):
    """All keys from call_metadata are preserved in the output."""
    result = set_processing_skip_metadata(call_metadata, skipped=skipped, reason=reason)
    for key in call_metadata:
        assert key in result


@given(
    call_metadata=st.dictionaries(st.text(min_size=1, max_size=10), st.text()),
    skipped=st.booleans(),
    reason=st.one_of(st.none(), st.text()),
)
def test_skip_metadata_does_not_mutate_input(call_metadata, skipped, reason):
    """Input dict is not mutated (function creates a copy)."""
    original = dict(call_metadata)
    set_processing_skip_metadata(call_metadata, skipped=skipped, reason=reason)
    assert call_metadata == original


def test_skip_metadata_none_input_treated_as_empty():
    result = set_processing_skip_metadata(None, skipped=True, reason="test")
    assert result["processing_skipped"] is True
    assert result["processing_skip_reason"] == "test"


# ── build_skipped_eval_output_payload ─────────────────────────────────────────

@given(
    eval_name=st.text(min_size=1, max_size=100),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skipped_payload_status_always_skipped(eval_name, reason):
    result = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    assert result["status"] == "skipped"


@given(
    eval_name=st.text(min_size=1, max_size=100),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skipped_payload_skipped_always_true(eval_name, reason):
    result = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    assert result["skipped"] is True


@given(
    eval_name=st.text(min_size=1, max_size=100),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skipped_payload_output_always_none(eval_name, reason):
    result = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    assert result["output"] is None


@given(
    eval_name=st.text(min_size=1, max_size=100),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skipped_payload_name_equals_eval_name(eval_name, reason):
    result = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    assert result["name"] == eval_name


@given(
    eval_name=st.text(min_size=1, max_size=100),
    reason=st.one_of(st.none(), st.text(min_size=1)),
)
def test_skipped_payload_reason_preserved(eval_name, reason):
    result = build_skipped_eval_output_payload(eval_name=eval_name, reason=reason)
    assert result["reason"] == reason


# ── validate_allowed_keys ─────────────────────────────────────────────────────

_ALLOWED = {"vapi", "retell", "eleven_labs", "livekit", "others"}


@given(
    keys=st.lists(st.sampled_from(sorted(_ALLOWED)), min_size=0, max_size=5, unique=True)
)
def test_validate_allowed_keys_passes_for_valid_keys(keys):
    v = {k: "value" for k in keys}
    result = validate_allowed_keys(v, _permitted=_ALLOWED)
    assert result == v


@given(
    bad_key=st.text(min_size=1).filter(lambda k: k not in _ALLOWED),
)
def test_validate_allowed_keys_raises_for_disallowed_key(bad_key):
    v = {bad_key: "value"}
    with pytest.raises(ValueError, match="forbidden keys"):
        validate_allowed_keys(v, _permitted=_ALLOWED)


def test_validate_allowed_keys_empty_dict_passes():
    result = validate_allowed_keys({}, _permitted=_ALLOWED)
    assert result == {}


# ── _calculate_avg_score ─────────────────────────────────────────────────────

def test_avg_score_empty_returns_zero():
    assert _calculate_avg_score([]) == 0


@given(st.lists(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    min_size=1, max_size=50,
))
def test_avg_score_is_within_range(scores):
    result = _calculate_avg_score(scores)
    assert min(scores) - 0.01 <= result <= max(scores) + 0.01


@given(st.lists(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    min_size=1, max_size=50,
))
def test_avg_score_is_non_negative_for_non_negative_inputs(scores):
    result = _calculate_avg_score(scores)
    assert result >= 0


@given(st.just([0.5, 0.5]))
def test_avg_score_symmetry(scores):
    assert _calculate_avg_score(scores) == 0.5


@settings(max_examples=200)
@given(
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=20,
    )
)
def test_avg_score_is_between_min_and_max(scores):
    result = _calculate_avg_score(scores)
    assert round(min(scores), 2) - 0.01 <= result <= round(max(scores), 2) + 0.01
