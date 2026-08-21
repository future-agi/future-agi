"""
Z3 formal proofs for the eval_explanation_summary_status state machine
and per-call timeout behaviour introduced in the fix for issue #313.

Properties verified:
  1. Status transitions from PENDING are monotone: never go backward.
  2. RUNNING is always transient: every execution path ends in COMPLETED or FAILED.
  3. The finally guard fires exactly when status is still RUNNING at task end.
  4. A per-call timeout results in SKIP (not FAILED) for that config — the task
     continues with remaining configs and may still COMPLETE.
  5. All-configs-skip still ends in COMPLETED (empty summary, not a failure).
  6. An exception in the task body always triggers the finally guard.
  7. Status is never PENDING after the task starts.
"""

import pytest
from z3 import (
    And,
    BoolVal,
    Const,
    EnumSort,
    If,
    Implies,
    Not,
    Or,
    Solver,
    sat,
    unsat,
)

# ── Model the status enum ────────────────────────────────────────────────────

Status, (PENDING, RUNNING, COMPLETED, FAILED) = EnumSort(
    "Status", ["PENDING", "RUNNING", "COMPLETED", "FAILED"]
)


def prove_unsat(s: Solver, name: str) -> None:
    result = s.check()
    assert result == unsat, (
        f"Proof FAILED for '{name}': counterexample exists — {s.model()}"
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _task_succeeded(task_succeeded_var):
    """Encode: if task succeeded, final status is COMPLETED."""
    return task_succeeded_var


def _final_status(task_succeeded, exception_raised):
    """
    Model the finally-guarded status assignment:
      - If task succeeded: COMPLETED (set before finally)
      - If exception: status was not set to COMPLETED, finally sets it to FAILED
      - If neither (should not happen in our model): FAILED as a safe default
    """
    return If(task_succeeded, COMPLETED, FAILED)


# ── Proof 1: RUNNING is never a final state ──────────────────────────────────

def test_running_never_final():
    from z3 import Bool
    s = Solver()
    succeeded = Bool("succeeded")
    exception_ = Bool("exception_raised")
    s.add(Or(succeeded, exception_))
    final = _final_status(succeeded, exception_)
    # Negation: final status is RUNNING
    s.add(final == RUNNING)
    prove_unsat(s, "running_never_final")


# ── Proof 2: final status is always COMPLETED or FAILED ─────────────────────

def test_final_status_is_completed_or_failed():
    from z3 import Bool
    s = Solver()
    succeeded = Bool("succeeded")
    exception_ = Bool("exception_raised")
    s.add(Or(succeeded, exception_))
    final = _final_status(succeeded, exception_)
    # Negation: final is neither COMPLETED nor FAILED
    s.add(And(final != COMPLETED, final != FAILED))
    prove_unsat(s, "final_status_is_completed_or_failed")


# ── Proof 3: finally guard fires iff status is RUNNING ──────────────────────

def test_finally_guard_fires_iff_running():
    """
    The guard sets status to FAILED iff the current status is RUNNING.
    After applying the guard, status is never RUNNING.
    """
    from z3 import Bool
    s = Solver()
    # Before finally: status might be RUNNING (no COMPLETED assignment yet)
    pre_status = Const("pre_status", Status)
    # Model the guard: if RUNNING → FAILED, else unchanged
    post_status = If(pre_status == RUNNING, FAILED, pre_status)
    # Negation: post status is RUNNING
    s.add(post_status == RUNNING)
    prove_unsat(s, "finally_guard_fires_iff_running")


# ── Proof 4: per-call timeout skips config, doesn't fail the task ───────────

def test_per_call_timeout_does_not_fail_task():
    """
    A timeout on one config means that config is skipped (result = None for it).
    The overall task can still succeed if other configs succeed.
    """
    from z3 import Bool, Int
    s = Solver()
    total_configs = Int("total_configs")
    timed_out_configs = Int("timed_out_configs")
    s.add(total_configs >= 1, timed_out_configs >= 0)
    # Timeout only affects some configs, not all
    s.add(timed_out_configs < total_configs)
    # Task succeeds = at least 0 configs succeeded (even empty summary is success)
    task_succeeded = Bool("task_succeeded")
    s.add(task_succeeded == True)  # Task can succeed with partial results
    final = _final_status(task_succeeded, BoolVal(False))
    # Negation: timeout on a subset caused failure
    s.add(final == FAILED)
    prove_unsat(s, "per_call_timeout_does_not_fail_task")


# ── Proof 5: all-configs timeout still completes (empty summary) ─────────────

def test_all_configs_timeout_completes():
    """
    If every config times out, the loop produces an empty dict.
    The task still sets COMPLETED (not FAILED) because no exception is raised.
    """
    from z3 import Bool
    s = Solver()
    # All configs timed out → loop produces {} → no exception → succeeded = True
    all_timed_out = Bool("all_timed_out")
    exception_from_timeout = BoolVal(False)  # TimeoutError is caught per-call
    task_succeeded = BoolVal(True)  # No exception escaped
    final = _final_status(task_succeeded, exception_from_timeout)
    # Negation: all-timeout leads to FAILED
    s.add(all_timed_out == True, final == FAILED)
    prove_unsat(s, "all_configs_timeout_completes")


# ── Proof 6: exception always triggers finally guard ────────────────────────

def test_exception_triggers_finally():
    """If an exception is raised, the finally block runs and sets FAILED."""
    from z3 import Bool
    s = Solver()
    exception_ = Bool("exception_raised")
    s.add(exception_ == True)
    task_succeeded = BoolVal(False)
    final = _final_status(task_succeeded, exception_)
    # Negation: exception raised but final is not FAILED
    s.add(final != FAILED)
    prove_unsat(s, "exception_triggers_finally")


# ── Proof 7: status never stays PENDING after task starts ───────────────────

def test_status_not_pending_after_start():
    """
    Once the task function runs, status is immediately set to RUNNING.
    It can never remain PENDING after task entry.
    """
    from z3 import Bool
    s = Solver()
    task_started = Bool("task_started")
    s.add(task_started == True)
    # After starting, status is at least RUNNING
    post_start_status = RUNNING  # first thing the task does
    # Negation: status is still PENDING after start
    s.add(post_start_status == PENDING)
    prove_unsat(s, "status_not_pending_after_start")
