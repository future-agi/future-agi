"""PostgreSQL failures cross real activity conversion, retries and workflow timers."""

import asyncio
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from asgiref.sync import sync_to_async
from django.db import InternalError, OperationalError
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import activities, get_activities, get_workflows
from tfc.temporal.eval_tasks import workflows as wf
from tfc.temporal.eval_tasks.db_errors import is_transient_database_error
from tfc.temporal.eval_tasks.tests._refusing_postgres import RefusingPostgres
from tfc.temporal.eval_tasks.tests.test_workflows import (
    _patch_completing_run_entry,
    _patch_noop_reconcile,
    _set_status,
    _status_counts,
    _task_status,
)
from tfc.temporal.eval_tasks.types import ContinuousDrainState
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType


def _db_error(kind="refused"):
    drivers = {
        "refused": psycopg.OperationalError("connection refused"),
        "timeout": psycopg.OperationalError("connection timeout expired"),
        "lost": psycopg.InterfaceError("the connection is closed"),
        "read_only": psycopg.errors.ReadOnlySqlTransaction("read-only transaction"),
    }
    exc = (
        InternalError("writer unavailable")
        if kind == "read_only"
        else OperationalError("writer unavailable")
    )
    exc.__cause__ = drivers[kind]
    return exc


@pytest.mark.parametrize("kind", ["refused", "timeout", "lost", "read_only"])
def test_driver_outage_classification(kind):
    assert is_transient_database_error(_db_error(kind))


@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("connection refused"),  # no driver evidence
        psycopg.errors.InvalidPassword("connection refused"),
        psycopg.errors.UndefinedTable("missing table"),
        psycopg.errors.SerializationFailure("could not serialize"),
        psycopg.OperationalError("invalid connection option"),
        ValueError("connection timeout expired"),
    ],
)
def test_configuration_and_deterministic_errors_are_not_outages(exc):
    assert not is_transient_database_error(exc)


# PgBouncer's own refusals while PostgreSQL behind it is unreachable, paused,
# disabled or killed, or the pooler is restarting (wordings of 1.18 to 1.26).
# It sends them as SQLSTATE 08P01, or inside libpq's text at connect time.
_PGBOUNCER_OUTAGES = {
    "login-retry": (
        "server login has been failing, try again later (server_login_retry)"
    ),
    "login-retry-cached": (
        "server login has been failing, cached error: connect failed"
        " (server_login_retry)"
    ),
    "server-crashed": "server conn crashed?",
    "wait-timeout": "query_wait_timeout",
    "login-timeout": "client_login_timeout (server down)",
    "disabled": 'database "synthetic_db" is disabled',
    "killed": "database removed",
    "pooler-shutdown": "pooler is shutting down",
    "server-shutdown": "server shutting down",
}


def _at_query_and_connect(message):
    at_query = OperationalError("writer unavailable")
    at_query.__cause__ = psycopg.errors.ProtocolViolation(message)
    at_connect = OperationalError("writer unavailable")
    at_connect.__cause__ = psycopg.OperationalError(
        'connection failed: connection to server at "127.0.0.1", port 6432'
        f" failed: FATAL:  {message}"
    )
    return at_query, at_connect


@pytest.mark.parametrize(
    "message", list(_PGBOUNCER_OUTAGES.values()), ids=list(_PGBOUNCER_OUTAGES)
)
def test_pgbouncer_outage_refusals_are_transient(message):
    assert all(map(is_transient_database_error, _at_query_and_connect(message)))


@pytest.mark.parametrize(
    "message",
    [
        # PostgreSQL's own protocol violations.
        "invalid frontend message type 0",
        'bind message supplies 1 parameters, but prepared statement "" requires 2',
        "insufficient data left in message",
        # PgBouncer refusing the client itself, or a statement it ran.
        "unsupported startup parameter in options: work_mem",
        "no such user: synthetic_user",
        '"trust" authentication failed',
        'password authentication failed for user "synthetic_user"',
        "no more connections allowed (max_client_conn)",
        "query_timeout",
    ],
)
def test_protocol_violations_and_pooler_client_refusals_are_not_outages(message):
    assert not any(map(is_transient_database_error, _at_query_and_connect(message)))


