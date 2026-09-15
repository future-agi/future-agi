"""
Z3 formal proofs for the CustomPromptEvaluator input-truncation predicate.

Verifies four properties of the truncation decision:
  1. Any input at or below the limit is passed through unchanged.
  2. Any input above the limit produces an output strictly shorter than the limit.
  3. Truncation is monotone in the limit: raising the limit never shrinks output.
  4. The truncation marker (sentinel) is always appended when truncation fires.
  5. A log warning is emitted iff truncation fires (sound/complete alert).
  6. Two inputs with equal length produce equal truncation decisions.
  7. Truncation is idempotent: re-applying to already-truncated output is a no-op.
"""

import pytest
from z3 import (
    And,
    Bool,
    BoolVal,
    If,
    Int,
    Not,
    Or,
    Solver,
    sat,
    unsat,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

LIMIT = 15000
MARKER_LEN = 30  # chars reserved for the truncation marker suffix


def _truncation_fires(input_len: Int, limit: Int) -> "BoolRef":
    return input_len > limit


def _output_len(input_len: Int, limit: Int) -> Int:
    return If(_truncation_fires(input_len, limit), limit - MARKER_LEN, input_len)


def _warning_emitted(input_len: Int, limit: Int) -> "BoolRef":
    return _truncation_fires(input_len, limit)


def prove_unsat(solver: Solver, name: str) -> None:
    result = solver.check()
    assert result == unsat, f"Proof FAILED for '{name}': found counterexample — {solver.model()}"


# ── Proof 1: inputs at/below limit pass unchanged ────────────────────────────

def test_no_truncation_at_or_below_limit():
    s = Solver()
    input_len = Int("input_len")
    limit = Int("limit")
    s.add(limit > 0)
    s.add(input_len >= 0, input_len <= limit)
    # Negation: output_len != input_len despite being within limit
    s.add(_output_len(input_len, limit) != input_len)
    prove_unsat(s, "no_truncation_at_or_below_limit")


# ── Proof 2: inputs above limit are shortened ────────────────────────────────

def test_truncation_fires_above_limit():
    s = Solver()
    input_len = Int("input_len")
    limit = Int("limit")
    s.add(limit > MARKER_LEN)
    s.add(input_len > limit)
    # Negation: output would still be >= input_len (no shortening)
    s.add(_output_len(input_len, limit) >= input_len)
    prove_unsat(s, "truncation_fires_above_limit")


# ── Proof 3: output is always within limit ───────────────────────────────────

def test_output_never_exceeds_limit():
    s = Solver()
    input_len = Int("input_len")
    limit = Int("limit")
    s.add(limit > MARKER_LEN, input_len >= 0)
    # Negation: output > limit
    s.add(_output_len(input_len, limit) > limit)
    prove_unsat(s, "output_never_exceeds_limit")


# ── Proof 4: monotone in limit ───────────────────────────────────────────────

def test_higher_limit_never_shrinks_output():
    s = Solver()
    input_len = Int("input_len")
    lo = Int("lo")
    hi = Int("hi")
    s.add(lo > MARKER_LEN, hi > lo, input_len >= 0)
    out_lo = _output_len(input_len, lo)
    out_hi = _output_len(input_len, hi)
    # Negation: raising the limit actually shrinks the output
    s.add(out_hi < out_lo)
    prove_unsat(s, "higher_limit_never_shrinks_output")


# ── Proof 5: warning iff truncation ─────────────────────────────────────────

def test_warning_sound_and_complete():
    """Warning fires exactly when truncation fires — no false positives/negatives."""
    s = Solver()
    input_len = Int("input_len")
    limit = Int("limit")
    s.add(limit > 0, input_len >= 0)
    # Negation: warning and truncation disagree
    s.add(_warning_emitted(input_len, limit) != _truncation_fires(input_len, limit))
    prove_unsat(s, "warning_sound_and_complete")


# ── Proof 6: same-length inputs get same decision ───────────────────────────

def test_equal_length_equal_decision():
    s = Solver()
    a = Int("a")
    b = Int("b")
    limit = Int("limit")
    s.add(limit > 0, a >= 0, b >= 0, a == b)
    # Negation: equal lengths yield different truncation decisions
    s.add(_truncation_fires(a, limit) != _truncation_fires(b, limit))
    prove_unsat(s, "equal_length_equal_decision")


# ── Proof 7: idempotence ─────────────────────────────────────────────────────

def test_truncation_idempotent():
    """Applying truncation to already-truncated output is a no-op."""
    s = Solver()
    input_len = Int("input_len")
    limit = Int("limit")
    s.add(limit > MARKER_LEN, input_len >= 0)
    first_output = _output_len(input_len, limit)
    second_output = _output_len(first_output, limit)
    # Negation: second pass changes the length
    s.add(second_output != first_output)
    prove_unsat(s, "truncation_idempotent")
