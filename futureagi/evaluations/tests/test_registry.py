"""
Registry unit tests + Hypothesis invariants.

Tests the registry contract from docs/adr/005-registry-lazy-singleton.md:
- get_eval_class raises ValueError xor returns a class (never None)
- is_registered iff get_eval_class succeeds
- list_registered covers all registered names
- Registry is idempotent (multiple builds produce the same state)

Run with: pytest evaluations/tests/test_registry.py -v -m unit
"""

import sys
import types
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.unit


class _FakeEvalA:
    pass


class _FakeEvalB:
    pass


_FAKE_EVALS_MODULE = types.ModuleType("agentic_eval.core_evals.fi_evals")
_FAKE_EVALS_MODULE.__all__ = ["_FakeEvalA", "_FakeEvalB"]
_FAKE_EVALS_MODULE._FakeEvalA = _FakeEvalA
_FAKE_EVALS_MODULE._FakeEvalB = _FakeEvalB


def _reset_registry():
    import evaluations.engine.registry as reg
    reg._REGISTRY.clear()
    reg._BUILT = False


@pytest.fixture(autouse=True)
def fresh_registry():
    """Each test gets a clean registry backed by the fake fi_evals module."""
    _reset_registry()
    with patch.dict(sys.modules, {
        "agentic_eval": types.ModuleType("agentic_eval"),
        "agentic_eval.core_evals": types.ModuleType("agentic_eval.core_evals"),
        "agentic_eval.core_evals.fi_evals": _FAKE_EVALS_MODULE,
    }):
        yield
    _reset_registry()


# ── Contract tests ────────────────────────────────────────────────────────────

def test_get_eval_class_raises_for_unknown_type():
    """get_eval_class raises ValueError for unknown type IDs, never returns None."""
    from evaluations.engine.registry import get_eval_class

    with pytest.raises(ValueError, match="Unknown evaluator type"):
        get_eval_class("DoesNotExist_XYZ")


def test_get_eval_class_never_returns_none():
    """get_eval_class contract: returns a class or raises, never None."""
    from evaluations.engine.registry import get_eval_class

    result = None
    raised = False
    try:
        result = get_eval_class("DoesNotExist_XYZ")
    except ValueError:
        raised = True

    assert raised or result is not None, "get_eval_class returned None without raising"


def test_is_registered_and_get_class_are_consistent():
    """is_registered(name) iff get_eval_class(name) succeeds — no split-brain."""
    from evaluations.engine.registry import get_eval_class, is_registered, list_registered

    names = list_registered()
    assert names == ["_FakeEvalA", "_FakeEvalB"]
    for name in names:
        assert is_registered(name), f"{name} in list_registered but not is_registered"
        cls = get_eval_class(name)
        assert cls is not None, f"get_eval_class({name!r}) returned None"


def test_is_registered_false_for_unknown():
    from evaluations.engine.registry import is_registered

    assert not is_registered("DefinitelyNotReal_ABC123")


def test_list_registered_covers_all_registered():
    """Every name accessible via get_eval_class appears in list_registered."""
    from evaluations.engine.registry import _REGISTRY, list_registered

    listed = set(list_registered())
    actual = set(_REGISTRY.keys())
    assert listed == actual


def test_registry_built_once():
    """_BUILT flag prevents re-building on repeated calls."""
    import evaluations.engine.registry as reg

    reg.list_registered()  # trigger build
    assert reg._BUILT

    original_count = len(reg._REGISTRY)
    reg.list_registered()  # second call — no rebuild
    assert len(reg._REGISTRY) == original_count


def test_registry_import_failure_propagates():
    """If fi_evals cannot be imported, registry raises — never returns empty silently."""
    import evaluations.engine.registry as reg

    with patch.dict("sys.modules", {"agentic_eval.core_evals.fi_evals": None}):
        _reset_registry()
        with pytest.raises((ImportError, TypeError, Exception)):
            reg.list_registered()


# ── Hypothesis: registry contract holds for all registered names ──────────────

@given(suffix=st.text(min_size=1, max_size=30).filter(str.isidentifier))
@settings(max_examples=100)
def test_unregistered_names_always_raise(suffix):
    """
    For any name not in the registry, get_eval_class must raise ValueError.
    (We construct names unlikely to collide with real evaluators.)
    """
    from evaluations.engine.registry import get_eval_class, is_registered

    name = f"Nonexistent_{suffix}_ZZZ"
    if is_registered(name):
        return  # extremely unlikely collision — skip

    with pytest.raises(ValueError):
        get_eval_class(name)


@given(st.data())
@settings(max_examples=50)
def test_registered_names_always_return_class(data):
    """For any registered name, get_eval_class always returns a callable class."""
    from evaluations.engine.registry import get_eval_class, list_registered

    names = list_registered()
    if not names:
        return

    name = data.draw(st.sampled_from(names))
    cls = get_eval_class(name)

    assert cls is not None
    assert callable(cls), f"get_eval_class({name!r}) returned non-callable: {cls!r}"
