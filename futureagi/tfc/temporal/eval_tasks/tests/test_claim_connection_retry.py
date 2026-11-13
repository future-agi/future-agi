"""A writer that never submitted claim SQL retains the activity retry budget."""

import socket
import uuid
from collections import Counter
from contextvars import ContextVar

import psycopg
import pytest
from asgiref.sync import sync_to_async
from django.db import connections, router
from django.db.backends.postgresql.base import DatabaseWrapper
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

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
