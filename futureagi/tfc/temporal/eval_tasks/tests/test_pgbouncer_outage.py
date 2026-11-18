"""PostgreSQL unavailable behind a healthy PgBouncer is an outage, not a failure.

PgBouncer answers with SQLSTATE 08P01 when PostgreSQL behind it is down, paused
or disabled, the same code PostgreSQL uses for a real protocol violation. These
tests run a real PgBouncer (``pgbouncer`` on PATH, or PGBOUNCER_BIN) in front of
the test PostgreSQL. In the workflow test only activity connections go through
it, as only the worker did in the production repro; the test reads rows
directly.
"""

import asyncio
import threading
import uuid
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import psycopg
import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from temporalio import activity
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import activities, get_activities, get_workflows
from tfc.temporal.eval_tasks import workflows as wf
from tfc.temporal.eval_tasks.db_errors import is_transient_database_error
from tfc.temporal.eval_tasks.tests._pgbouncer import (
    PGBOUNCER_BIN,
    Gate,
    Outage,
    PgBouncer,
)
from tfc.temporal.eval_tasks.tests.test_workflows import (
    _patch_noop_reconcile,
    _set_status,
    _status_counts,
    _task_status,
)
from tfc.temporal.eval_tasks.types import ContinuousDrainState
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger

pytestmark = pytest.mark.skipif(
    PGBOUNCER_BIN is None, reason="needs a pgbouncer binary (PATH or PGBOUNCER_BIN)"
)

# What PgBouncer says in each outage (seen with PgBouncer 1.22).
_SAID = {
    "postgres-stopped": "(server_login_retry)",
    "paused": "query_wait_timeout",
    "disabled": "is disabled",
}


@pytest.fixture
def pgbouncer_route(db, tmp_path):
    params = connections["default"].get_connection_params()
    gate = Gate((params.get("host") or "localhost", int(params.get("port") or 5432)))
    try:
        bouncer = PgBouncer(
            tmp_path,
            backend_port=gate.port,
            user=params["user"],
            password=params["password"],
        )
    except BaseException:
        gate.shut()
        raise

    def connect(**overrides):
        return psycopg.connect(
            **{
                "host": "127.0.0.1",
                "port": bouncer.port,
                "dbname": params["dbname"],
                "user": params["user"],
                "autocommit": True,
                "connect_timeout": 10,
                **overrides,
            }
        )

    try:
        yield SimpleNamespace(
            gate=gate, bouncer=bouncer, dbname=params["dbname"], connect=connect
        )
    finally:
        bouncer.stop()
        gate.shut()


def _refusal(statement) -> psycopg.Error:
    with pytest.raises(psycopg.Error) as caught:
        statement()
    return caught.value


def _shape(exc) -> str:
    return f"{type(exc).__name__}[{exc.sqlstate}] {exc}"


@pytest.mark.parametrize("kind", Outage.KINDS)
def test_real_pgbouncer_outage_refusals_are_transient(pgbouncer_route, kind):
    route = pgbouncer_route

    def query_new_connection():
        with route.connect() as conn:
            conn.execute("SELECT 1")

    held = route.connect()
    held.execute("SELECT 1")  # the pool has served PostgreSQL before the outage
    outage = Outage(kind, route.gate, route.bouncer, route.dbname)
    outage.start()
    try:
        refusals = [
            _refusal(lambda: held.execute("SELECT 1")),
            _refusal(query_new_connection),
            _refusal(query_new_connection),
        ]
    finally:
        outage.end()
        held.close()
    shapes = [_shape(exc) for exc in refusals]
    assert any(_SAID[kind] in str(exc) for exc in refusals), shapes
    assert all(is_transient_database_error(exc) for exc in refusals), shapes


def test_real_protocol_violations_and_pooler_refusals_stay_failures(pgbouncer_route):
    route = pgbouncer_route
    with route.connect() as conn:
        # PostgreSQL's own 08P01: the bind carries fewer parameters than $2 needs.
        violation = _refusal(lambda: conn.execute("SELECT %s::int + $2::int", (1,)))
    refusals = [
        violation,
        _refusal(lambda: route.connect(options="-c work_mem=4MB")),
        _refusal(lambda: route.connect(user="synthetic_unknown_user")),
    ]
    shapes = [_shape(exc) for exc in refusals]
    assert violation.sqlstate == "08P01", shapes
    assert not any(is_transient_database_error(exc) for exc in refusals), shapes


def _driver(exc):
    while exc is not None and not isinstance(exc, psycopg.Error):
        exc = exc.__cause__
    return exc


