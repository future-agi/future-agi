"""
Tests for `normalize_function_params` — pins the empty-string → schema-default
coercion that fixes the FE/BE contract for blank optional eval params.

Bug pattern: the FE form fields serialize blank optional inputs as the empty
string instead of omitting the key. The downstream eval body did
`int(kwargs.get("max_words"))` and crashed with
`invalid literal for int() with base 10: ''`. This module is the single
chokepoint that coerces empty inputs back to the schema's default before any
type coercion runs.
"""

from __future__ import annotations

import pytest

from model_hub.utils.function_eval_params import normalize_function_params


def _schema_word_count():
    """The actual function_params_schema shape from word_count_in_range.yaml."""
    return {
        "function_params_schema": {
            "min_words": {
                "type": "integer",
                "default": None,
                "nullable": True,
                "minimum": 0,
            },
            "max_words": {
                "type": "integer",
                "default": None,
                "nullable": True,
                "minimum": 1,
            },
        }
    }


# ---------------------------------------------------------------------------
# Empty-string coercion — the core fix
# ---------------------------------------------------------------------------


def test_empty_string_for_optional_integer_becomes_none():
    """The original repro: max_words='' previously triggered int('') crash."""
    out = normalize_function_params(_schema_word_count(), {"min_words": 5, "max_words": ""})
    assert out == {"min_words": 5, "max_words": None}


def test_both_empty_strings_both_become_none():
    out = normalize_function_params(_schema_word_count(), {"min_words": "", "max_words": ""})
    assert out == {"min_words": None, "max_words": None}


def test_whitespace_only_string_treated_as_empty():
    out = normalize_function_params(_schema_word_count(), {"min_words": "   ", "max_words": "\t"})
    assert out == {"min_words": None, "max_words": None}


# ---------------------------------------------------------------------------
# Happy-path preservation
# ---------------------------------------------------------------------------


def test_real_integer_values_preserved():
    out = normalize_function_params(_schema_word_count(), {"min_words": 3, "max_words": 10})
    assert out == {"min_words": 3, "max_words": 10}


def test_stringified_integer_still_coerces():
    """FE sometimes sends '5' instead of 5; existing coercion must still work."""
    out = normalize_function_params(_schema_word_count(), {"min_words": "5", "max_words": "10"})
    assert out == {"min_words": 5, "max_words": 10}


def test_mixed_empty_and_real():
    out = normalize_function_params(_schema_word_count(), {"min_words": 5, "max_words": ""})
    assert out == {"min_words": 5, "max_words": None}


def test_none_explicit_treated_as_not_provided():
    out = normalize_function_params(_schema_word_count(), {"min_words": None, "max_words": None})
    assert out == {"min_words": None, "max_words": None}


# ---------------------------------------------------------------------------
# Required + nullable interaction
# ---------------------------------------------------------------------------


def test_required_non_nullable_blank_raises():
    """Required fields with empty input should produce a clean error, not a crash."""
    schema = {
        "function_params_schema": {
            "positive_label": {
                "type": "string",
                "default": None,
                "required": True,
                "nullable": False,
            }
        }
    }
    with pytest.raises(ValueError, match="positive_label is required"):
        normalize_function_params(schema, {"positive_label": ""})


def test_required_nullable_blank_passes_as_none():
    """`nullable: true` lets blank fall through to None even when required."""
    schema = {
        "function_params_schema": {
            "threshold": {
                "type": "number",
                "default": None,
                "required": True,
                "nullable": True,
            }
        }
    }
    out = normalize_function_params(schema, {"threshold": ""})
    assert out == {"threshold": None}


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------


def test_blank_picks_up_schema_default_when_provided():
    schema = {
        "function_params_schema": {
            "beta": {
                "type": "number",
                "default": 1.0,
                "minimum": 0.01,
            }
        }
    }
    out = normalize_function_params(schema, {"beta": ""})
    assert out == {"beta": 1.0}


# ---------------------------------------------------------------------------
# Type variety — every field_type branch
# ---------------------------------------------------------------------------


def test_empty_string_for_string_param_falls_back_to_default():
    schema = {
        "function_params_schema": {
            "language": {
                "type": "string",
                "default": None,
                "nullable": True,
            }
        }
    }
    out = normalize_function_params(schema, {"language": ""})
    assert out == {"language": None}


