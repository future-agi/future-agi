"""
Hypothesis property-based tests for the schedule_auto_eval debounce protocol.

These tests exercise the pure scheduling logic extracted from auto_eval.py
without Django/Redis dependencies.  They complement the Z3 timing proofs
in test_eval_dag_z3.py and the TLA+ liveness spec in docs/tla/DatasetAutoEval.tla.
"""

from collections import defaultdict

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st


# ── Minimal in-process simulation of the debounce protocol ──────────────────

class FakeCache:
    """Thread-unsafe single-process stand-in for Django cache (sufficient here)."""

    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value, timeout=None):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)

    def add(self, key, value, timeout=None):
        """NX semantics: set only if absent.  Returns True if set."""
        if key in self._store:
            return False
        self._store[key] = value
        return True


def simulate_schedule(cache, config_id, row_ids, debounce_seconds):
    """
    Pure Python reimplementation of schedule_auto_eval's core logic,
    returning whether a flush was scheduled this call.
    """
    pending_key = f"auto_eval:{config_id}:pending"
    lock_key = f"auto_eval:{config_id}:lock"

    existing = cache.get(pending_key) or []
    cache.set(pending_key, existing + list(row_ids), timeout=debounce_seconds * 10)

    return cache.add(lock_key, "1", timeout=debounce_seconds * 2)


def simulate_flush(cache, config_id):
    """Returns deduplicated pending rows and clears state (like flush_auto_eval_batch)."""
    pending_key = f"auto_eval:{config_id}:pending"
    lock_key = f"auto_eval:{config_id}:lock"
    raw = cache.get(pending_key) or []
    cache.delete(pending_key)
    cache.delete(lock_key)
    return list(dict.fromkeys(raw))


# ── Properties ───────────────────────────────────────────────────────────────

row_id_st = st.text(min_size=1, max_size=32, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-")
batch_st = st.lists(row_id_st, min_size=1, max_size=20)


@given(
    config_id=st.uuids().map(str),
    batches=st.lists(batch_st, min_size=1, max_size=10),
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=200)
def test_no_row_lost_across_n_signals(config_id, batches, debounce):
    """
    Property: every row ID pushed before the flush fires appears in the flush output.
    Regardless of how many concurrent signals arrive, flush drains all of them.
    """
    cache = FakeCache()
    all_inserted = []
    flush_count = 0

    for batch in batches:
        scheduled = simulate_schedule(cache, config_id, batch, debounce)
        all_inserted.extend(batch)
        if scheduled:
            flush_count += 1  # first signal in window schedules flush

    # Simulate the flush firing once.
    flushed = simulate_flush(cache, config_id)

    assert set(flushed) == set(all_inserted), (
        f"Rows lost: {set(all_inserted) - set(flushed)}"
    )


@given(
    config_id=st.uuids().map(str),
    row_ids=st.lists(row_id_st, min_size=1, max_size=50),
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=200)
def test_exactly_one_flush_scheduled_per_window(config_id, row_ids, debounce):
    """
    Property: within a single debounce window, cache.add NX ensures at most
    one flush is armed, regardless of how many signals arrive.
    """
    cache = FakeCache()
    flush_scheduled = 0
    for row_id in row_ids:
        if simulate_schedule(cache, config_id, [row_id], debounce):
            flush_scheduled += 1

    assert flush_scheduled == 1, (
        f"Expected exactly 1 flush scheduled, got {flush_scheduled}"
    )


@given(
    config_id=st.uuids().map(str),
    row_ids=st.lists(row_id_st, min_size=2, max_size=30),
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=200)
def test_deduplication_preserves_uniqueness(config_id, row_ids, debounce):
    """
    Property: flush output contains no duplicate row IDs even when the same
    row_id is pushed multiple times (simulating retried signals).
    """
    cache = FakeCache()
    doubled = row_ids + row_ids  # push every ID twice
    simulate_schedule(cache, config_id, doubled, debounce)
    flushed = simulate_flush(cache, config_id)

    assert len(flushed) == len(set(flushed)), "Duplicates in flush output"
    assert set(flushed) == set(row_ids)


@given(
    config_id=st.uuids().map(str),
    first_batch=batch_st,
    requeued=batch_st,
    new_batch=batch_st,
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=200)
def test_requeue_after_workflow_failure_no_loss(config_id, first_batch, requeued, new_batch, debounce):
    """
    Property: after a workflow failure, re-queued rows + newly arrived rows
    are all captured by the next flush window.  Mirrors TLA+ WorkflowFail action.
    """
    cache = FakeCache()

    # First window: schedule and flush
    simulate_schedule(cache, config_id, first_batch, debounce)
    simulate_flush(cache, config_id)

    # Workflow fails — requeue those rows (opens new window)
    simulate_schedule(cache, config_id, requeued, debounce)

    # New rows arrive in same second window
    simulate_schedule(cache, config_id, new_batch, debounce)

    flushed = simulate_flush(cache, config_id)
    expected = set(requeued) | set(new_batch)
    assert set(flushed) == expected, (
        f"Rows missing after requeue: {expected - set(flushed)}"
    )


@given(
    config_id=st.uuids().map(str),
    windows=st.lists(batch_st, min_size=2, max_size=5),
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=100)
def test_successive_windows_are_independent(config_id, windows, debounce):
    """
    Property: rows from window N never appear in window N+1's flush.
    The lock clears on flush, so each window is independent.
    """
    cache = FakeCache()
    for window_rows in windows:
        simulate_schedule(cache, config_id, window_rows, debounce)
        flushed = simulate_flush(cache, config_id)
        # Next window starts fresh — pending list must be empty after flush
        assert cache.get(f"auto_eval:{config_id}:pending") is None


@given(
    config_id=st.uuids().map(str),
    row_ids=batch_st,
    debounce=st.integers(min_value=5, max_value=3600),
)
@settings(max_examples=200)
def test_flush_of_empty_list_is_safe(config_id, row_ids, debounce):
    """
    Property: flushing with nothing pending returns [] without raising.
    """
    cache = FakeCache()
    # Do not schedule anything — just flush
    result = simulate_flush(cache, config_id)
    assert result == []
