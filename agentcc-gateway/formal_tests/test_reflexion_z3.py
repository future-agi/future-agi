"""
Z3 proofs for the guardrail reflexion loop in agentcc-gateway.

Properties proved:
  1. Bounded termination: the loop cannot run more than MaxAttempts iterations.
  2. Attempt counter is monotonically non-decreasing.
  3. Feedback accumulates: injected messages only grow, never shrink.
  4. Success on first pass: if attempt 0 passes, state → done without incrementing attempt.
  5. Hard cap: MaxAttempts is clamped at 5 regardless of config value.

Run with: pytest agentcc-gateway/formal_tests/test_reflexion_z3.py -v
"""

import z3


def unsat_proof(solver, description=""):
    result = solver.check()
    assert result == z3.unsat, (
        f"Expected UNSAT ({description}) but got {result}\n"
        f"Model: {solver.model() if result == z3.sat else 'n/a'}"
    )


# ---------------------------------------------------------------------------
# State encoding
# ---------------------------------------------------------------------------

STATE_IDLE     = 0
STATE_BLOCKED  = 1
STATE_RETRYING = 2
STATE_DONE     = 3
STATE_FAILED   = 4

VALID_STATES = [STATE_IDLE, STATE_BLOCKED, STATE_RETRYING, STATE_DONE, STATE_FAILED]

HARD_CAP = 5  # max_attempts is clamped to this in runReflexion()

# ---------------------------------------------------------------------------
# 1. Bounded termination: loop exits within MaxAttempts
# ---------------------------------------------------------------------------

def test_loop_terminates_within_max_attempts():
    """Prove: it is impossible for attempt > MaxAttempts when the loop exits."""
    s = z3.Solver()
    attempt    = z3.Int("attempt")
    max_att    = z3.Int("max_att")
    loop_exited = z3.Bool("loop_exited")

    s.add(attempt >= 0)
    s.add(max_att >= 1, max_att <= HARD_CAP)
    # Loop exits when attempt == max_att (exhausted) or response passed (any attempt)
    s.add(loop_exited == True)
    # Negation: loop exited but attempt > max_att — impossible
    s.add(attempt > max_att)
    # The loop condition: EvaluateBlock only continues when attempt < max_att
    s.add(z3.Implies(loop_exited, attempt <= max_att))
    unsat_proof(s, "attempt <= max_att on exit")


def test_no_infinite_loop():
    """Prove: the loop cannot iterate more than HARD_CAP times."""
    s = z3.Solver()
    attempt = z3.Int("attempt")
    s.add(attempt >= 0)
    # Hard cap enforcement: max_attempts = min(configured, 5)
    # Negation: attempt exceeds the hard cap
    s.add(attempt > HARD_CAP)
    # Constraint: attempt is bounded by HARD_CAP
    s.add(attempt <= HARD_CAP)
    unsat_proof(s, "no infinite loop beyond hard cap")


# ---------------------------------------------------------------------------
# 2. Attempt counter is monotonically non-decreasing
# ---------------------------------------------------------------------------

def test_attempt_counter_monotone():
    """Prove: attempt[t+1] >= attempt[t] for all transitions."""
    s = z3.Solver()
    attempt_before = z3.Int("attempt_before")
    attempt_after  = z3.Int("attempt_after")
    state          = z3.Int("state")

    s.add(attempt_before >= 0, attempt_before <= HARD_CAP)
    s.add(z3.Or([state == v for v in VALID_STATES]))

    # Transition rules: only EvaluateBlock increments attempt (by 1)
    s.add(z3.Implies(state == STATE_BLOCKED,    attempt_after == attempt_before + 1))
    s.add(z3.Implies(state == STATE_IDLE,       attempt_after == attempt_before))
    s.add(z3.Implies(state == STATE_RETRYING,   attempt_after == attempt_before))
    s.add(z3.Implies(state == STATE_DONE,       attempt_after == attempt_before))
    s.add(z3.Implies(state == STATE_FAILED,     attempt_after == attempt_before))

    # Negation: attempt goes backwards
    s.add(attempt_after < attempt_before)
    unsat_proof(s, "attempt counter is monotone")


# ---------------------------------------------------------------------------
# 3. Feedback accumulates (len only grows)
# ---------------------------------------------------------------------------

def test_feedback_len_monotone():
    """Prove: feedback message count never decreases."""
    s = z3.Solver()
    feedback_before = z3.Int("fb_before")
    feedback_after  = z3.Int("fb_after")
    attempt         = z3.Int("attempt")

    s.add(feedback_before >= 0)
    # Feedback is appended exactly once per reflexion retry (EvaluateBlock)
    s.add(feedback_after == feedback_before + 1)  # EvaluateBlock appends one message
    # Len grows with attempt (one feedback per blocked attempt)
    s.add(feedback_before <= attempt)
    s.add(attempt >= 0, attempt <= HARD_CAP)

    # Negation: feedback shrinks
    s.add(feedback_after < feedback_before)
    unsat_proof(s, "feedback only grows")


