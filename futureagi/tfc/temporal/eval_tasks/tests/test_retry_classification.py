"""Pure workflow-side classification for exhausted reconcile activities."""

import pytest
from temporalio.exceptions import ActivityError, ApplicationError

from tfc.temporal.eval_tasks.types import ContinuousDrainState
from tfc.temporal.eval_tasks.workflows import (
    _CONTINUOUS_RECONCILE_BUDGET_DEFERRAL_PATCH,
    ContinuousEvalTaskWorkflow,
    _is_retryable_reconcile_budget_error,
    _reconcile_continuous,
)


def _activity_error(cause: Exception) -> ActivityError:
    error = ActivityError(
        "reconcile failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="reconcile_eval_task_activity",
        activity_id="activity-id",
        retry_state=None,
    )
    error.__cause__ = cause
    return error


def test_only_retryable_read_budget_activity_error_is_deferred() -> None:
    assert _is_retryable_reconcile_budget_error(
        _activity_error(
            ApplicationError(
                "temporary CH pressure",
                type="EvalTaskReadBudgetExceeded",
                non_retryable=False,
            )
        )
    )


def test_deterministic_selection_rejection_is_not_deferred() -> None:
    assert not _is_retryable_reconcile_budget_error(
        _activity_error(
            ApplicationError(
                "unsupported filter",
                type="EvalTaskSelectionRejected",
                non_retryable=True,
            )
        )
    )


def test_wrong_failure_shapes_are_not_deferred() -> None:
    assert not _is_retryable_reconcile_budget_error(RuntimeError("programming error"))
    assert not _is_retryable_reconcile_budget_error(
        _activity_error(ApplicationError("other", type="OtherError"))
    )


@pytest.mark.asyncio
async def test_new_history_records_patch_and_defers_retryable_budget(
    monkeypatch,
) -> None:
    import tfc.temporal.eval_tasks.workflows as workflows

    failure = _activity_error(
        ApplicationError(
            "temporary CH pressure",
            type="EvalTaskReadBudgetExceeded",
            non_retryable=False,
        )
    )
    patch_ids: list[str] = []

    async def fail_reconcile(_task_id: str) -> None:
        raise failure

    def patched(patch_id: str) -> bool:
        patch_ids.append(patch_id)
        return True

    monkeypatch.setattr(workflows, "_reconcile", fail_reconcile)
    monkeypatch.setattr(workflows.workflow, "patched", patched)

    assert await _reconcile_continuous("task-id") is False
    assert patch_ids == [_CONTINUOUS_RECONCILE_BUDGET_DEFERRAL_PATCH]


@pytest.mark.asyncio
async def test_legacy_history_without_patch_preserves_terminal_failure(
    monkeypatch,
) -> None:
    import tfc.temporal.eval_tasks.workflows as workflows

    failure = _activity_error(
        ApplicationError(
            "temporary CH pressure",
            type="EvalTaskReadBudgetExceeded",
            non_retryable=False,
        )
    )

    async def fail_reconcile(_task_id: str) -> None:
        raise failure

    monkeypatch.setattr(workflows, "_reconcile", fail_reconcile)
    monkeypatch.setattr(workflows.workflow, "patched", lambda _patch_id: False)

    with pytest.raises(ActivityError) as captured:
        await _reconcile_continuous("task-id")
    assert captured.value is failure


