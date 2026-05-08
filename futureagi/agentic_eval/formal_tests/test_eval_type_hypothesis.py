"""
Hypothesis property-based tests for agentic_eval pure classifier functions.

Tests the actual implementations imported directly (eval_type.py has no
external dependencies beyond stdlib enum).

Also tests format_concise_error (inlined — error_handler.py has litellm deps).

Run with: pytest agentic_eval/formal_tests/ -v -m unit
"""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.unit

# ── Direct imports (eval_type.py only needs stdlib enum) ─────────────────────

import importlib.util
import pathlib

_eval_type_path = (
    pathlib.Path(__file__).parent.parent
    / "core_evals" / "fi_evals" / "eval_type.py"
)
_spec = importlib.util.spec_from_file_location("eval_type", _eval_type_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

FunctionEvalTypeId = _mod.FunctionEvalTypeId
FutureAgiEvalTypeId = _mod.FutureAgiEvalTypeId
GroundedEvalTypeId = _mod.GroundedEvalTypeId
LlmEvalTypeId = _mod.LlmEvalTypeId
is_function_eval = _mod.is_function_eval
is_future_agi_eval = _mod.is_future_agi_eval
is_grounded_eval = _mod.is_grounded_eval
is_llm_eval = _mod.is_llm_eval


# ── Inlined format_concise_error (error_handler.py has litellm deps) ─────────

def format_concise_error(parsed_error: dict[str, Any]) -> str:
    message = parsed_error["message"]
    max_message_length = 200
    if len(message) > max_message_length:
        message = message[:max_message_length] + "..."
    return message


# ── Helpers: collect all known evaluator type values ─────────────────────────

_ALL_LLM = {m.value for m in LlmEvalTypeId}
_ALL_FUNCTION = {m.value for m in FunctionEvalTypeId}
_ALL_GROUNDED = {m.value for m in GroundedEvalTypeId}
_ALL_FUTURE_AGI = {m.value for m in FutureAgiEvalTypeId}
_ALL_KNOWN = _ALL_LLM | _ALL_FUNCTION | _ALL_GROUNDED | _ALL_FUTURE_AGI


# ── is_llm_eval ───────────────────────────────────────────────────────────────

@given(st.sampled_from(sorted(_ALL_LLM)))
def test_is_llm_eval_true_for_all_llm_types(t):
    assert is_llm_eval(t) is True


@given(st.sampled_from(sorted(_ALL_FUNCTION | _ALL_GROUNDED | _ALL_FUTURE_AGI)))
def test_is_llm_eval_false_for_non_llm_types(t):
    assert is_llm_eval(t) is False


@given(st.text().filter(lambda s: s not in _ALL_KNOWN))
def test_is_llm_eval_false_for_unknown(t):
    assert is_llm_eval(t) is False


# ── is_function_eval ──────────────────────────────────────────────────────────

@given(st.sampled_from(sorted(_ALL_FUNCTION)))
def test_is_function_eval_true_for_all_function_types(t):
    assert is_function_eval(t) is True


@given(st.sampled_from(sorted(_ALL_LLM | _ALL_GROUNDED | _ALL_FUTURE_AGI)))
def test_is_function_eval_false_for_non_function_types(t):
    assert is_function_eval(t) is False


@given(st.text().filter(lambda s: s not in _ALL_KNOWN))
def test_is_function_eval_false_for_unknown(t):
    assert is_function_eval(t) is False


# ── Mutual exclusion ──────────────────────────────────────────────────────────

@given(st.sampled_from(sorted(_ALL_KNOWN)))
def test_each_known_type_in_exactly_one_category(t):
    """Each known evaluator type is classified by exactly one is_*_eval function."""
    results = [
        is_llm_eval(t),
        is_function_eval(t),
        is_grounded_eval(t),
        is_future_agi_eval(t),
    ]
    assert sum(results) == 1, f"{t!r} matched {sum(results)} categories: {results}"


@given(st.text().filter(lambda s: s not in _ALL_KNOWN))
def test_unknown_type_in_no_category(t):
    """Unknown evaluator types return False for all classifiers."""
    assert not is_llm_eval(t)
    assert not is_function_eval(t)
    assert not is_grounded_eval(t)
    assert not is_future_agi_eval(t)


# ── Partition coverage ────────────────────────────────────────────────────────

def test_all_enum_members_covered_by_exactly_one_classifier():
    """
    Exhaustive check: for every enum member in all four enums,
    exactly one classifier returns True.
    """
    violations = []
    for t in _ALL_KNOWN:
        matches = sum([
            is_llm_eval(t),
            is_function_eval(t),
            is_grounded_eval(t),
            is_future_agi_eval(t),
        ])
        if matches != 1:
            violations.append((t, matches))
    assert not violations, f"Partition violations: {violations}"


def test_no_overlap_between_llm_and_function():
    assert _ALL_LLM.isdisjoint(_ALL_FUNCTION), "LLM and Function enums share values"


def test_no_overlap_between_llm_and_grounded():
    assert _ALL_LLM.isdisjoint(_ALL_GROUNDED), "LLM and Grounded enums share values"


def test_no_overlap_between_function_and_grounded():
    assert _ALL_FUNCTION.isdisjoint(_ALL_GROUNDED), "Function and Grounded enums share values"


def test_no_overlap_between_function_and_future_agi():
    assert _ALL_FUNCTION.isdisjoint(_ALL_FUTURE_AGI), "Function and FutureAGI enums share values"


# ── format_concise_error ──────────────────────────────────────────────────────

@given(st.text(max_size=200))
def test_short_message_passes_through_unchanged(msg):
    result = format_concise_error({"message": msg})
    assert result == msg


@given(st.text(min_size=201))
def test_long_message_is_truncated(msg):
    result = format_concise_error({"message": msg})
    assert len(result) == 203  # 200 chars + "..."
    assert result.endswith("...")


@given(st.text())
@settings(max_examples=500)
def test_output_length_bounded(msg):
    result = format_concise_error({"message": msg})
    assert len(result) <= 203


@given(st.text(min_size=201))
def test_truncated_message_starts_with_original_prefix(msg):
    result = format_concise_error({"message": msg})
    assert result[:200] == msg[:200]