def test_pooler_text_under_another_sqlstate_is_not_an_outage():
    raised = psycopg.errors.RaiseException('database "synthetic_db" is disabled')
    assert not is_transient_database_error(raised)


# PostgreSQL's 57P03 login refusals while it starts up, shuts down, recovers
# after a backend crash, or replays WAL before a standby is consistent.
# PgBouncer 1.18 relays the same text to clients logging in while its pool is
# cold. psycopg reports them, like any connect failure, with no SQLSTATE.
_POSTGRES_NOT_READY = {
    "starting-up": "the database system is starting up",
    "shutting-down": "the database system is shutting down",
    "recovery": "the database system is in recovery mode",
    "not-consistent": "the database system is not yet accepting connections",
}
# Login refusals that no wait fixes: credentials, a missing database or role,
# access rules, a malformed startup packet, a standby with hot_standby = off.
_POSTGRES_LOGIN_FAILURES = {
    "auth": ("28P01", 'password authentication failed for user "synthetic_user"'),
    "missing-database": ("3D000", 'database "synthetic_db" does not exist'),
    "missing-role": ("28000", 'role "synthetic_user" does not exist'),
    "hba": (
        "28000",
        'no pg_hba.conf entry for host "127.0.0.1", user "synthetic_user",'
        ' database "synthetic_db", no encryption',
    ),
    "protocol": (
        "08P01",
        "invalid startup packet layout: expected terminator as last byte",
    ),
    "hot-standby-off": ("57P03", "the database system is not accepting connections"),
}


def _connect_refusal(message, port):
    # The text psycopg raised against postgres:16 and PgBouncer 1.18.
    exc = OperationalError("writer unavailable")
    exc.__cause__ = psycopg.OperationalError(
        f'connection failed: connection to server at "127.0.0.1", port {port}'
        f" failed: FATAL:  {message}"
    )
    return exc


@pytest.mark.parametrize("port", [5432, 6432], ids=["direct", "via-pgbouncer"])
@pytest.mark.parametrize(
    "message", list(_POSTGRES_NOT_READY.values()), ids=list(_POSTGRES_NOT_READY)
)
def test_postgres_not_ready_refusals_at_connect_are_transient(message, port):
    assert is_transient_database_error(_connect_refusal(message, port))


@pytest.mark.parametrize(
    "message",
    [message for _, message in _POSTGRES_LOGIN_FAILURES.values()],
    ids=list(_POSTGRES_LOGIN_FAILURES),
)
def test_postgres_login_failures_at_connect_are_not_outages(message):
    assert not is_transient_database_error(_connect_refusal(message, 5432))


def _refused_login(message, sqlstate) -> psycopg.OperationalError:
    with (
        RefusingPostgres(message, sqlstate=sqlstate) as server,
        pytest.raises(psycopg.OperationalError) as caught,
    ):
        psycopg.connect(
            host="127.0.0.1",
            port=server.port,
            dbname="synthetic_db",
            user="synthetic_user",
            connect_timeout=10,
        )
    return caught.value


@pytest.mark.parametrize(
    "message", list(_POSTGRES_NOT_READY.values()), ids=list(_POSTGRES_NOT_READY)
)
def test_real_driver_not_ready_refusal_is_transient(message):
    refusal = _refused_login(message, "57P03")
    shape = f"{type(refusal).__name__}[{refusal.sqlstate}] {refusal}"
    assert message in str(refusal), shape
    # The SQLSTATE the server sent does not reach the exception.
    assert refusal.sqlstate in (None, "57P03"), shape
    assert is_transient_database_error(refusal), shape


@pytest.mark.parametrize(
    "sqlstate,message",
    list(_POSTGRES_LOGIN_FAILURES.values()),
    ids=list(_POSTGRES_LOGIN_FAILURES),
)
def test_real_driver_login_failure_is_not_an_outage(sqlstate, message):
    refusal = _refused_login(message, sqlstate)
    shape = f"{type(refusal).__name__}[{refusal.sqlstate}] {refusal}"
    assert message in str(refusal), shape
    assert not is_transient_database_error(refusal), shape


