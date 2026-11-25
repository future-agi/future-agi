"""A writer that never submitted claim SQL retains the activity retry budget."""

import socket
import uuid
from collections import Counter
from contextvars import ContextVar

import psycopg
import pytest
from asgiref.sync import sync_to_async
from django.db import OperationalError, connections, router
from django.db.backends.postgresql.base import DatabaseWrapper
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import activities, get_activities, get_workflows
from tfc.temporal.eval_tasks.db_errors import database_control_errors
from tfc.temporal.eval_tasks.tests.test_workflows import (
    _patch_noop_reconcile,
    _status_counts,
    _task_status,
)
from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput
from tfc.temporal.eval_tasks.workflows import HistoricalEvalTaskWorkflow
from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.services.eval_tasks import entries


def test_claim_sqlstate_08001_remains_retryable():
    with pytest.raises(ApplicationError) as caught:
        with database_control_errors(
            task_id="synthetic-task", control_activity="claim", claim=True
        ):
            raise psycopg.errors.SqlclientUnableToEstablishSqlconnection(
                "synthetic connection establishment failure"
            )
    assert caught.value.non_retryable is False
    assert caught.value.type == "EvalTaskDatabaseUnavailable"


def test_unobserved_submission_is_conservatively_non_retryable():
    with pytest.raises(ApplicationError) as caught:
        with database_control_errors(
            task_id="synthetic-task", control_activity="claim", claim=True
        ):
            raise psycopg.InterfaceError("the connection is closed")
    assert caught.value.non_retryable is True
    assert caught.value.type == "EvalTaskClaimOutcomeUnknown"