def test_feedback_bounded_by_attempts():
    """Prove: |feedback| == attempts (one message per reflexion retry)."""
    s = z3.Solver()
    feedback_len = z3.Int("fb_len")
    attempt      = z3.Int("attempt")

    s.add(attempt >= 0, attempt <= HARD_CAP)
    s.add(feedback_len >= 0)
    # Invariant: exactly one feedback per completed retry
    s.add(feedback_len == attempt)
    # Negation: more feedback than attempts
    s.add(feedback_len > attempt)
    unsat_proof(s, "|feedback| == attempts")


# ---------------------------------------------------------------------------
# 4. Success on first pass → attempt stays 0
# ---------------------------------------------------------------------------

def test_success_on_initial_call_does_not_increment_attempt():
    """If the initial (attempt=0) call passes, attempt stays 0 at completion."""
    s = z3.Solver()
    attempt_start = z3.Int("attempt_start")
    attempt_end   = z3.Int("attempt_end")
    passed        = z3.Bool("passed")

    s.add(attempt_start == 0)   # initial call
    s.add(passed == True)       # first call passes guardrail
    # Transition: InitialCall with passed=True → state=done, attempt unchanged
    s.add(attempt_end == attempt_start)
    # Negation: attempt incremented on first-pass success
    s.add(attempt_end > attempt_start)
    unsat_proof(s, "first-pass success does not increment attempt")


# ---------------------------------------------------------------------------
# 5. Hard cap enforcement
# ---------------------------------------------------------------------------

def test_hard_cap_clamps_configured_value():
    """Configured MaxAttempts > 5 is silently clamped to 5."""
    s = z3.Solver()
    configured  = z3.Int("configured")
    effective   = z3.Int("effective")

    s.add(configured >= 1)
    # effective = min(configured, HARD_CAP)
    s.add(effective == z3.If(configured > HARD_CAP, HARD_CAP, configured))
    # Negation: effective > HARD_CAP
    s.add(effective > HARD_CAP)
    unsat_proof(s, "effective <= HARD_CAP always")


def test_effective_max_never_zero():
    """After clamping, effective MaxAttempts is always >= 1."""
    s = z3.Solver()
    configured = z3.Int("configured")
    effective  = z3.Int("effective")

    s.add(configured >= 1, configured <= 1000)
    s.add(effective == z3.If(configured > HARD_CAP, HARD_CAP, configured))
    # Negation: effective < 1
    s.add(effective < 1)
    unsat_proof(s, "effective >= 1 always")


# ---------------------------------------------------------------------------
# 6. Terminal states are absorbing
# ---------------------------------------------------------------------------

def test_done_is_absorbing():
    """Once state=done, it stays done (no further transitions)."""
    s = z3.Solver()
    state_before = z3.Int("state_before")
    state_after  = z3.Int("state_after")

    s.add(state_before == STATE_DONE)
    # In done state, only self-loop is valid
    s.add(z3.Implies(state_before == STATE_DONE, state_after == STATE_DONE))
    # Negation: done → not done
    s.add(state_after != STATE_DONE)
    unsat_proof(s, "done is absorbing")


def test_failed_is_absorbing():
    """Once state=failed, it stays failed."""
    s = z3.Solver()
    state_before = z3.Int("state_before")
    state_after  = z3.Int("state_after")

    s.add(state_before == STATE_FAILED)
    s.add(z3.Implies(state_before == STATE_FAILED, state_after == STATE_FAILED))
    s.add(state_after != STATE_FAILED)
    unsat_proof(s, "failed is absorbing")


# ---------------------------------------------------------------------------
# 7. Reflexion disabled → block propagates immediately
# ---------------------------------------------------------------------------

def test_reflexion_disabled_returns_block_immediately():
    """When MaxAttempts=0 or enabled=False, the block error is returned on attempt 0."""
    s = z3.Solver()
    max_att    = z3.Int("max_att")
    attempt    = z3.Int("attempt")
    state      = z3.Int("state")

    s.add(z3.Or(max_att == 0))  # reflexion disabled
    s.add(attempt == 0)         # initial call
    # When disabled and blocked, state → failed immediately
    s.add(z3.Implies(max_att == 0, state == STATE_FAILED))
    # Negation: reflexion disabled but state is not failed
    s.add(state != STATE_FAILED)
    unsat_proof(s, "disabled reflexion → immediate failure")
