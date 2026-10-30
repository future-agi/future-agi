"""Unit tests for aggregate_call_status.

Covers the acceptance criteria of the "chat simulation status shows test
execution state instead of aggregated call states" fix: the overall status is
computed from individual call statuses by picking the least-advanced (earliest)
one, with a fallback when there are no calls.
"""

import pytest

from simulate.models.test_execution import CallExecution
from simulate.views.run_test import aggregate_call_status

Status = CallExecution.CallStatus

# Lifecycle order the aggregate must honor (earliest -> latest).
LIFECYCLE_ORDER = [
    Status.PENDING.value,  # "pending"
    Status.REGISTERED.value,  # "queued"
    Status.ONGOING.value,  # "ongoing"
    Status.ANALYZING.value,  # "analyzing"
    Status.COMPLETED.value,  # "completed"
    Status.FAILED.value,  # "failed"
    Status.CANCELLED.value,  # "cancelled"
]


@pytest.mark.unit
def test_mixed_statuses_report_earliest():
    # Some calls pending, some ongoing -> overall reflects the earliest state.
    assert (
        aggregate_call_status(
            [Status.ONGOING.value, Status.PENDING.value, Status.ONGOING.value],
            fallback=Status.COMPLETED.value,
        )
        == Status.PENDING.value
    )


@pytest.mark.unit
def test_uniform_statuses_report_that_status():
    assert (
        aggregate_call_status(
            [Status.ONGOING.value, Status.ONGOING.value],
            fallback=Status.PENDING.value,
        )
        == Status.ONGOING.value
    )


@pytest.mark.unit
def test_no_calls_uses_fallback():
    assert aggregate_call_status([], fallback=Status.COMPLETED.value) == (
        Status.COMPLETED.value
    )


@pytest.mark.unit
@pytest.mark.parametrize("status", LIFECYCLE_ORDER)
def test_single_status_returns_itself(status):
    # Every valid CallStatus value is handled and returned unchanged.
    assert aggregate_call_status([status], fallback="unused") == status


@pytest.mark.unit
def test_priority_ordering_is_pending_to_cancelled():
    # For each adjacent pair, the earlier status must win regardless of order.
    for i in range(len(LIFECYCLE_ORDER) - 1):
        earlier, later = LIFECYCLE_ORDER[i], LIFECYCLE_ORDER[i + 1]
        assert aggregate_call_status([later, earlier], fallback="unused") == earlier
        assert aggregate_call_status([earlier, later], fallback="unused") == earlier


@pytest.mark.unit
def test_unknown_status_does_not_mask_a_real_earlier_status():
    # A stray/unknown value sorts last, so a genuine earlier status still wins.
    assert (
        aggregate_call_status(
            ["some_unknown_state", Status.ONGOING.value], fallback="unused"
        )
        == Status.ONGOING.value
    )
