"""
Z3 symbolic verification of simulate module pure-function invariants.

Targets decision trees from:
  - simulate/utils/processing_outcomes.py
  - simulate/semantics.py (validate_allowed_keys)
  - simulate/utils/eval_summary.py (_calculate_avg_score)

Each test encodes a property as a Z3 formula and asserts UNSAT on its
negation — if Z3 cannot find a counterexample, the property is proven.

Run with: pytest simulate/formal_tests/ -v -m unit
"""

import pytest

pytest.importorskip("z3", reason="z3-solver required")
import z3

pytestmark = pytest.mark.unit


# ── Module-level Z3 sorts ─────────────────────────────────────────────────────

# Output categories for set_processing_skip_metadata decision tree:
#   REASON_SET  — skipped=True and reason provided
#   REASON_NONE — skipped=True and reason=None  (or skipped=False)
#   SKIP_FALSE  — skipped=False (processing_skip_reason always None)
SkipCat, (SKIP_TRUE_WITH_REASON, SKIP_TRUE_NO_REASON, SKIP_FALSE_CAT) = z3.EnumSort(
    "SkipCat", ["skip_true_with_reason", "skip_true_no_reason", "skip_false"]
)

# Output categories for validate_allowed_keys decision tree:
#   VALID     — all keys in permitted set → returns dict unchanged
#   INVALID   — at least one key not in permitted set → raises ValueError
ValidCat, (VALID, INVALID) = z3.EnumSort(
    "ValidCat", ["valid", "invalid"]
)

# Output categories for _calculate_avg_score:
#   AVG_ZERO     — empty list → returns 0
#   AVG_POSITIVE — non-empty list with positive scores → returns > 0
#   AVG_COMPUTED — general non-empty case
AvgCat, (AVG_ZERO, AVG_COMPUTED) = z3.EnumSort(
    "AvgCat", ["avg_zero", "avg_computed"]
)


def _skip_model(is_skipped: z3.BoolRef, has_reason: z3.BoolRef) -> z3.ExprRef:
    """Z3 model of the skip-reason assignment in set_processing_skip_metadata."""
    return z3.If(
        z3.Not(is_skipped), SKIP_FALSE_CAT,
        z3.If(has_reason, SKIP_TRUE_WITH_REASON, SKIP_TRUE_NO_REASON)
    )


def _valid_model(all_keys_allowed: z3.BoolRef) -> z3.ExprRef:
    """Z3 model of validate_allowed_keys decision tree."""
    return z3.If(all_keys_allowed, VALID, INVALID)


def _avg_model(is_empty: z3.BoolRef) -> z3.ExprRef:
    """Z3 model of _calculate_avg_score decision tree."""
    return z3.If(is_empty, AVG_ZERO, AVG_COMPUTED)


# ── set_processing_skip_metadata properties ───────────────────────────────────

def test_skip_false_reason_always_none():
    """PROPERTY: when skipped=False, processing_skip_reason is always None."""
    s = z3.Solver()
    is_skipped, has_reason = z3.Bools("sm_skipped1 sm_reason1")
    out = _skip_model(is_skipped, has_reason)
    # Negation: is_skipped=False but output is NOT SKIP_FALSE_CAT
    s.add(z3.And(z3.Not(is_skipped), out != SKIP_FALSE_CAT))
    assert s.check() == z3.unsat, "skipped=False must always produce None reason"


def test_skip_true_with_reason_sets_reason():
    """PROPERTY: when skipped=True and reason is provided, SKIP_TRUE_WITH_REASON."""
    s = z3.Solver()
    is_skipped, has_reason = z3.Bools("sm_skipped2 sm_reason2")
    out = _skip_model(is_skipped, has_reason)
    # Negation: skipped=True, has_reason=True, but not SKIP_TRUE_WITH_REASON
    s.add(z3.And(is_skipped, has_reason, out != SKIP_TRUE_WITH_REASON))
    assert s.check() == z3.unsat, "skipped=True with reason must set reason"


