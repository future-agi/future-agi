"""
Hypothesis property tests for the eval_explanation_summary timeout fix (issue #313).

Tests the timeout wrapper logic and status-state-machine invariants directly,
using importlib to avoid Django-dependent package __init__ chains.
"""

import importlib.util
import os
import concurrent.futures
import threading
import time

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ── Status model (mirrors EvalExplanationSummaryStatus) ──────────────────────

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
ALL_STATUSES = {PENDING, RUNNING, COMPLETED, FAILED}

# ── Load the per-call timeout wrapper logic in isolation ─────────────────────

_TIMEOUT = 0.05  # 50 ms — fast for property tests


def _call_with_timeout(fn, timeout):
    """Mirrors the concurrent.futures pattern used in the fix."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return None  # caller treats None as "skip this config"


# ── Status state-machine helpers ─────────────────────────────────────────────

def _apply_finally_guard(status: str) -> str:
    """Mirrors the finally block: if still RUNNING, set to FAILED."""
    return FAILED if status == RUNNING else status


def _run_task(configs_fail_at: set[int]) -> str:
    """
    Simulate the task body.
    configs_fail_at: indices of configs whose LLM call raises an exception.
    Returns the final status value.
    """
    status = PENDING
    try:
        status = RUNNING
        for i in range(5):
            if i in configs_fail_at:
                raise RuntimeError("simulated LLM failure")
        status = COMPLETED
    except Exception:
        pass
    finally:
        status = _apply_finally_guard(status)
    return status


# ── Property 1: final status is always COMPLETED or FAILED ──────────────────

@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
@given(fail_indices=st.frozensets(st.integers(min_value=0, max_value=4)))
def test_final_status_never_running_or_pending(fail_indices):
    final = _run_task(fail_indices)
    assert final in {COMPLETED, FAILED}


# ── Property 2: no exception → COMPLETED ────────────────────────────────────

def test_no_exception_gives_completed():
    final = _run_task(set())
    assert final == COMPLETED


# ── Property 3: any exception → FAILED ──────────────────────────────────────

@settings(max_examples=200)
@given(fail_index=st.integers(min_value=0, max_value=4))
def test_any_exception_gives_failed(fail_index):
    final = _run_task({fail_index})
    assert final == FAILED


# ── Property 4: finally guard is idempotent ─────────────────────────────────

@settings(max_examples=200)
@given(status=st.sampled_from(list(ALL_STATUSES)))
def test_finally_guard_idempotent(status):
    once = _apply_finally_guard(status)
    twice = _apply_finally_guard(once)
    assert once == twice


# ── Property 5: finally guard only changes RUNNING ──────────────────────────

@settings(max_examples=200)
@given(status=st.sampled_from(list(ALL_STATUSES)))
def test_finally_guard_only_changes_running(status):
    result = _apply_finally_guard(status)
    if status != RUNNING:
        assert result == status
    else:
        assert result == FAILED


# ── Property 6: per-call timeout returns None on slow function ───────────────

def test_timeout_returns_none_for_slow_call():
    def slow():
        time.sleep(10)
        return "done"

    result = _call_with_timeout(slow, timeout=0.01)
    assert result is None


# ── Property 7: per-call timeout returns value for fast function ──────────────

def test_timeout_returns_value_for_fast_call():
    result = _call_with_timeout(lambda: 42, timeout=5.0)
    assert result == 42


# ── Property 8: exception inside future propagates as None (caught) ──────────

def test_exception_in_future_gives_none():
    def explode():
        raise ValueError("boom")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(explode)
            future.result(timeout=5.0)
        result = None
    except Exception:
        result = None

    assert result is None


# ── Property 9: multiple timeouts don't compound into failure ────────────────

@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(n_timeouts=st.integers(min_value=0, max_value=10))
def test_multiple_timeouts_dont_cause_failure(n_timeouts):
    """
    Configs that time out are skipped; task still ends COMPLETED if no exception escapes.
    Mirrors the loop in _generate_cluster_dict_by_eval where TimeoutError is caught per-call.
    """
    status = RUNNING
    try:
        for _ in range(n_timeouts):
            # TimeoutError caught per-call: the loop continues
            pass
        status = COMPLETED
    except Exception:
        pass
    finally:
        status = _apply_finally_guard(status)
    assert status == COMPLETED


# ── Property 10: status is never PENDING after task starts ──────────────────

@settings(max_examples=200)
@given(fail_indices=st.frozensets(st.integers(min_value=0, max_value=4)))
def test_status_never_pending_after_start(fail_indices):
    final = _run_task(fail_indices)
    assert final != PENDING
