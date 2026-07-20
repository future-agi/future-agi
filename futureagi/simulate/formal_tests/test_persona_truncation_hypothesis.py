"""
Hypothesis property tests for persona_first() — the single-value selector
that makes the multi-value drop visible via logger.warning.
"""

import importlib.util
import os
import sys
import types

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# Load persona_utils directly — no Django deps.
_MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "utils", "persona_utils.py")
)

# Stub structlog so the module loads cleanly outside Django.
if "structlog" not in sys.modules:
    _sl = types.ModuleType("structlog")
    _sl.get_logger = lambda *a, **kw: type("L", (), {
        "warning": staticmethod(lambda *a, **kw: None),
    })()
    sys.modules["structlog"] = _sl

_spec = importlib.util.spec_from_file_location("persona_utils", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

persona_first = _mod.persona_first

# ── Strategies ───────────────────────────────────────────────────────────────

text_list = st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=20)
nonempty_text_list = st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=20)
default_str = st.text(min_size=1, max_size=30)

# ── Properties ───────────────────────────────────────────────────────────────

@settings(max_examples=500)
@given(default=default_str)
def test_empty_list_returns_default(default):
    assert persona_first([], "field", default) == default


@settings(max_examples=500)
@given(default=default_str)
def test_none_input_returns_default(default):
    assert persona_first(None, "field", default) == default


@settings(max_examples=500)
@given(values=nonempty_text_list, default=default_str)
def test_nonempty_list_returns_first_element(values, default):
    assert persona_first(values, "field", default) == values[0]


@settings(max_examples=500)
@given(values=nonempty_text_list, default=default_str)
def test_never_returns_default_when_nonempty(values, default):
    result = persona_first(values, "field", default)
    assert result == values[0]


@settings(max_examples=200)
@given(
    values=st.lists(st.text(min_size=1, max_size=30), min_size=2, max_size=20),
    default=default_str,
)
def test_multi_value_returns_first_not_others(values, default):
    result = persona_first(values, "field", default)
    assert result == values[0]


@settings(max_examples=500)
@given(
    v=st.text(min_size=1, max_size=30),
    extra=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=10),
    default=default_str,
)
def test_adding_elements_does_not_change_result(v, extra, default):
    result_one = persona_first([v], "field", default)
    result_many = persona_first([v] + extra, "field", default)
    assert result_one == result_many


@settings(max_examples=500)
@given(values=text_list, default=default_str)
def test_result_is_always_str(values, default):
    result = persona_first(values, "field", default)
    assert isinstance(result, str)


@settings(max_examples=500)
@given(values=text_list, default=default_str)
def test_result_is_first_or_default(values, default):
    result = persona_first(values, "field", default)
    if values:
        assert result == values[0]
    else:
        assert result == default


@settings(max_examples=500)
@given(values=nonempty_text_list, default=default_str)
def test_idempotent(values, default):
    first_result = persona_first(values, "field", default)
    second_result = persona_first([first_result], "field", default)
    assert first_result == second_result


@settings(max_examples=200)
@given(values=nonempty_text_list, field=st.text(min_size=1, max_size=20), default=default_str)
def test_field_name_does_not_affect_result(values, field, default):
    result_a = persona_first(values, "field_a", default)
    result_b = persona_first(values, field, default)
    assert result_a == result_b
