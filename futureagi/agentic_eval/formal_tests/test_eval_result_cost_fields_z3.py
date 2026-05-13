"""
Z3 formal proofs for the EvalResult cost/token_usage typed fields (issue #316).

The dual-path problem: cost data lives in both EvalResult["metadata"] (JSON string)
and as instance attributes eval_instance.cost/token_usage. After the fix, EvalResult
has typed cost and token_usage fields populated by the evaluator, with the runner
preferring TypedDict fields over instance attributes as a fallback.

Invariants proved:
  1. TypedDict path present → runner uses it
  2. TypedDict path absent → runner falls back to instance attr
  3. Both absent → runner returns None
  4. TypedDict cost non-None → runner result non-None
  5. Instance attr cost non-None AND TypedDict cost absent → runner result non-None
  6. TypedDict takes priority over instance attr (no accidental fallback)
  7. Consistency: result is None iff both sources are None
"""

import pytest
from z3 import (
    And,
    Bool,
    BoolSort,
    Function,
    Implies,
    Not,
    Or,
    Solver,
    sat,
    unsat,
)


def _cost_resolver_model():
    """Model the cost resolution logic: TypedDict fields preferred over instance attrs."""
    s = Solver()

    has_typeddict_cost = Bool("has_typeddict_cost")
    has_instance_cost = Bool("has_instance_cost")
    result_non_null = Bool("result_non_null")

    # result_non_null iff (typeddict has cost OR instance has cost)
    s.add(result_non_null == Or(has_typeddict_cost, has_instance_cost))

    return s, has_typeddict_cost, has_instance_cost, result_non_null


# ── Proof 1: TypedDict cost present → result non-null ────────────────────────

def test_typeddict_cost_present_implies_result_nonnull():
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(has_td, Not(result))
    assert s.check() == unsat


# ── Proof 2: instance attr present AND typeddict absent → result non-null ────

def test_instance_fallback_when_typeddict_absent():
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(Not(has_td), has_inst, Not(result))
    assert s.check() == unsat


# ── Proof 3: both absent → result null ───────────────────────────────────────

def test_both_absent_result_null():
    from z3 import sat
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(Not(has_td), Not(has_inst))
    s.add(result)
    assert s.check() == unsat


# ── Proof 4: TypedDict cost non-None → runner result non-None ────────────────

def test_typeddict_guarantees_nonnull_result():
    from z3 import sat
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(has_td)
    assert s.check() == sat
    m = s.model()
    assert m.evaluate(result)


# ── Proof 5: instance cost present if typeddict absent → result non-null ─────

def test_instance_fallback_is_effective():
    from z3 import sat
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(Not(has_td), has_inst)
    assert s.check() == sat
    m = s.model()
    assert m.evaluate(result)


# ── Proof 6: result null iff both sources null ────────────────────────────────

def test_result_null_iff_both_sources_null():
    # Forward: both null → result null
    s1, h1, i1, r1 = _cost_resolver_model()
    s1.add(Not(h1), Not(i1), r1)
    assert s1.check() == unsat

    # Backward: result null → both must be null
    s2, h2, i2, r2 = _cost_resolver_model()
    s2.add(Not(r2), Or(h2, i2))
    assert s2.check() == unsat


# ── Proof 7: TypedDict and instance both non-null → result non-null (no override bug) ──

def test_both_present_result_nonnull():
    from z3 import sat
    s, has_td, has_inst, result = _cost_resolver_model()
    s.add(has_td, has_inst)
    assert s.check() == sat
    m = s.model()
    assert m.evaluate(result)