@pytest.mark.asyncio
async def test_eventual_reconcile_success_reaps_before_first_claim(monkeypatch) -> None:
    """Initial deferral cannot drain; recovery reaps crash leftovers once."""

    import tfc.temporal.eval_tasks.workflows as workflows

    events: list[str] = []
    reconcile_results = iter((False, True))

    async def record_async(name: str):
        events.append(name)

    async def reconcile(_task_id: str) -> bool:
        result = next(reconcile_results)
        events.append(f"reconcile:{result}")
        return result

    async def task_state(_task_id: str) -> dict:
        events.append("state")
        return {"active": True, "status": "running"}

    async def claim(_task_id: str, _n: int) -> dict:
        events.append("claim")
        raise RuntimeError("stop after proving claim order")

    async def sleep(_self, _seconds: int) -> None:
        events.append("sleep")

    monkeypatch.setattr(
        workflows, "_apply_labels", lambda _task_id: record_async("labels")
    )
    monkeypatch.setattr(
        workflows, "_set_status", lambda _status: events.append("status")
    )
    monkeypatch.setattr(
        workflows, "_mark_running", lambda _task_id: record_async("mark")
    )
    monkeypatch.setattr(workflows, "_reconcile_continuous", reconcile)
    monkeypatch.setattr(
        workflows, "_reap", lambda _task_id, **_kwargs: record_async("reap")
    )
    monkeypatch.setattr(workflows, "_task_state", task_state)
    monkeypatch.setattr(workflows, "_claim", claim)
    monkeypatch.setattr(ContinuousEvalTaskWorkflow, "_sleep_or_recheck", sleep)

    workflow = ContinuousEvalTaskWorkflow()
    with pytest.raises(RuntimeError, match="stop after proving claim order"):
        await workflow._run(ContinuousDrainState(task_id="task-id"))

    assert events == [
        "labels",
        "status",
        "mark",
        "reconcile:False",
        "state",
        "sleep",
        "state",
        "reconcile:True",
        "state",
        "reap",
        "claim",
    ]


def _backoff_seconds(policy) -> float:
    """Total wait across every retry the policy allows."""
    total, delay = 0.0, policy.initial_interval.total_seconds()
    for _ in range(policy.maximum_attempts - 1):
        total += min(delay, policy.maximum_interval.total_seconds())
        delay *= policy.backoff_coefficient
    return total


def test_control_retries_outlast_a_database_outage() -> None:
    """One exhausted control activity fails the whole workflow, so the retry
    window must cover a connection-pooler restart or a database failover."""
    from tfc.temporal.eval_tasks.workflows import CONTROL_RETRY_POLICY

    assert _backoff_seconds(CONTROL_RETRY_POLICY) >= 5 * 60


def test_reconcile_keeps_its_short_retry_window() -> None:
    """Reconcile reads ClickHouse; longer retries there only add read pressure."""
    from tfc.temporal.eval_tasks.workflows import RECONCILE_RETRY_POLICY

    assert _backoff_seconds(RECONCILE_RETRY_POLICY) <= 60


def test_production_attempt_counts_are_pinned() -> None:
    """The outage tests shorten only the timers and read these counts from the
    policies, so a changed count would pass them silently. Pin them here."""
    from tfc.temporal.eval_tasks.workflows import (
        CONTROL_RETRY_POLICY,
        RECONCILE_RETRY_POLICY,
    )

    assert CONTROL_RETRY_POLICY.maximum_attempts == 12
    assert RECONCILE_RETRY_POLICY.maximum_attempts == 5


def _claim_error(error_type: str, non_retryable: bool) -> ActivityError:
    error = ActivityError(
        "claim failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="claim_eval_batch_activity",
        activity_id="activity-id",
        retry_state=None,
    )
    error.__cause__ = ApplicationError(
        "synthetic", type=error_type, non_retryable=non_retryable
    )
    return error


def test_only_a_non_retryable_lost_claim_reply_is_an_unknown_outcome() -> None:
    from tfc.temporal.eval_tasks.workflows import _is_claim_outcome_unknown

    assert _is_claim_outcome_unknown(_claim_error("EvalTaskClaimOutcomeUnknown", True))
    assert not _is_claim_outcome_unknown(
        _claim_error("EvalTaskClaimOutcomeUnknown", False)
    )
    assert not _is_claim_outcome_unknown(
        _claim_error("EvalTaskDatabaseUnavailable", False)
    )
    assert not _is_claim_outcome_unknown(ApplicationError("x", non_retryable=True))


def test_lost_claim_reply_waits_are_bounded_and_kept_apart_from_idle_waits() -> None:
    """Twelve lost replies in a row, ten-minute waits each, then the drain fails.

    The marker and the cap decide between sleeping and raising, so changing
    either needs a new ``workflow.patched`` marker.
    """
    from tfc.temporal.eval_tasks.workflows import (
        _HISTORICAL_CLAIM_OUTCOME_UNKNOWN_PATCH,
        _MAX_CLAIM_OUTCOME_UNKNOWN_WAITS,
    )

    assert _MAX_CLAIM_OUTCOME_UNKNOWN_WAITS == 12
    assert (
        _HISTORICAL_CLAIM_OUTCOME_UNKNOWN_PATCH
        == "historical-eval-claim-outcome-unknown-v1"
    )
