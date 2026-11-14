"""The eval-task activities must say what they did.

Every one of them was a bare ``async with Heartbeater(): await
otel_sync_to_async(...)`` with no log call, so across a production log corpus
the only loggers ever seen under an eval-task workflow id were the ClickHouse
ones. A task that stopped draining was invisible: the only evidence it had
stalled lived in the entry table, which no dashboard reads. These tests pin one
structured line per claim, run, reap, reconcile and finalize, and pin that the
lines carry counts and task-level state only — never an entry payload.
"""

import pytest
import structlog

import tfc.temporal.eval_tasks.activities as activities
from tfc.temporal.eval_tasks.types import (
    ClaimBatchInput,
    FinalizeInput,
    ReapInput,
    ReconcileActivityInput,
    RunEntryInput,
)

_TASK_ID = "11111111-2222-3333-4444-555555555555"


class _NoopHeartbeater:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _direct(function, **_kwargs):
    async def invoke(*args, **kwargs):
        return function(*args, **kwargs)

    return invoke


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(activities, "Heartbeater", _NoopHeartbeater)
    monkeypatch.setattr(activities, "otel_sync_to_async", _direct)
    return monkeypatch


def _event(records, name):
    matching = [record for record in records if record.get("event") == name]
    assert matching, f"no {name!r} line in {[r.get('event') for r in records]}"
    assert len(matching) == 1, f"{name!r} logged {len(matching)} times"
    return matching[0]


@pytest.mark.asyncio
async def test_reconcile_logs_its_counts(stub):
    stub.setattr(
        activities,
        "_reconcile_sync",
        lambda task_id: {
            "task_id": task_id,
            "created": 7,
            "requeued": 2,
            "dropped": 1,
        },
    )

    with structlog.testing.capture_logs() as records:
        await activities.reconcile_eval_task_activity(
            ReconcileActivityInput(task_id=_TASK_ID)
        )

    line = _event(records, "eval_task_reconciled")
    assert line["task_id"] == _TASK_ID
    assert (line["created"], line["requeued"], line["dropped"]) == (7, 2, 1)


@pytest.mark.asyncio
async def test_claim_logs_how_many_entries_it_took(stub):
    stub.setattr(
        activities,
        "_claim_batch_sync",
        lambda task_id, n: {"entry_ids": ["a", "b", "c"]},
    )

    with structlog.testing.capture_logs() as records:
        await activities.claim_eval_batch_activity(
            ClaimBatchInput(task_id=_TASK_ID, n=3)
        )

    line = _event(records, "eval_task_batch_claimed")
    assert line["task_id"] == _TASK_ID
    assert line["claimed"] == 3


@pytest.mark.asyncio
async def test_run_logs_the_task_and_terminal_status_but_no_entry_payload(stub):
    """Task-level state only: an eval entry's payload is customer data and has
    no place in a log line."""
    stub.setattr(
        activities,
        "_run_entry_sync",
        lambda entry_id: {
            "entry_id": entry_id,
            "task_id": _TASK_ID,
            "status": "completed",
        },
    )

    with structlog.testing.capture_logs() as records:
        await activities.run_eval_entry_activity(RunEntryInput(entry_id="entry-1"))

    line = _event(records, "eval_task_entry_run")
    assert line["task_id"] == _TASK_ID
    assert line["status"] == "completed"
    assert "entry_id" not in line


@pytest.mark.asyncio
async def test_reap_logs_what_it_reclaimed(stub):
    stub.setattr(
        activities,
        "_reap_sync",
        lambda task_id, older_than_seconds, max_attempts, confirmed: {
            "requeued": 4,
            "failed": 1,
            "older_than_seconds": 5_401,
        },
    )

    with structlog.testing.capture_logs() as records:
        await activities.reap_stale_running_activity(ReapInput(task_id=_TASK_ID))

    line = _event(records, "eval_task_reaped")
    assert line["task_id"] == _TASK_ID
    assert (line["requeued"], line["failed"]) == (4, 1)
    # The threshold the reap applied, not the one it was asked for: the
    # workflow always asks for ReapInput's 600 s default and the activity
    # either honours it or raises it to the floor, depending on what the
    # starter's describe established, so logging the input would report a
    # window the reap may never have used.
    assert line["older_than_seconds"] == 5_401
    assert ReapInput(task_id=_TASK_ID).older_than_seconds == 600


@pytest.mark.asyncio
async def test_finalize_logs_whether_the_task_actually_finished(stub):
    """A drain that ends without finalizing is a task with entries stranded
    RUNNING. That is the line an operator needs, so it is logged either way."""
    stub.setattr(
        activities,
        "_finalize_task_sync",
        lambda task_id: {
            "task_id": task_id,
            "finalized": False,
            "status": "running",
        },
    )

    with structlog.testing.capture_logs() as records:
        await activities.finalize_eval_task_activity(FinalizeInput(task_id=_TASK_ID))

    line = _event(records, "eval_task_finalize_attempted")
    assert line["task_id"] == _TASK_ID
    assert line["finalized"] is False
    assert line["status"] == "running"
