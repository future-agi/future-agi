"""Lost-worker recovery is bounded by the entry row, not the CAN cache."""

from dataclasses import asdict

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from temporalio.exceptions import ActivityError, TimeoutType
from temporalio.exceptions import TimeoutError as ActivityTimeoutError

from tfc.temporal.eval_tasks import activities, workflows
from tfc.temporal.eval_tasks.types import ContinuousDrainState, EvalTaskWorkflowInput
from tracer.models.observation_span import EvalEntryStatus
from tracer.services.eval_tasks.entries import claim_pending_batch


def _lost_worker():
    cause = ActivityTimeoutError(
        "worker stopped heartbeating",
        type=TimeoutType.HEARTBEAT,
        last_heartbeat_details=[],
    )
    exc = ActivityError(
        "run failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="run_eval_entry_activity",
        activity_id="entry",
        retry_state=None,
    )
    exc.__cause__ = cause
    return exc


@pytest.mark.django_db(transaction=True)
def test_lost_worker_retry_is_durable_and_pause_does_not_consume_it(
    eval_task, make_pending_entries
):
    [entry] = make_pending_entries(eval_task, 1, status=EvalEntryStatus.RUNNING)
    task_id, entry_id = str(eval_task.id), str(entry.id)

    assert activities._requeue_entries_sync(task_id, [entry_id]) == {"requeued": 1}
    entry.refresh_from_db()
    assert entry.attempts == 0
    claim_pending_batch(eval_task, 1)
    assert activities._requeue_lost_entry_sync(entry_id)["status"] == "pending"
    entry.refresh_from_db()
    assert entry.attempts == 1
    # A retry after the write landed is idempotent while the row is pending.
    assert activities._requeue_lost_entry_sync(entry_id)["status"] == "pending"
    entry.refresh_from_db()
    assert entry.attempts == 1

    claim_pending_batch(eval_task, 1)
    activities._requeue_entries_sync(task_id, [entry_id])
    entry.refresh_from_db()
    assert entry.attempts == 1
    claim_pending_batch(eval_task, 1)
    assert activities._requeue_lost_entry_sync(entry_id)["status"] == "errored"
    entry.refresh_from_db()
    assert entry.status == EvalEntryStatus.ERRORED
    assert entry.attempts == 1
    assert entry.error is True
    assert entry.config_hash
    assert "recovery retry" in entry.error_message
    assert activities._requeue_lost_entry_sync(entry_id)["status"] == "errored"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "terminal", [EvalEntryStatus.COMPLETED, EvalEntryStatus.SKIPPED]
)
def test_lost_worker_requeue_leaves_finished_and_deleted_entries_alone(
    eval_task, make_pending_entries, terminal
):
    [entry] = make_pending_entries(eval_task, 1, status=terminal)
    assert activities._requeue_lost_entry_sync(str(entry.id))["status"] == terminal
    entry.refresh_from_db()
    assert entry.status == terminal
    assert entry.attempts == 0
    entry.delete()
    assert activities._requeue_lost_entry_sync(str(entry.id))["status"] == "deleted"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("continuous", [False, True], ids=["historical", "continuous"])
def test_lost_worker_retry_is_bounded_after_can_cache_overflows(
    monkeypatch, eval_task, make_pending_entries, continuous
):
    """Real claims and recovery writes over two workflow hops, with the lost
    entry omitted from the 5,000-ID CAN cache. Its second loss must settle it.
    """
    [entry] = make_pending_entries(eval_task, 1)
    task_id, entry_id = str(eval_task.id), str(entry.id)
    carried = [f"00000000-0000-0000-0000-{i:012x}" for i in range(5000)]
    assert entry_id > carried[-1]
    recovery_calls = []

    class Hop(BaseException):
        pass

    async def nothing(*args, **kwargs):
        return None

    async def reconciled(*args, **kwargs):
        return True

    async def active(*args, **kwargs):
        return {"active": True, "status": "running", "has_undrained_work": True}

    async def execute(name, arg, **kwargs):
        if name == "run_eval_entry_activity":
            raise _lost_worker()
        if name == "requeue_lost_eval_entry_activity":
            recovery_calls.append(arg.entry_id)
            return await sync_to_async(activities._requeue_lost_entry_sync)(
                arg.entry_id
            )
        if name == "fail_eval_entry_activity":
            return await sync_to_async(activities._fail_entry_sync)(arg.entry_id)
        if name == "requeue_eval_entries_activity":
            return await sync_to_async(activities._requeue_entries_sync)(
                arg.task_id, arg.entry_ids
            )
        raise AssertionError(name)

    def hop(arg):
        raise Hop(arg)

    for name in ("_apply_labels", "_mark_running", "_reap"):
        monkeypatch.setattr(workflows, name, nothing)
    monkeypatch.setattr(workflows, "_reconcile_continuous", reconciled)
    monkeypatch.setattr(workflows, "_task_state", active)
    monkeypatch.setattr(
        workflows, "_claim", sync_to_async(activities._claim_batch_sync)
    )
    monkeypatch.setattr(workflows, "_set_status", lambda *args: None)
    monkeypatch.setattr(workflows, "_should_continue_as_new", lambda *args: True)
    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(workflows.workflow, "patched", lambda *args: True)
    monkeypatch.setattr(workflows.workflow, "continue_as_new", hop)
    cls = (
        workflows.ContinuousEvalTaskWorkflow
        if continuous
        else workflows.HistoricalEvalTaskWorkflow
    )
    state = (
        ContinuousDrainState(task_id=task_id, lost_entry_ids=carried)
        if continuous
        else EvalTaskWorkflowInput(
            task_id=task_id, already_reconciled=True, lost_entry_ids=carried
        )
    )
    for expected_status in (EvalEntryStatus.PENDING, EvalEntryStatus.ERRORED):
        with pytest.raises(Hop) as captured:
            async_to_sync(cls()._run)(state)
        state = captured.value.args[0]
        # Round-trip like Temporal's JSON dataclass converter, into a new run.
        state = type(state)(**asdict(state))
        assert len(state.lost_entry_ids) == 5000
        assert entry_id not in state.lost_entry_ids
        entry.refresh_from_db()
        assert entry.status == expected_status
        assert entry.attempts == 1
    assert recovery_calls == [entry_id, entry_id]
    assert state.processed == 1


def test_legacy_history_keeps_the_original_requeue_commands(monkeypatch):
    async def execute(name, arg, **kwargs):
        assert name == "run_eval_entry_activity"
        raise _lost_worker()

    monkeypatch.setattr(workflows.workflow, "execute_activity", execute)
    monkeypatch.setattr(
        workflows.workflow,
        "patched",
        lambda name: name == workflows._LOST_WORKER_REQUEUE_PATCH,
    )
    lost = set()
    skipped = async_to_sync(workflows._drain_batch)(["entry"], 1, lost=lost)
    assert skipped == ["entry"]
    assert lost == {"entry"}


def test_lost_worker_recovery_activity_is_registered():
    from tfc.temporal.eval_tasks import get_activities

    assert activities.requeue_lost_eval_entry_activity in get_activities()
