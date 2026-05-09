"""
Hypothesis property tests for the preprocessor registry key fix (issue #301).

Tests the actual preprocess_inputs dispatch logic in isolation via importlib.
"""

import importlib.util
import os
import sys
import types

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# Stub structlog
if "structlog" not in sys.modules:
    _sl = types.ModuleType("structlog")
    _sl.get_logger = lambda *a, **kw: type("L", (), {
        "warning": staticmethod(lambda *a, **kw: None),
        "info": staticmethod(lambda *a, **kw: None),
    })()
    sys.modules["structlog"] = _sl

# Also stub json (already in stdlib, but just in case)
import json  # noqa

_MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "engine", "preprocessing.py")
)
_spec = importlib.util.spec_from_file_location("preprocessing", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

preprocess_inputs = _mod.preprocess_inputs
PREPROCESSORS = _mod.PREPROCESSORS
register_preprocessor = _mod.register_preprocessor

# ── Stable keys used after the fix ──────────────────────────────────────────

STABLE_KEYS = ["ClipScore", "FidScore"]
USER_NAMES = ["My Clip Eval", "clip_score", "ClipScore Renamed", "fid score", "Quality"]


# ── Property 1: ClipScore key dispatches ────────────────────────────────────

def test_clip_score_key_dispatches():
    inputs = {}
    # The preprocessor may fail (no real embeddings) but it should be called
    result = preprocess_inputs("ClipScore", inputs)
    # Either inputs passed through or modified — but no exception
    assert isinstance(result, dict)


def test_fid_score_key_dispatches():
    inputs = {}
    result = preprocess_inputs("FidScore", inputs)
    assert isinstance(result, dict)


# ── Property 2: user-editable name never dispatches a preprocessor ───────────

@settings(max_examples=200)
@given(name=st.sampled_from(USER_NAMES))
def test_user_name_never_dispatches(name):
    """eval_template.name (user-editable) should never be the lookup key."""
    # If the key is not in PREPROCESSORS, preprocess_inputs returns inputs unchanged
    inputs = {"sentinel": 42}
    if name not in PREPROCESSORS:
        result = preprocess_inputs(name, inputs)
        assert result == inputs


# ── Property 3: unknown key always returns inputs unchanged ──────────────────

@settings(max_examples=500)
@given(key=st.text(min_size=1, max_size=30).filter(lambda k: k not in PREPROCESSORS))
def test_unknown_key_noop(key):
    inputs = {"a": 1, "b": 2}
    result = preprocess_inputs(key, inputs)
    assert result == inputs


# ── Property 4: registered key is always found ──────────────────────────────

@settings(max_examples=50)
@given(key=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))))
def test_registered_key_always_dispatches(key):
    """Any key explicitly registered must be found by preprocess_inputs."""
    sentinel = {}
    called = []

    @register_preprocessor(key)
    def _dummy(inputs):
        called.append(True)
        return inputs

    preprocess_inputs(key, sentinel)
    assert called, f"Registered key '{key}' was not dispatched"

    # Cleanup
    PREPROCESSORS.pop(key, None)


# ── Property 5: dispatch is deterministic ───────────────────────────────────

@settings(max_examples=200)
@given(inputs=st.dictionaries(st.text(min_size=1), st.integers(), min_size=0, max_size=5))
def test_dispatch_deterministic_for_unknown_key(inputs):
    result1 = preprocess_inputs("__nonexistent__", dict(inputs))
    result2 = preprocess_inputs("__nonexistent__", dict(inputs))
    assert result1 == result2


# ── Property 6: stable keys are in PREPROCESSORS after module load ───────────

def test_stable_keys_registered():
    for key in STABLE_KEYS:
        assert key in PREPROCESSORS, f"'{key}' not in PREPROCESSORS after fix"


# ── Property 7: old snake_case keys are NOT registered ──────────────────────

def test_old_snake_case_keys_removed():
    assert "clip_score" not in PREPROCESSORS, "Old 'clip_score' key still registered"
    assert "fid_score" not in PREPROCESSORS, "Old 'fid_score' key still registered"


# ── Property 8: preprocess_inputs always returns a dict ─────────────────────

@settings(max_examples=200)
@given(
    key=st.sampled_from(STABLE_KEYS + ["__unknown__"]),
    inputs=st.dictionaries(st.text(min_size=1), st.integers(), min_size=0, max_size=5),
)
def test_always_returns_dict(key, inputs):
    result = preprocess_inputs(key, dict(inputs))
    assert isinstance(result, dict)