def test_step_count_blank_min_max_repro():
    """Second repro from the ticket: step_count with blank min_steps/max_steps."""
    schema = {
        "function_params_schema": {
            "expected_steps": {"type": "integer", "default": None, "nullable": True, "minimum": 0},
            "min_steps":      {"type": "integer", "default": None, "nullable": True, "minimum": 0},
            "max_steps":      {"type": "integer", "default": None, "nullable": True, "minimum": 1},
        }
    }
    out = normalize_function_params(schema, {"expected_steps": 3, "min_steps": "", "max_steps": ""})
    assert out == {"expected_steps": 3, "min_steps": None, "max_steps": None}


# ---------------------------------------------------------------------------
# Schema absence + invalid input
# ---------------------------------------------------------------------------


def test_no_schema_returns_empty_dict():
    """Templates without a function_params_schema get an empty dict back."""
    assert normalize_function_params({}, {"anything": "goes"}) == {}
    assert normalize_function_params(None, {"x": 1}) == {}


def test_unknown_keys_raise():
    """Keys not declared in the schema are rejected — typo catch."""
    schema = _schema_word_count()
    with pytest.raises(ValueError, match="Unknown function params"):
        normalize_function_params(schema, {"min_words": 1, "max_woooords": 5})


def test_non_dict_params_raise():
    with pytest.raises(ValueError, match="Invalid function parameter"):
        normalize_function_params(_schema_word_count(), ["not", "a", "dict"])


def test_min_max_bounds_still_enforced():
    schema = {
        "function_params_schema": {
            "k": {"type": "integer", "minimum": 1, "maximum": 100},
        }
    }
    with pytest.raises(ValueError, match=">= 1"):
        normalize_function_params(schema, {"k": 0})
    with pytest.raises(ValueError, match="<= 100"):
        normalize_function_params(schema, {"k": 101})
    # In-range value passes
    assert normalize_function_params(schema, {"k": 50}) == {"k": 50}


# ---------------------------------------------------------------------------
# Number type — FE form fields serialize numeric inputs as strings, so the
# normalizer must coerce. Regression coverage for TH-5022 (latency_check
# rejected a number entered as "1000" because the strict isinstance check
# only accepted int/float and never str).
# ---------------------------------------------------------------------------


def _schema_max_latency():
    return {
        "function_params_schema": {
            "max_latency_ms": {
                "type": "number",
                "default": None,
                "required": True,
                "minimum": 0,
            }
        }
    }


def test_number_accepts_native_int():
    assert normalize_function_params(_schema_max_latency(), {"max_latency_ms": 1000}) == {
        "max_latency_ms": 1000.0
    }


def test_number_accepts_native_float():
    assert normalize_function_params(
        _schema_max_latency(), {"max_latency_ms": 1500.5}
    ) == {"max_latency_ms": 1500.5}


def test_number_accepts_stringified_int():
    """FE serializes the form field as a string — must coerce."""
    assert normalize_function_params(
        _schema_max_latency(), {"max_latency_ms": "1000"}
    ) == {"max_latency_ms": 1000.0}


def test_number_accepts_stringified_float():
    assert normalize_function_params(
        _schema_max_latency(), {"max_latency_ms": "1500.5"}
    ) == {"max_latency_ms": 1500.5}


def test_number_accepts_stringified_with_whitespace():
    assert normalize_function_params(
        _schema_max_latency(), {"max_latency_ms": "  250  "}
    ) == {"max_latency_ms": 250.0}


def test_number_rejects_garbage_string():
    with pytest.raises(ValueError, match="max_latency_ms must be a number"):
        normalize_function_params(_schema_max_latency(), {"max_latency_ms": "fast"})


def test_number_rejects_bool():
    """Booleans are int subclasses in Python — must be rejected explicitly."""
    with pytest.raises(ValueError, match="max_latency_ms must be a number"):
        normalize_function_params(_schema_max_latency(), {"max_latency_ms": True})


def test_number_enforces_minimum():
    with pytest.raises(ValueError, match=">= 0"):
        normalize_function_params(_schema_max_latency(), {"max_latency_ms": -1})


def test_number_minimum_applies_after_string_coercion():
    """Bound check runs against the coerced float, not the raw string."""
    with pytest.raises(ValueError, match=">= 0"):
        normalize_function_params(_schema_max_latency(), {"max_latency_ms": "-50"})


def test_number_enforces_maximum():
    schema = {
        "function_params_schema": {
            "temperature": {"type": "number", "minimum": 0.0, "maximum": 2.0},
        }
    }
    with pytest.raises(ValueError, match="<= 2.0"):
        normalize_function_params(schema, {"temperature": "2.5"})
    assert normalize_function_params(schema, {"temperature": "1.5"}) == {
        "temperature": 1.5
    }
