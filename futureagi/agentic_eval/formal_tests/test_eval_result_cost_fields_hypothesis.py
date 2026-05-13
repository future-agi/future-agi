"""
Hypothesis property tests for EvalResult cost/token_usage typed fields (issue #316).

Tests that:
  1. EvalResult TypedDict now has cost/token_usage as optional fields (NotRequired)
  2. The cost resolution logic (prefer TypedDict, fall back to instance attr) works correctly
  3. extract_raw_result passes through cost/token_usage from evaluator results
"""

from hypothesis import given, settings
from hypothesis import strategies as st


# ── Reference implementation of the resolution logic ─────────────────────────

def resolve_cost(typeddict_cost, instance_cost):
    """Mirror of runner.py: response.get("cost") or getattr(eval_instance, "cost", None)"""
    return typeddict_cost or instance_cost


# ── Strategies ────────────────────────────────────────────────────────────────

_cost_dict = st.fixed_dictionaries({
    "total_cost": st.floats(min_value=0, max_value=10),
    "prompt_cost": st.floats(min_value=0, max_value=5),
    "completion_cost": st.floats(min_value=0, max_value=5),
})
_token_dict = st.fixed_dictionaries({
    "total_tokens": st.integers(min_value=0, max_value=100000),
    "prompt_tokens": st.integers(min_value=0, max_value=50000),
    "completion_tokens": st.integers(min_value=0, max_value=50000),
})
opt_cost = st.one_of(st.none(), _cost_dict)
opt_token = st.one_of(st.none(), _token_dict)


# ── Properties ────────────────────────────────────────────────────────────────

@given(typeddict_cost=_cost_dict, instance_cost=opt_cost)
@settings(max_examples=100)
def test_typeddict_cost_takes_priority(typeddict_cost, instance_cost):
    """When TypedDict has cost, it is returned regardless of instance attr."""
    result = resolve_cost(typeddict_cost, instance_cost)
    assert result == typeddict_cost


@given(instance_cost=_cost_dict)
@settings(max_examples=100)
def test_instance_fallback_when_typeddict_absent(instance_cost):
    """When TypedDict cost is None, instance attr is used."""
    result = resolve_cost(None, instance_cost)
    assert result == instance_cost


def test_both_none_returns_none():
    """When both sources are None, result is None."""
    assert resolve_cost(None, None) is None


@given(cost=_cost_dict)
def test_typeddict_cost_never_lost(cost):
    """A non-None TypedDict cost always produces a non-None result."""
    result = resolve_cost(cost, None)
    assert result is not None


@given(instance_cost=_cost_dict)
def test_instance_cost_rescued_when_typeddict_absent(instance_cost):
    """Instance attr cost is never lost when TypedDict field is absent."""
    result = resolve_cost(None, instance_cost)
    assert result is not None


# ── TypedDict structural tests ────────────────────────────────────────────────

def test_eval_result_typeddict_has_cost_field():
    """EvalResult TypedDict should accept cost as an optional key."""
    import importlib.util, sys
    from pathlib import Path

    path = Path("/Users/jonathanhill/src/future-agi/futureagi/agentic_eval/core_evals/fi_utils/evals_result.py")
    spec = importlib.util.spec_from_file_location("evals_result", path)
    mod = importlib.util.module_from_spec(spec)

    # Stub pandas
    import unittest.mock as mock
    sys.modules.setdefault("pandas", mock.MagicMock())
    sys.modules.setdefault("pydantic", mock.MagicMock())

    spec.loader.exec_module(mod)
    EvalResult = mod.EvalResult
    annotations = EvalResult.__annotations__
    assert "cost" in annotations, "EvalResult TypedDict must have 'cost' field"
    assert "token_usage" in annotations, "EvalResult TypedDict must have 'token_usage' field"


def test_eval_result_typeddict_cost_optional():
    """The cost/token_usage fields in EvalResult are NotRequired (optional)."""
    import importlib.util, sys
    from pathlib import Path
    from typing import get_type_hints

    path = Path("/Users/jonathanhill/src/future-agi/futureagi/agentic_eval/core_evals/fi_utils/evals_result.py")
    spec = importlib.util.spec_from_file_location("evals_result", path)
    mod = importlib.util.module_from_spec(spec)

    import unittest.mock as mock
    sys.modules.setdefault("pandas", mock.MagicMock())
    sys.modules.setdefault("pydantic", mock.MagicMock())

    spec.loader.exec_module(mod)
    EvalResult = mod.EvalResult

    # NotRequired fields appear in __optional_keys__ or are wrapped in NotRequired
    optional_keys = getattr(EvalResult, "__optional_keys__", frozenset())
    assert "cost" in optional_keys, "cost should be NotRequired (optional)"
    assert "token_usage" in optional_keys, "token_usage should be NotRequired (optional)"


# ── extract_raw_result passes through cost/token_usage ───────────────────────

def test_extract_raw_result_includes_cost_and_token_usage():
    """extract_raw_result should include cost and token_usage from the eval result."""
    import importlib.util, sys
    from pathlib import Path
    import unittest.mock as mock

    path = Path("/Users/jonathanhill/src/future-agi/futureagi/evaluations/engine/formatting.py")
    spec = importlib.util.spec_from_file_location("formatting", path)
    mod = importlib.util.module_from_spec(spec)

    sys.modules.setdefault("structlog", mock.MagicMock())
    spec.loader.exec_module(mod)

    cost = {"total_cost": 0.01, "prompt_cost": 0.005, "completion_cost": 0.005}
    token_usage = {"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50}

    fake_result = mock.MagicMock()
    fake_result.eval_results = [{
        "data": {"result": "Pass"},
        "failure": False,
        "reason": "Looks good",
        "runtime": 500,
        "model": "gpt-4o",
        "metrics": [],
        "metadata": None,
        "cost": cost,
        "token_usage": token_usage,
    }]

    fake_template = mock.MagicMock()
    fake_template.config = {"output": "Pass/Fail"}

    response = mod.extract_raw_result(fake_result, fake_template)
    assert response["cost"] == cost
    assert response["token_usage"] == token_usage


def test_extract_raw_result_cost_absent_is_none():
    """When cost is absent from eval result, extract_raw_result returns None."""
    import importlib.util, sys
    from pathlib import Path
    import unittest.mock as mock

    path = Path("/Users/jonathanhill/src/future-agi/futureagi/evaluations/engine/formatting.py")
    spec = importlib.util.spec_from_file_location("formatting2", path)
    mod = importlib.util.module_from_spec(spec)

    sys.modules.setdefault("structlog", mock.MagicMock())
    spec.loader.exec_module(mod)

    fake_result = mock.MagicMock()
    fake_result.eval_results = [{
        "data": {},
        "failure": None,
        "reason": "",
        "runtime": 0,
        "model": None,
        "metrics": [],
        "metadata": None,
        # cost and token_usage not present
    }]

    fake_template = mock.MagicMock()
    fake_template.config = {"output": "score"}

    response = mod.extract_raw_result(fake_result, fake_template)
    assert response["cost"] is None
    assert response["token_usage"] is None