async def _evaluated(handle, task_id, entry_done: threading.Event, timeout=120):
    """Wait for one entry's result; a workflow that closes instead fails at once."""
    closed = asyncio.ensure_future(handle.result())
    evaluated = asyncio.ensure_future(asyncio.to_thread(entry_done.wait, timeout))
    done, _ = await asyncio.wait(
        {closed, evaluated}, return_when=asyncio.FIRST_COMPLETED
    )
    if closed in done:
        entry_done.set()  # release the waiting thread
        outcome = repr(closed.exception() or closed.result())
        status = await _task_status(task_id)
        pytest.fail(
            f"workflow closed during a PgBouncer outage ({outcome}); task {status}"
        )
    closed.cancel()
    assert evaluated.result(), f"entry not evaluated within {timeout}s"
    assert await _task_status(task_id) != EvalTaskStatus.FAILED


@pytest.mark.e2e
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", Outage.KINDS)
async def test_continuous_task_waits_out_pgbouncer_outage(
    workflow_environment,
    eval_task,
    make_pending_entries,
    pgbouncer_route,
    monkeypatch,
    kind,
):
    """Before the fix the workflow FAILED (and the task, once the write landed).

    The outage ends when the state read has used its whole retry budget, the
    moment the old code gave up and wrote FAILED.
    """
    route = pgbouncer_route
    outage = Outage(kind, route.gate, route.bouncer, route.dbname)
    original_connect = DatabaseWrapper.get_new_connection

    def worker_through_pgbouncer(self, conn_params):
        if self.alias == "default" and activity.in_activity():
            conn_params = {
                **conn_params,
                "host": "127.0.0.1",
                "port": str(route.bouncer.port),
            }
        return original_connect(self, conn_params)

    monkeypatch.setattr(DatabaseWrapper, "get_new_connection", worker_through_pgbouncer)
    # As production settings do behind a transaction pooler.
    monkeypatch.setitem(
        connections.settings["default"], "DISABLE_SERVER_SIDE_CURSORS", True
    )
    # As many attempts as production, half a second apart.
    attempts = wf.CONTROL_RETRY_POLICY.maximum_attempts
    monkeypatch.setattr(
        wf,
        "CONTROL_RETRY_POLICY",
        RetryPolicy(
            initial_interval=timedelta(milliseconds=500),
            maximum_interval=timedelta(milliseconds=500),
            backoff_coefficient=1.0,
            maximum_attempts=attempts,
        ),
    )
    _patch_noop_reconcile(monkeypatch)
    lock = threading.Lock()
    completions = Counter()
    done = {}

    def entry_done(entry_id) -> threading.Event:
        with lock:
            return done.setdefault(str(entry_id), threading.Event())

    def complete(entry):
        EvalLogger.objects.filter(id=entry.id).update(
            status=EvalEntryStatus.COMPLETED, config_hash="0" * 64, error=False
        )
        with lock:
            completions[str(entry.id)] += 1
        entry_done(entry.id).set()
        return EvalEntryStatus.COMPLETED

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", complete)
    refusals = []
    read_state = activities._get_task_state_sync

    def state_read(*args):
        try:
            return read_state(*args)
        except Exception as exc:
            refusals.append(exc)
            if len(refusals) == attempts:
                outage.end()
            raise

    monkeypatch.setattr(activities, "_get_task_state_sync", state_read)

    task_id = str(eval_task.id)
    await sync_to_async(EvalTask.objects.filter(id=task_id).update)(
        run_type=RunType.CONTINUOUS, continuous_cursor=eval_task.created_at
    )
    (before,) = await sync_to_async(make_pending_entries)(eval_task, 1)
    queue = f"pgbouncer-outage-{uuid.uuid4().hex}"
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
            await _evaluated(handle, task_id, entry_done(before.id))
            assert any("login attempt" in line for line in route.bouncer.log)
            await sync_to_async(outage.start)()
            (during,) = await sync_to_async(make_pending_entries)(eval_task, 1)
            await _evaluated(handle, task_id, entry_done(during.id))

            drivers = [_driver(exc) for exc in refusals]
            shapes = [
                _shape(d) if d else repr(exc)
                for d, exc in zip(drivers, refusals, strict=True)
            ]
            print(f"pgbouncer_outage={kind} state_read_refusals={shapes}")
            assert len(drivers) >= attempts, shapes
            assert all(d is not None and d.sqlstate in ("08P01", None) for d in drivers)
            assert any(_SAID[kind] in str(d) for d in drivers), shapes
            assert completions == Counter({str(before.id): 1, str(during.id): 1})
            counts = await _status_counts(task_id)
            assert counts["completed"] == counts["total"] == 2, counts
            assert await _task_status(task_id) == EvalTaskStatus.RUNNING

            await _set_status(task_id, EvalTaskStatus.PAUSED)
            await handle.signal("pause")
            await asyncio.wait_for(handle.result(), timeout=10)
            history = await handle.fetch_history()
            assert "EvalTaskDatabaseUnavailable" in history.to_json()
            await Replayer(
                workflows=[wf.ContinuousEvalTaskWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
        finally:
            await sync_to_async(outage.end)()
            await handle.cancel()