@pytest.mark.parametrize(
    "submitted,error_type,non_retryable",
    [
        (False, "EvalTaskDatabaseUnavailable", False),
        (True, "EvalTaskClaimOutcomeUnknown", True),
    ],
)
def test_pooler_refusal_during_claim(submitted, error_type, non_retryable):
    """Before submission it keeps the retry budget; after, the claim parks."""
    with pytest.raises(ApplicationError) as caught:
        with database_control_errors(
            task_id="synthetic-task",
            control_activity="claim",
            claim=True,
            claim_submitted=lambda: submitted,
        ):
            raise psycopg.errors.ProtocolViolation(
                "server login has been failing, try again later (server_login_retry)"
            )
    assert caught.value.type == error_type
    assert caught.value.non_retryable is non_retryable


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True, databases=["default", "default_direct"])
@pytest.mark.parametrize("failure", [None, "refused", "08001", "08006"])
async def test_historical_claim_connect_failure_recovers(
    workflow_environment, eval_task, make_pending_entries, monkeypatch, failure
):
    """Real PG/Temporal; only paid computation and preseeded reconciliation stubbed.

    An independent reader succeeds before the writer fails at transaction entry.
    The refusal is a real TCP refusal against a reserved, non-listening socket;
    SQLSTATE cases represent driver failures at the same connection boundary.
    """
    _patch_noop_reconcile(monkeypatch)
    created = await sync_to_async(make_pending_entries)(eval_task, 2)
    completions = Counter()
    attempts, statements, statuses = [], [], []
    in_claim = ContextVar("in_claim", default=False)
    failures = []
    original_claim = entries.claim_pending_batch
    original_connect = DatabaseWrapper.get_new_connection
    original_sync_claim = activities._claim_batch_sync
    original_set_status = activities._set_task_status_sync

    class IndependentReader:
        def db_for_read(self, model, **hints):
            return "default_direct" if model is EvalTask else None

        def db_for_write(self, model, **hints):
            return None

    monkeypatch.setattr(router, "routers", [IndependentReader(), *router.routers])

    def complete(entry):
        completions[str(entry.id)] += 1
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", complete)

    def record_attempt(*args):
        attempts.append(activity.info().attempt)
        return original_sync_claim(*args)

    def record_status(task_id, status, *args, **kwargs):
        statuses.append(status)
        return original_set_status(task_id, status, *args, **kwargs)

    monkeypatch.setattr(activities, "_claim_batch_sync", record_attempt)
    monkeypatch.setattr(activities, "_set_task_status_sync", record_status)

    def record_sql(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    with socket.socket() as refused_port:
        refused_port.bind(("127.0.0.1", 0))

        def connect(self, conn_params):
            if failure and in_claim.get() and self.alias == "default" and not failures:
                assert self.connection is None and not self.in_atomic_block
                assert statements == []
                failures.append(failure)
                if failure == "08001":
                    raise psycopg.errors.SqlclientUnableToEstablishSqlconnection()
                if failure == "08006":
                    raise psycopg.errors.ConnectionFailure()
                return original_connect(
                    self,
                    {
                        **conn_params,
                        "host": "127.0.0.1",
                        "port": str(refused_port.getsockname()[1]),
                        "connect_timeout": 2,
                    },
                )
            return original_connect(self, conn_params)

        monkeypatch.setattr(DatabaseWrapper, "get_new_connection", connect)

        def instrument_claim(task, n):
            assert task._state.db == "default_direct"  # preliminary read succeeded
            writer = connections["default"]
            writer.close()
            token = in_claim.set(True)
            try:
                with writer.execute_wrapper(record_sql):
                    return original_claim(task, n)
            finally:
                in_claim.reset(token)

        monkeypatch.setattr(entries, "claim_pending_batch", instrument_claim)
        queue = f"claim-connect-{uuid.uuid4().hex}"
        async with Worker(
            workflow_environment.client,
            task_queue=queue,
            workflows=get_workflows(),
            activities=get_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await workflow_environment.client.execute_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=str(eval_task.id), task_queue=queue, batch_size=2
                ),
                id=f"eval-task-{eval_task.id}",
                task_queue=queue,
            )

    print(
        f"claim_connection={failure} attempts={attempts} statuses={statuses} completed={dict(completions)}"
    )
    assert await _task_status(str(eval_task.id)) == EvalTaskStatus.COMPLETED
    assert result.status == "completed" and result.processed == 2
    assert EvalTaskStatus.FAILED not in statuses
    assert (await _status_counts(str(eval_task.id)))["completed"] == 2
    assert completions == Counter({str(entry.id): 1 for entry in created})
    if failure:
        assert failures == [failure]
        assert 2 in attempts


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True, databases=["default", "default_direct"])
@pytest.mark.parametrize("outcome", ["rolled_back", "committed"])
async def test_historical_lost_claim_reply_completes_with_each_entry_evaluated_once(
    workflow_environment, eval_task, make_pending_entries, monkeypatch, outcome
):
    """Real PG/Temporal; only paid computation and preseeded reconciliation stubbed.

    The writer's connection drops after the claim SQL was submitted, so the
    activity cannot know whether the claim committed and raises the
    non-retryable ``EvalTaskClaimOutcomeUnknown``. A historical task must not
    fail on it, and no entry may be evaluated or billed twice:

    * ``rolled_back``: the server connection is terminated between the claim's
      UPDATE and its COMMIT, so the commit fails on a real dropped connection
      and the batch stays pending;
    * ``committed``: the claim commits and the reply is then lost, so the
      batch stays RUNNING with no evaluation dispatched until the stale-claim
      reaper requeues it. Its claim is aged past the reaper's threshold right
      after the commit, so the first reap takes it back; the window in which
      the batch is still too young to reap is covered by
      ``test_committed_lost_reply_before_the_reaper_can_reclaim_it``.
    """
    from datetime import timedelta

    from django.utils import timezone

    from tfc.temporal.eval_tasks import workflows as wf

    _patch_noop_reconcile(monkeypatch)
    # One-second finalize waits instead of ten minutes; everything else,
    # including every retry policy, is production's.
    monkeypatch.setattr(wf, "_FINALIZE_WAIT", timedelta(seconds=1))
    created = await sync_to_async(make_pending_entries)(eval_task, 3)
    completions = Counter()
    lost_batch, statuses = [], []
    claim_calls = []
    original_claim = entries.claim_pending_batch
    original_set_status = activities._set_task_status_sync

    def complete(entry):
        completions[str(entry.id)] += 1
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", complete)

    def record_status(task_id, status, *args, **kwargs):
        statuses.append(status)
        return original_set_status(task_id, status, *args, **kwargs)

    monkeypatch.setattr(activities, "_set_task_status_sync", record_status)

    def terminate_after_update(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.lstrip().upper().startswith("UPDATE"):
            pid = context["connection"].connection.info.backend_pid
            settings = connections["default"].settings_dict
            with psycopg.connect(
                dbname=settings["NAME"],
                user=settings["USER"],
                password=settings["PASSWORD"],
                host=settings["HOST"],
                port=settings["PORT"],
                autocommit=True,
            ) as admin:
                # Waits until the backend has exited, so the COMMIT that
                # follows meets a dropped connection rather than racing it.
                admin.execute("SELECT pg_terminate_backend(%s, 5000)", [pid])
        return result

    events = []
    original_reap = activities._reap_sync

    def record_reap(*args, **kwargs):
        reaped = original_reap(*args, **kwargs)
        events.append(("reap", reaped["requeued"]))
        return reaped

    monkeypatch.setattr(activities, "_reap_sync", record_reap)

    def lose_first_reply(task, n):
        claim_calls.append(n)
        if lost_batch:
            claimed = original_claim(task, n)
            events.append(("claim", sorted(str(entry.id) for entry in claimed)))
            return claimed
        events.append(("lost", None))
        if outcome == "rolled_back":
            lost_batch.append(None)
            with connections["default"].execute_wrapper(terminate_after_update):
                return original_claim(task, n)
        claimed = original_claim(task, n)
        lost_batch.extend(str(entry.id) for entry in claimed)
        EvalLogger.objects.filter(id__in=lost_batch).update(
            updated_at=timezone.now() - timedelta(hours=3)
        )
        raise OperationalError("writer unavailable") from psycopg.InterfaceError(
            "the connection is closed"
        )

    monkeypatch.setattr(entries, "claim_pending_batch", lose_first_reply)
    queue = f"claim-lost-reply-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(
                task_id=str(eval_task.id), task_queue=queue, batch_size=2
            ),
            id=f"eval-task-{eval_task.id}",
            task_queue=queue,
        )
        result = await handle.result()
        history = await handle.fetch_history()

    print(
        f"lost_claim={outcome} events={events} statuses={statuses} completed={dict(completions)}"
    )
    # The lost claim was not re-run in place: the drain waited and reaped
    # first. Here the next claim takes the first two entries, still pending if
    # the claim rolled back, or requeued by that reap because the committed
    # batch was aged past the reaper's threshold. A batch too young to reap
    # instead waits while other pending work is claimed (see
    # test_committed_lost_reply_before_the_reaper_can_reclaim_it).
    first_two = sorted(str(entry.id) for entry in created[:2])
    lost_at = events.index(("lost", None))
    after = events[lost_at + 1 :]
    assert after[0][0] == "reap", events
    assert [e for e in after if e[0] == "claim"][0] == ("claim", first_two), events
    if outcome == "committed":
        assert after[0] == ("reap", 2), events
    assert result.status == "completed" and result.processed == 3
    assert await _task_status(str(eval_task.id)) == EvalTaskStatus.COMPLETED
    assert EvalTaskStatus.FAILED not in statuses
    counts = await _status_counts(str(eval_task.id))
    assert counts["completed"] == 3 and counts["total"] == 3
    # Every entry evaluated exactly once: the lost batch was neither run under
    # its unacknowledged claim nor claimed a second time while it stood.
    assert completions == Counter({str(entry.id): 1 for entry in created})
    serialized = history.to_json()
    assert "EvalTaskClaimOutcomeUnknown" in serialized
    assert any(
        b"historical-eval-claim-outcome-unknown-v1" in payload.data
        for event in history.events
        for detail in event.marker_recorded_event_attributes.details.values()
        for payload in detail.payloads
    )
    if outcome == "committed":
        # The lost batch came back only through the reaper, once.
        requeued = await sync_to_async(
            lambda: sorted(
                EvalLogger.objects.filter(id__in=lost_batch).values_list(
                    "attempts", flat=True
                )
            )
        )()
        assert len(lost_batch) == 2 and requeued == [1, 1]
    await Replayer(
        workflows=[HistoricalEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_committed_lost_reply_before_the_reaper_can_reclaim_it(
    workflow_environment, eval_task, make_pending_entries, monkeypatch
):
    """A committed claim whose reply is lost, with the batch left as young as
    it really is: the reaper cannot take it back yet.

    The reaper's staleness floor (ninety minutes in production) is scaled down
    to a few seconds and the finalize wait to one; the batch is not aged. The
    drain must not fail, must go on claiming the task's other pending work
    while the lost batch is still RUNNING, and once the reaper requeues that
    batch it is claimed again and evaluated once. No entry is evaluated or
    billed twice, and the task ends COMPLETED.
    """
    from datetime import timedelta

    from tfc.temporal.eval_tasks import workflows as wf
    from tracer.services.eval_tasks import reaper

    _patch_noop_reconcile(monkeypatch)
    monkeypatch.setattr(wf, "_FINALIZE_WAIT", timedelta(seconds=1))
    stale_after = 5
    monkeypatch.setattr(
        reaper, "effective_stale_seconds", lambda requested, **kwargs: stale_after
    )
    created = await sync_to_async(make_pending_entries)(eval_task, 3)
    completions = Counter()
    events, statuses, lost_batch = [], [], []
    lost_batch_status_at_claim = []

    def complete(entry):
        completions[str(entry.id)] += 1
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", complete)
    original_set_status = activities._set_task_status_sync

    def record_status(task_id, status, *args, **kwargs):
        statuses.append(status)
        return original_set_status(task_id, status, *args, **kwargs)

    monkeypatch.setattr(activities, "_set_task_status_sync", record_status)
    original_reap = activities._reap_sync

    def record_reap(*args, **kwargs):
        reaped = original_reap(*args, **kwargs)
        events.append(("reap", reaped["requeued"]))
        return reaped

    monkeypatch.setattr(activities, "_reap_sync", record_reap)
    original_claim = entries.claim_pending_batch

    def lose_first_reply(task, n):
        if lost_batch:
            lost_batch_status_at_claim.append(
                sorted(
                    EvalLogger.objects.filter(id__in=lost_batch).values_list(
                        "status", flat=True
                    )
                )
            )
        claimed = original_claim(task, n)
        ids = sorted(str(entry.id) for entry in claimed)
        if not lost_batch:
            # Committed, then the reply is lost.
            lost_batch.extend(ids)
            events.append(("lost", ids))
            raise OperationalError("writer unavailable") from psycopg.InterfaceError(
                "the connection is closed"
            )
        events.append(("claim", ids))
        return claimed

    monkeypatch.setattr(entries, "claim_pending_batch", lose_first_reply)
    queue = f"claim-lost-young-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(
                task_id=str(eval_task.id), task_queue=queue, batch_size=2
            ),
            id=f"eval-task-{eval_task.id}",
            task_queue=queue,
        )
        result = await handle.result()
        history = await handle.fetch_history()

    print(f"events={events} statuses={statuses} completed={dict(completions)}")
    first_two = sorted(str(entry.id) for entry in created[:2])
    third = [str(created[2].id)]
    assert lost_batch == first_two
    lost_at = events.index(("lost", first_two))
    after = events[lost_at + 1 :]
    # The first reap after the loss finds the batch too young to take back,
    # and the next claim takes the other pending entry while the lost batch is
    # still RUNNING.
    assert after[0] == ("reap", 0), events
    claims = [e for e in after if e[0] == "claim"]
    assert claims[0] == ("claim", third), events
    assert lost_batch_status_at_claim[0] == [EvalEntryStatus.RUNNING] * 2
    # Only the reaper brings the lost batch back, once, and only then is it
    # claimed again.
    assert [e for e in after if e[0] == "reap" and e[1]] == [("reap", 2)], events
    assert ("claim", first_two) in claims, events
    assert events.index(("reap", 2)) < events.index(("claim", first_two)), events
    assert result.status == "completed" and result.processed == 3
    assert await _task_status(str(eval_task.id)) == EvalTaskStatus.COMPLETED
    assert EvalTaskStatus.FAILED not in statuses
    counts = await _status_counts(str(eval_task.id))
    assert counts["completed"] == 3 and counts["total"] == 3
    assert completions == Counter({str(entry.id): 1 for entry in created})
    requeued = await sync_to_async(
        lambda: sorted(
            EvalLogger.objects.filter(id__in=lost_batch).values_list(
                "attempts", flat=True
            )
        )
    )()
    assert requeued == [1, 1]
    await Replayer(
        workflows=[HistoricalEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_historical_lost_claim_replies_in_a_row_are_bounded(
    workflow_environment, eval_task, make_pending_entries, monkeypatch
):
    """A writer that loses every claim reply cannot keep a historical task
    waiting forever: after the cap of lost replies in a row it fails as it did
    before, never having dispatched an evaluation."""
    from datetime import timedelta

    from temporalio.client import WorkflowFailureError

    from tfc.temporal.eval_tasks import workflows as wf

    _patch_noop_reconcile(monkeypatch)
    monkeypatch.setattr(wf, "_FINALIZE_WAIT", timedelta(seconds=0.2))
    await sync_to_async(make_pending_entries)(eval_task, 2)
    claim_calls, ran = [], []
    monkeypatch.setattr(
        "tracer.services.eval_tasks.run_entry.run_entry", lambda e: ran.append(e)
    )

    def submitted_then_lost(task, n):
        claim_calls.append(n)
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")  # the claim's SQL reached the server
        raise OperationalError("writer unavailable") from psycopg.InterfaceError(
            "the connection is closed"
        )

    monkeypatch.setattr(entries, "claim_pending_batch", submitted_then_lost)
    queue = f"claim-lost-cap-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        with pytest.raises(WorkflowFailureError):
            await workflow_environment.client.execute_workflow(
                HistoricalEvalTaskWorkflow.run,
                EvalTaskWorkflowInput(
                    task_id=str(eval_task.id), task_queue=queue, batch_size=2
                ),
                id=f"eval-task-{eval_task.id}",
                task_queue=queue,
            )

    # Each lost reply is one claim attempt, never retried in place.
    assert len(claim_calls) == wf._MAX_CLAIM_OUTCOME_UNKNOWN_WAITS + 1
    assert ran == []
    assert await _task_status(str(eval_task.id)) == EvalTaskStatus.FAILED
    assert (await _status_counts(str(eval_task.id)))["pending"] == 2


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_historical_lost_claim_reply_leaves_the_idle_wait_budget_alone(
    workflow_environment, eval_task, make_pending_entries, monkeypatch
):
    """Waits after a lost claim reply are not idle finalize waits.

    One entry stays RUNNING under a fresh claim the reaper may not take yet,
    so every empty claim costs an idle wait. After eleven of them a claim
    reply is lost, and the next empty claim must still get the twelfth idle
    wait rather than fail the drain. The entry then finishes and the task
    completes.
    """
    from datetime import timedelta

    from tfc.temporal.eval_tasks import workflows as wf

    _patch_noop_reconcile(monkeypatch)
    monkeypatch.setattr(wf, "_FINALIZE_WAIT", timedelta(seconds=0.2))
    [stranded] = await sync_to_async(make_pending_entries)(
        eval_task, 1, status=EvalEntryStatus.RUNNING
    )
    idle_cap = wf._MAX_IDLE_FINALIZE_WAITS
    claim_calls = []
    original_claim = entries.claim_pending_batch

    def claim(task, n):
        claim_calls.append(n)
        if len(claim_calls) == idle_cap:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")  # the claim's SQL reached the server
            raise OperationalError("writer unavailable") from psycopg.InterfaceError(
                "the connection is closed"
            )
        if len(claim_calls) == idle_cap + 2:
            # Only reachable if the lost reply did not use up an idle wait.
            EvalLogger.objects.filter(id=stranded.id).update(
                status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
            )
        return original_claim(task, n)

    monkeypatch.setattr(entries, "claim_pending_batch", claim)
    queue = f"claim-lost-idle-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        result = await workflow_environment.client.execute_workflow(
            HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(
                task_id=str(eval_task.id), task_queue=queue, batch_size=2
            ),
            id=f"eval-task-{eval_task.id}",
            task_queue=queue,
        )

    assert result.status == "completed"
    assert len(claim_calls) == idle_cap + 2
    assert await _task_status(str(eval_task.id)) == EvalTaskStatus.COMPLETED
