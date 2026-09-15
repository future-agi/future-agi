"""Regression tests for execute_with_concurrent_future_pool.

The helper runs tasks in a thread pool. Callers zip its output back to the
input list by index, so the returned list must stay in input order and keep a
slot for every input, even when tasks finish out of order or raise.
"""

import time

from agentic_eval.core.utils.functions import execute_with_concurrent_future_pool


def test_results_follow_input_order_not_completion_order():
    # Earlier indices sleep longer, so tasks complete in reverse order.
    # A completion-ordered implementation would return [4, 3, 2, 1, 0].
    def slow_identity(i):
        time.sleep((5 - i) * 0.02)
        return i

    args_list = [(i,) for i in range(5)]
    results = execute_with_concurrent_future_pool(slow_identity, args_list, pool_size=5)

    assert results == [0, 1, 2, 3, 4]


def test_failed_task_keeps_its_slot_no_silent_drop():
    # Index 2 raises. The output must keep full length and hold None there,
    # rather than silently dropping the row and misaligning everything after.
    def maybe_raise(i):
        if i == 2:
            raise ValueError("boom")
        return i * 10

    args_list = [(i,) for i in range(5)]
    results = execute_with_concurrent_future_pool(maybe_raise, args_list, pool_size=5)

    assert len(results) == len(args_list)
    assert results[2] is None
    assert results == [0, 10, None, 30, 40]