@pytest.mark.parametrize(
    "fixture", ["continuous-base-outage.json", "continuous-base-poll.json"]
)
async def test_replays_unmodified_base_history(fixture):
    """Recorded on base dev, not generated by the new workflow with a mock patch."""
    history = WorkflowHistory.from_json(
        "synthetic-replay", (Path(__file__).parent / "fixtures" / fixture).read_text()
    )
    await Replayer(
        workflows=[wf.ContinuousEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def _until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)


# The production attempt counts, read before any test shortens the timers.
CONTROL_ATTEMPTS = wf.CONTROL_RETRY_POLICY.maximum_attempts
RECONCILE_ATTEMPTS = wf.RECONCILE_RETRY_POLICY.maximum_attempts


def _fast_control_retries(monkeypatch):
    # Still exhaust every production attempt through Temporal, for the control
    # steps and for reconcile's shorter policy; shorten only their timers.
    for name, attempts in (
        ("CONTROL_RETRY_POLICY", CONTROL_ATTEMPTS),
        ("RECONCILE_RETRY_POLICY", RECONCILE_ATTEMPTS),
    ):
        monkeypatch.setattr(
            wf,
            name,
            RetryPolicy(
                initial_interval=timedelta(milliseconds=10),
                maximum_interval=timedelta(milliseconds=10),
                backoff_coefficient=1.0,
                maximum_attempts=attempts,
            ),
        )


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "helper,kind",
    [
        ("_reconcile_sync", "refused"),
        ("_reconcile_sync", "timeout"),
        ("_reconcile_sync", "lost"),
        ("_reconcile_sync", "read_only"),
        ("_get_task_state_sync", "refused"),
        ("_get_workflow_labels_sync", "refused"),
        ("_set_task_status_sync", "read_only"),
        ("_reap_sync", "refused"),
        ("_claim_batch_sync", "lost"),
    ],
)
async def test_control_outage_recovers_without_resume(
    workflow_environment,
    eval_task,
    make_pending_entries,
    monkeypatch,
    helper,
    kind,
):
    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    _patch_completing_run_entry(monkeypatch)
    task_id = str(eval_task.id)
    cursor = eval_task.created_at
    await sync_to_async(EvalTask.objects.filter(id=task_id).update)(
        run_type=RunType.CONTINUOUS,
        continuous_cursor=cursor,
    )
    await sync_to_async(make_pending_entries)(eval_task, 1)
    original = getattr(activities, helper)
    recovering = threading.Event()
    failed = threading.Event()
    attempts = []

    def unavailable(*args, **kwargs):
        if not recovering.is_set():
            attempts.append(activity.info().attempt)
            failed.set()
            raise _db_error(kind)
        return original(*args, **kwargs)

    monkeypatch.setattr(activities, helper, unavailable)
    # Reconcile's following state read must also survive the same outage.
    state_reads = []
    if helper == "_reconcile_sync":
        original_state = activities._get_task_state_sync

        def unavailable_state(*args):
            if failed.is_set() and not recovering.is_set():
                state_reads.append(1)
                raise _db_error(kind)
            return original_state(*args)

        monkeypatch.setattr(activities, "_get_task_state_sync", unavailable_state)

    queue = f"db-backoff-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(task_id=task_id, poll_interval_seconds=1),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        try:

            async def deferred():
                assert await _task_status(task_id) != EvalTaskStatus.FAILED, (
                    "transient database outage terminally failed the task"
                )
                return await handle.query("phase") == "waiting_for_database"

            await _until(deferred)
            if helper == "_reconcile_sync":

                async def state_also_exhausted():
                    return len(state_reads) >= CONTROL_ATTEMPTS

                await _until(state_also_exhausted)
            if helper == "_reconcile_sync":
                # Reconcile exhausted exactly its own five attempts and then
                # parked; the state reads failing since then never let the
                # loop reach it again, so the count cannot have grown.
                assert attempts == list(range(1, RECONCILE_ATTEMPTS + 1))
            else:
                assert len(attempts) >= CONTROL_ATTEMPTS
            assert await _task_status(task_id) in (
                EvalTaskStatus.PENDING,
                EvalTaskStatus.RUNNING,
            )
            assert (await _status_counts(task_id))["completed"] == 0
            assert (
                await sync_to_async(
                    lambda: EvalTask.objects.get(id=task_id).continuous_cursor
                )()
                == cursor
            )
            recovering.set()

            async def completed():
                return (await _status_counts(task_id))["completed"] == 1

            await _until(completed)
            assert (await _status_counts(task_id))["total"] == 1
            assert await _task_status(task_id) == EvalTaskStatus.RUNNING
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await handle.signal("pause")
            await asyncio.wait_for(handle.result(), timeout=10)
            history = await handle.fetch_history()
            serialized = history.to_json()
            assert (
                "EvalTaskDatabaseUnavailable" in serialized
                or "EvalTaskClaimOutcomeUnknown" in serialized
            )
            assert any(
                b"continuous-eval-database-backoff-v1" in payload.data
                for event in history.events
                for detail in event.marker_recorded_event_attributes.details.values()
                for payload in detail.payloads
            )
            await Replayer(
                workflows=[wf.ContinuousEvalTaskWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
        finally:
            await handle.cancel()


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_non_outage_database_error_fails_after_the_control_window(
    workflow_environment, eval_task, monkeypatch
):
    """Bad credentials are not an outage: the control step spends its bounded
    retries and the task fails; the workflow never waits for the database."""
    from temporalio.client import WorkflowFailureError

    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    task_id = str(eval_task.id)
    await sync_to_async(EvalTask.objects.filter(id=task_id).update)(
        run_type=RunType.CONTINUOUS, continuous_cursor=eval_task.created_at
    )
    attempts = []

    def bad_credentials(*args):
        attempts.append(1)
        exc = OperationalError("writer unavailable")
        exc.__cause__ = psycopg.errors.InvalidPassword(
            "password authentication failed for user"
        )
        raise exc

    monkeypatch.setattr(activities, "_get_task_state_sync", bad_credentials)
    queue = f"db-misconfigured-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(task_id=task_id, poll_interval_seconds=1),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=15)
    assert len(attempts) == CONTROL_ATTEMPTS
    assert await _task_status(task_id) == EvalTaskStatus.FAILED
    serialized = (await handle.fetch_history()).to_json()
    assert "EvalTaskDatabaseUnavailable" not in serialized
    assert "EvalTaskClaimOutcomeUnknown" not in serialized


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "terminal_status", [EvalTaskStatus.PAUSED, EvalTaskStatus.DELETED]
)
async def test_empty_task_honors_stop_during_outage(
    workflow_environment,
    eval_task,
    monkeypatch,
    terminal_status,
):
    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    original = activities._reconcile_sync
    recovered = threading.Event()

    def unavailable(*args):
        if not recovered.is_set():
            raise _db_error("read_only")
        return original(*args)

    monkeypatch.setattr(activities, "_reconcile_sync", unavailable)
    queue = f"db-stop-{uuid.uuid4().hex}"
    task_id = str(eval_task.id)
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(task_id=task_id, poll_interval_seconds=1),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        try:

            async def deferred():
                assert await _task_status(task_id) != EvalTaskStatus.FAILED, (
                    "transient database outage terminally failed the task"
                )
                return await handle.query("phase") == "waiting_for_database"

            await _until(deferred)
            await _set_status(task_id, terminal_status)
            await handle.signal("request_recheck")
            recovered.set()
            await asyncio.wait_for(handle.result(), 10)
            assert await _task_status(task_id) == terminal_status
            assert (await _status_counts(task_id))["total"] == 0
        finally:
            await handle.cancel()


async def test_deferred_pause_cleanup_rolls_history_without_replaying_provider(
    monkeypatch,
):
    from temporalio.exceptions import ActivityError, ApplicationError

    class Continued(BaseException):
        pass

    state = ContinuousDrainState(task_id="synthetic-task", processed=7)
    instance = wf.ContinuousEvalTaskWorkflow()
    calls = []
    fail_requeue = True

    async def execute(name, arg, **kwargs):
        calls.append(name)
        if name == "get_eval_task_state_activity":
            return {
                "active": fail_requeue,
                "status": "running" if fail_requeue else "paused",
                "has_undrained_work": True,
            }
        if name == "get_workflow_labels_activity":
            return {
                "org_id": "org",
                "project_id": "project",
                "run_type": "continuous",
                "task_name": "task",
                "project_name": "project",
                "org_name": "org",
                "config_summary": "",
            }
        if name == "claim_eval_batch_activity":
            return {"entry_ids": ["finished", "unstarted"]}
        if name == "requeue_eval_entries_activity" and fail_requeue:
            error = ActivityError(
                "unavailable",
                scheduled_event_id=1,
                started_event_id=2,
                identity="synthetic",
                activity_type=name,
                activity_id="1",
                retry_state=None,
            )
            error.__cause__ = ApplicationError(
                "unavailable", type="EvalTaskDatabaseUnavailable"
            )
            raise error
        return {}

    async def drain(*args, **kwargs):
        calls.append("provider")
        instance.pause()
        return ["unstarted"]

    async def sleep(*args):
        pass

    def can(checkpoint):
        assert checkpoint.processed == 8
        assert checkpoint.pending_requeue_ids == ["unstarted"]
        assert checkpoint.pause_requested
        raise Continued

    monkeypatch.setattr(wf.workflow, "execute_activity", execute)
    monkeypatch.setattr(wf.workflow, "upsert_search_attributes", lambda *_: None)
    monkeypatch.setattr(wf.workflow, "upsert_memo", lambda *_: None)
    monkeypatch.setattr(wf.workflow, "continue_as_new", can)
    monkeypatch.setattr(wf, "_should_continue_as_new", lambda *_: False)
    monkeypatch.setattr(wf, "_MAX_CONTINUOUS_POLLS", 2)
    monkeypatch.setattr(wf, "_drain_batch", drain)
    monkeypatch.setattr(wf.ContinuousEvalTaskWorkflow, "_sleep_or_recheck", sleep)
    with pytest.raises(Continued):
        await instance._run_with_database_backoff(state)
    fail_requeue = False
    await wf.ContinuousEvalTaskWorkflow()._run_with_database_backoff(state)
    assert calls.count("provider") == 1
    assert calls.count("claim_eval_batch_activity") == 1
    assert calls.count("requeue_eval_entries_activity") == 2
    assert state.pending_requeue_ids == []


@pytest.mark.django_db(transaction=True)
def test_reconcile_mid_transaction_outage_rolls_back_entries_and_cursor(
    eval_task, make_pending_entries, monkeypatch
):
    from django.utils import timezone

    from tracer.selectors.eval_tasks.row_resolver import ResolvedRowSet
    from tracer.services.eval_tasks import reconciler

    cursor = eval_task.created_at
    EvalTask.objects.filter(id=eval_task.id).update(
        run_type=RunType.CONTINUOUS, continuous_cursor=cursor
    )
    monkeypatch.setattr(
        reconciler,
        "resolve_desired_rows",
        lambda *a, **kw: ResolvedRowSet((), (), True),
    )

    def materialize(task, *args):
        make_pending_entries(task, 1)

    def failed_cursor(task, now):
        EvalTask.objects.filter(id=task.id).update(continuous_cursor=timezone.now())
        raise _db_error("lost")

    monkeypatch.setattr(reconciler, "materialize_pending", materialize)
    monkeypatch.setattr(reconciler, "_advance_continuous_cursor", failed_cursor)
    with pytest.raises(OperationalError):
        reconciler.reconcile(eval_task)
    eval_task.refresh_from_db()
    from tracer.models.observation_span import EvalLogger

    assert EvalLogger.objects.filter(eval_task_id=str(eval_task.id)).count() == 0
    assert eval_task.continuous_cursor == cursor


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_unknown_claim_commit_is_not_retried_or_dispatched(
    workflow_environment,
    eval_task,
    make_pending_entries,
    monkeypatch,
):
    from django.utils import timezone

    from tracer.models.observation_span import EvalLogger
    from tracer.services.eval_tasks import entries

    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    _patch_completing_run_entry(monkeypatch)
    await sync_to_async(make_pending_entries)(eval_task, 2)
    claim = entries.claim_pending_batch
    unacknowledged = []
    calls = []

    def commit_then_disconnect(task, n):
        calls.append(1)
        result = claim(task, n)
        if not unacknowledged:
            unacknowledged.extend(str(entry.id) for entry in result)
            raise _db_error("lost")
        return result

    monkeypatch.setattr(entries, "claim_pending_batch", commit_then_disconnect)
    task_id = str(eval_task.id)
    queue = f"db-claim-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(
                task_id=task_id, batch_size=1, poll_interval_seconds=1
            ),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        try:

            async def deferred():
                return await handle.query("phase") == "waiting_for_database"

            await _until(deferred)
            assert len(calls) == 1  # no second claim in Temporal's whole retry budget
            assert (await _status_counts(task_id))["completed"] == 0

            async def other_completed():
                counts = await _status_counts(task_id)
                return counts["completed"] == 1 and counts["running"] == 1

            await _until(other_completed)
            assert (
                await sync_to_async(
                    lambda: EvalLogger.objects.get(id=unacknowledged[0]).status
                )()
                == "running"
            )
            # Only the existing safe stale-claim mechanism can recover the
            # unacknowledged IDs, not a replay of the uncertain claim/provider.
            await sync_to_async(
                EvalLogger.objects.filter(id__in=unacknowledged).update
            )(updated_at=timezone.now() - timedelta(hours=3))

            async def all_completed():
                return (await _status_counts(task_id))["completed"] == 2

            await _until(all_completed)
            assert (await _status_counts(task_id))["total"] == 2
        finally:
            await handle.cancel()


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_pause_requeue_outage_retains_unstarted_entries(
    workflow_environment,
    eval_task,
    make_pending_entries,
    monkeypatch,
):
    from tracer.models.observation_span import EvalLogger

    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    await sync_to_async(make_pending_entries)(eval_task, 3)
    provider_started = threading.Event()
    release_provider = threading.Event()
    database_restored = threading.Event()
    provider_calls = []
    requeue_calls = []
    original_requeue = activities._requeue_entries_sync

    def run(entry):
        provider_calls.append(str(entry.id))
        provider_started.set()
        assert release_provider.wait(10)
        EvalLogger.objects.filter(id=entry.id).update(status="completed")
        return "completed"

    def requeue(*args):
        requeue_calls.append(1)
        if not database_restored.is_set():
            raise _db_error("lost")
        return original_requeue(*args)

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", run)
    monkeypatch.setattr(activities, "_requeue_entries_sync", requeue)
    task_id = str(eval_task.id)
    queue = f"db-requeue-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(
                task_id=task_id, batch_size=3, max_concurrent=1, poll_interval_seconds=1
            ),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        try:
            assert await sync_to_async(provider_started.wait)(10)
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await handle.signal("pause")
            release_provider.set()

            async def deferred():
                return await handle.query("phase") == "waiting_for_database"

            await _until(deferred)
            assert len(requeue_calls) >= CONTROL_ATTEMPTS
            assert len(provider_calls) == 1
            assert await _task_status(task_id) == EvalTaskStatus.PAUSED
            database_restored.set()
            await asyncio.wait_for(handle.result(), 10)
            counts = await _status_counts(task_id)
            assert counts["completed"] == 1
            assert counts["pending"] == 2
            assert counts["running"] == 0
            assert len(provider_calls) == 1
        finally:
            release_provider.set()
            await handle.cancel()


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_database_outage_continues_as_new_before_any_batch_finishes(
    workflow_environment,
    eval_task,
    make_pending_entries,
    monkeypatch,
):
    from temporalio.client import WorkflowExecutionStatus

    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    _patch_completing_run_entry(monkeypatch)
    monkeypatch.setattr(wf, "_MAX_CONTINUOUS_POLLS", 1)
    original = activities._get_task_state_sync
    restored = threading.Event()
    attempts = []

    def unavailable(*args):
        if not restored.is_set():
            attempts.append(1)
            raise _db_error("timeout")
        return original(*args)

    monkeypatch.setattr(activities, "_get_task_state_sync", unavailable)
    await sync_to_async(make_pending_entries)(eval_task, 1)
    task_id = str(eval_task.id)
    queue = f"db-rollover-{uuid.uuid4().hex}"
    async with Worker(
        workflow_environment.client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            wf.ContinuousEvalTaskWorkflow.run,
            ContinuousDrainState(task_id=task_id, poll_interval_seconds=1),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )
        first = workflow_environment.client.get_workflow_handle(
            handle.id,
            run_id=handle.first_execution_run_id,
        )
        try:

            async def rolled_over():
                return (
                    await first.describe()
                ).status == WorkflowExecutionStatus.CONTINUED_AS_NEW

            await _until(rolled_over)
            assert len(attempts) >= CONTROL_ATTEMPTS
            assert await _task_status(task_id) == EvalTaskStatus.PENDING
            assert (await _status_counts(task_id))["completed"] == 0
            history = await first.fetch_history()
            assert len(history.events) < 40
            await Replayer(
                workflows=[wf.ContinuousEvalTaskWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
            restored.set()

            async def completed():
                return (await _status_counts(task_id))["completed"] == 1

            await _until(completed)
            assert (await _status_counts(task_id))["total"] == 1
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await asyncio.wait_for(handle.result(), 10)
        finally:
            await handle.cancel()


@pytest.mark.parametrize("kind,non_retryable", [("read_only", False), ("lost", True)])
def test_claim_transaction_distinguishes_confirmed_rollback_from_unknown_commit(
    kind, non_retryable
):
    from temporalio.exceptions import ApplicationError

    from tfc.temporal.eval_tasks.db_errors import database_control_errors

    with pytest.raises(ApplicationError) as error:
        with database_control_errors(
            task_id="synthetic", control_activity="claim", claim=True
        ):
            raise _db_error(kind)
    assert error.value.non_retryable is non_retryable
    assert error.value.type == (
        "EvalTaskClaimOutcomeUnknown"
        if non_retryable
        else "EvalTaskDatabaseUnavailable"
    )


@workflow.defn(name="ContinuousEvalTaskWorkflow")
class LegacyContinuous(wf.ContinuousEvalTaskWorkflow):
    @workflow.run
    async def run(self, state: ContinuousDrainState) -> None:
        try:
            await self._run(state)
        except Exception:
            await wf._fail_task(state.task_id)
            raise


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
async def test_open_legacy_execution_adopts_backoff_on_first_new_outage(
    workflow_environment,
    eval_task,
    make_pending_entries,
    monkeypatch,
):
    _fast_control_retries(monkeypatch)
    _patch_noop_reconcile(monkeypatch)
    _patch_completing_run_entry(monkeypatch)
    task_id = str(eval_task.id)
    queue = f"db-live-upgrade-{uuid.uuid4().hex}"
    client = workflow_environment.client
    async with Worker(
        client,
        task_queue=queue,
        workflows=[LegacyContinuous],
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await client.start_workflow(
            LegacyContinuous.run,
            ContinuousDrainState(task_id=task_id, poll_interval_seconds=1),
            id=f"eval-task-{task_id}",
            task_queue=queue,
        )

        async def sleeping():
            return await handle.query("phase") == "sleeping"

        await _until(sleeping)
    # Replace the worker, not the execution. Its recorded start has no new
    # patch marker and must replay the legacy command sequence on this worker.
    restored = threading.Event()
    original = activities._reconcile_sync

    def unavailable(*args):
        if not restored.is_set():
            raise _db_error("refused")
        return original(*args)

    monkeypatch.setattr(activities, "_reconcile_sync", unavailable)
    async with Worker(
        client,
        task_queue=queue,
        workflows=get_workflows(),
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        try:
            await handle.signal("request_recheck")

            async def deferred():
                assert await _task_status(task_id) == EvalTaskStatus.RUNNING
                return await handle.query("phase") == "waiting_for_database"

            await _until(deferred)
            await sync_to_async(make_pending_entries)(eval_task, 1)
            restored.set()

            async def completed():
                return (await _status_counts(task_id))["completed"] == 1

            await _until(completed)
            assert (await handle.describe()).run_id == handle.first_execution_run_id
            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await handle.signal("pause")
            await asyncio.wait_for(handle.result(), 10)
            history = await handle.fetch_history()
            assert any(
                wf._CONTINUOUS_DATABASE_ADOPTION_PATCH.encode() in payload.data
                for event in history.events
                for detail in event.marker_recorded_event_attributes.details.values()
                for payload in detail.payloads
            )
            await Replayer(
                workflows=[wf.ContinuousEvalTaskWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
        finally:
            await handle.cancel()
