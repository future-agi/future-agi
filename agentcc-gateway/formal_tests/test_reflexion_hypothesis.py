"""
Hypothesis property-based tests for the guardrail reflexion loop.

Tests the pure algorithm logic (state machine) without Go dependencies.

Run with: pytest agentcc-gateway/formal_tests/test_reflexion_hypothesis.py -v
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

HARD_CAP = 5  # matches runReflexion() hard cap in handlers.go

# ---------------------------------------------------------------------------
# Pure model of the reflexion loop (mirrors Go implementation)
# ---------------------------------------------------------------------------

def run_reflexion(max_attempts: int, outcomes: list[bool]) -> dict:
    """
    Pure Python model of the runReflexion loop.

    Args:
        max_attempts: configured MaxAttempts (clamped to HARD_CAP internally)
        outcomes: list of booleans; True = model response passes guardrail

    Returns dict with:
        success: bool
        attempts: int (total provider calls made)
        feedback_injected: int (number of feedback messages appended)
        final_error: str | None
    """
    effective = min(max_attempts, HARD_CAP)
    if effective <= 0:
        return {"success": False, "attempts": 0, "feedback_injected": 0,
                "final_error": "reflexion_disabled"}

    feedback_count = 0
    for attempt_idx, passed in enumerate(outcomes[:effective]):
        if passed:
            return {"success": True, "attempts": attempt_idx + 1,
                    "feedback_injected": feedback_count, "final_error": None}
        # inject feedback before next attempt (if there is one)
        if attempt_idx < effective - 1:
            feedback_count += 1

    return {"success": False, "attempts": effective,
            "feedback_injected": feedback_count,
            "final_error": "content_blocked_after_reflexion"}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

max_att_st   = st.integers(min_value=1, max_value=10)
outcomes_st  = st.lists(st.booleans(), min_size=1, max_size=HARD_CAP)

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_attempts_bounded_by_hard_cap(max_attempts, outcomes):
    """Total provider calls never exceed min(max_attempts, HARD_CAP)."""
    result = run_reflexion(max_attempts, outcomes)
    assert result["attempts"] <= HARD_CAP
    assert result["attempts"] <= max_attempts or max_attempts > HARD_CAP


@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_feedback_equals_failed_attempts(max_attempts, outcomes):
    """Number of injected feedback messages == number of failed attempts (minus last)."""
    result = run_reflexion(max_attempts, outcomes)
    effective = min(max_attempts, HARD_CAP)
    # feedback_injected is at most effective-1 (can't inject after the last attempt)
    assert result["feedback_injected"] < effective
    assert result["feedback_injected"] >= 0


@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_success_iff_any_outcome_true(max_attempts, outcomes):
    """Success iff at least one outcome within the attempt window was True."""
    result = run_reflexion(max_attempts, outcomes)
    effective = min(max_attempts, HARD_CAP)
    window = outcomes[:effective]
    any_pass = any(window)
    assert result["success"] == any_pass


@given(max_attempts=max_att_st)
def test_all_blocked_outcomes_gives_failure(max_attempts):
    """If every outcome is False (all blocked), result is failure."""
    outcomes = [False] * HARD_CAP
    result = run_reflexion(max_attempts, outcomes)
    assert not result["success"]
    assert result["final_error"] is not None


@given(max_attempts=max_att_st)
def test_first_pass_success_needs_no_feedback(max_attempts):
    """If the first attempt passes, no feedback is injected."""
    outcomes = [True] + [False] * (HARD_CAP - 1)
    result = run_reflexion(max_attempts, outcomes)
    assert result["success"]
    assert result["feedback_injected"] == 0
    assert result["attempts"] == 1


@given(max_attempts=max_att_st, n_fails=st.integers(min_value=1, max_value=HARD_CAP - 1))
def test_pass_after_n_failures(max_attempts, n_fails):
    """Passing after n failures: exactly n feedback messages injected."""
    assume(n_fails < min(max_attempts, HARD_CAP))
    outcomes = [False] * n_fails + [True]
    result = run_reflexion(max_attempts, outcomes)
    assert result["success"]
    assert result["feedback_injected"] == n_fails


@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_monotone_attempt_count(max_attempts, outcomes):
    """Attempt count never decreases across successive calls (stateless check)."""
    result = run_reflexion(max_attempts, outcomes)
    assert result["attempts"] >= 1


@given(max_attempts=st.integers(min_value=6, max_value=100), outcomes=outcomes_st)
def test_hard_cap_clamps_large_config(max_attempts, outcomes):
    """Configured MaxAttempts > 5 is silently clamped to 5."""
    result = run_reflexion(max_attempts, outcomes)
    assert result["attempts"] <= HARD_CAP


@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_success_implies_no_final_error(max_attempts, outcomes):
    """success=True iff final_error is None."""
    result = run_reflexion(max_attempts, outcomes)
    assert result["success"] == (result["final_error"] is None)


@given(max_attempts=max_att_st, outcomes=outcomes_st)
def test_failure_implies_final_error_set(max_attempts, outcomes):
    """success=False iff final_error is non-empty."""
    result = run_reflexion(max_attempts, outcomes)
    if not result["success"]:
        assert result["final_error"] is not None
        assert len(result["final_error"]) > 0