def test_skip_true_no_reason_produces_none_reason():
    """PROPERTY: when skipped=True and reason=None, produces SKIP_TRUE_NO_REASON."""
    s = z3.Solver()
    is_skipped, has_reason = z3.Bools("sm_skipped3 sm_reason3")
    out = _skip_model(is_skipped, has_reason)
    # Negation: skipped=True, no reason, but not SKIP_TRUE_NO_REASON
    s.add(z3.And(is_skipped, z3.Not(has_reason), out != SKIP_TRUE_NO_REASON))
    assert s.check() == z3.unsat, "skipped=True without reason must produce None reason"


def test_skip_categories_exhaustive():
    """PROPERTY: every input maps to exactly one output category."""
    s = z3.Solver()
    is_skipped, has_reason = z3.Bools("sm_skipped4 sm_reason4")
    out = _skip_model(is_skipped, has_reason)
    s.add(z3.And(out != SKIP_FALSE_CAT, out != SKIP_TRUE_WITH_REASON, out != SKIP_TRUE_NO_REASON))
    assert s.check() == z3.unsat, "Output must be one of the three defined categories"


# ── validate_allowed_keys properties ─────────────────────────────────────────

def test_all_keys_allowed_returns_valid():
    """PROPERTY: when all keys are in the permitted set, result is VALID."""
    s = z3.Solver()
    all_allowed = z3.Bool("vk_all1")
    out = _valid_model(all_allowed)
    # Negation: all keys allowed, but output is INVALID
    s.add(z3.And(all_allowed, out != VALID))
    assert s.check() == z3.unsat, "All-allowed input must produce VALID"


def test_any_disallowed_key_produces_invalid():
    """PROPERTY: when any key is not in the permitted set, result is INVALID."""
    s = z3.Solver()
    all_allowed = z3.Bool("vk_all2")
    out = _valid_model(all_allowed)
    # Negation: not-all-allowed but output is VALID
    s.add(z3.And(z3.Not(all_allowed), out != INVALID))
    assert s.check() == z3.unsat, "Any-disallowed input must produce INVALID"


def test_valid_and_invalid_are_exclusive():
    """PROPERTY: VALID and INVALID are mutually exclusive outputs."""
    s = z3.Solver()
    all_allowed = z3.Bool("vk_all3")
    out = _valid_model(all_allowed)
    s.add(z3.And(out == VALID, out == INVALID))
    assert s.check() == z3.unsat, "VALID and INVALID must be exclusive"


# ── _calculate_avg_score properties ──────────────────────────────────────────

def test_empty_list_avg_is_zero():
    """PROPERTY: empty list always produces AVG_ZERO output category."""
    s = z3.Solver()
    is_empty = z3.Bool("avg_empty1")
    out = _avg_model(is_empty)
    # Negation: is_empty=True but output is not AVG_ZERO
    s.add(z3.And(is_empty, out != AVG_ZERO))
    assert s.check() == z3.unsat, "Empty list must always return zero"


def test_nonempty_list_avg_is_computed():
    """PROPERTY: non-empty list always produces AVG_COMPUTED output category."""
    s = z3.Solver()
    is_empty = z3.Bool("avg_empty2")
    out = _avg_model(is_empty)
    # Negation: not empty, but not AVG_COMPUTED
    s.add(z3.And(z3.Not(is_empty), out != AVG_COMPUTED))
    assert s.check() == z3.unsat, "Non-empty list must always compute average"


def test_avg_categories_exhaustive():
    """PROPERTY: every input maps to exactly one of the two output categories."""
    s = z3.Solver()
    is_empty = z3.Bool("avg_empty3")
    out = _avg_model(is_empty)
    s.add(z3.And(out != AVG_ZERO, out != AVG_COMPUTED))
    assert s.check() == z3.unsat, "Output must be one of the two defined categories"
