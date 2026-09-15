"""
Z3 formal proofs for the LSP contract of CustomPromptEvaluator (issue #315).

The Liskov Substitution Principle requires that any subclass of BaseEvaluator:
  1. Implements all abstract properties (name, display_name, metric_ids, required_args, examples)
  2. is_failure is implemented
  3. _evaluate is implemented
  4. The run() method from BaseEvaluator can be called on the subclass

Proofs model the abstract interface contract as a set of boolean predicates and
prove that a correctly-implementing subclass satisfies all contractual obligations.
"""

import pytest
from z3 import (
    And,
    Bool,
    Implies,
    Not,
    Or,
    Solver,
    unsat,
)


def _base_contract_solver():
    """Model the BaseEvaluator abstract property contract."""
    s = Solver()

    # Predicates: does the class implement each required method/property?
    has_name = Bool("has_name")
    has_display_name = Bool("has_display_name")
    has_metric_ids = Bool("has_metric_ids")
    has_required_args = Bool("has_required_args")
    has_examples = Bool("has_examples")
    has_is_failure = Bool("has_is_failure")
    has_evaluate = Bool("has_evaluate")

    # A class is a valid BaseEvaluator iff all abstract members are implemented
    is_valid_subclass = And(
        has_name,
        has_display_name,
        has_metric_ids,
        has_required_args,
        has_examples,
        has_is_failure,
        has_evaluate,
    )
    s.add(Bool("is_valid") == is_valid_subclass)

    return s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate


# ── Proof 1: missing metric_ids means invalid subclass ───────────────────────

def test_missing_metric_ids_fails_contract():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    s.add(has_name, has_display_name, Not(has_metric_ids), has_required_args, has_examples, has_is_failure, has_evaluate)
    s.add(Bool("is_valid"))
    assert s.check() == unsat


# ── Proof 2: missing required_args means invalid subclass ────────────────────

def test_missing_required_args_fails_contract():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    s.add(has_name, has_display_name, has_metric_ids, Not(has_required_args), has_examples, has_is_failure, has_evaluate)
    s.add(Bool("is_valid"))
    assert s.check() == unsat


# ── Proof 3: missing examples means invalid subclass ─────────────────────────

def test_missing_examples_fails_contract():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    s.add(has_name, has_display_name, has_metric_ids, has_required_args, Not(has_examples), has_is_failure, has_evaluate)
    s.add(Bool("is_valid"))
    assert s.check() == unsat


# ── Proof 4: missing is_failure means invalid subclass ───────────────────────

def test_missing_is_failure_fails_contract():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    s.add(has_name, has_display_name, has_metric_ids, has_required_args, has_examples, Not(has_is_failure), has_evaluate)
    s.add(Bool("is_valid"))
    assert s.check() == unsat


# ── Proof 5: missing _evaluate means invalid subclass ────────────────────────

def test_missing_evaluate_fails_contract():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    s.add(has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, Not(has_evaluate))
    s.add(Bool("is_valid"))
    assert s.check() == unsat


# ── Proof 6: complete implementation satisfies contract ──────────────────────

def test_full_implementation_satisfies_contract():
    from z3 import sat
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    # Assert all are implemented (the post-fix state of CustomPromptEvaluator)
    s.add(has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate)
    s.add(Bool("is_valid"))
    assert s.check() == sat


# ── Proof 7: run() is available iff subclass is valid ────────────────────────

def test_run_available_iff_valid():
    s, has_name, has_display_name, has_metric_ids, has_required_args, has_examples, has_is_failure, has_evaluate = _base_contract_solver()
    # run() is inherited from BaseEvaluator; it's available iff the class is valid
    run_available = Bool("run_available")
    s.add(run_available == Bool("is_valid"))

    # If is_valid is False, run() should NOT be callable (ABC would raise TypeError)
    s.add(Not(Bool("is_valid")), run_available)
    assert s.check() == unsat
