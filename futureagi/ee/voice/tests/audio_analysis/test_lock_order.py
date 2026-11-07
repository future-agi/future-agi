"""PostgreSQL regressions for parent-first audio writes against native regrades."""

import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from queue import Queue
from threading import Event
from time import monotonic

import pytest
from django.db import DatabaseError, connection, connections, transaction

from ee.voice.services.audio_analysis.commit import commit_envelope
from ee.voice.services.audio_analysis.errors import StaleAnalysisError
from ee.voice.temporal.activities import audio_analysis as aa
from ee.voice.tests.audio_analysis import test_schedule
from simulate.models.test_execution import CallExecution

audio_call = test_schedule.audio_call
eligible_call = test_schedule.eligible_call
pytestmark = pytest.mark.django_db(transaction=True)
BOUNDARIES = ("commit_envelope", "commit_result", "finalizer", "schedule")


def audio_write(call, boundary):
    scheduled = test_schedule.schedule(call)
    call.refresh_from_db()
    input = aa.AnalyzeCallAudioInput(
        str(call.id),
        scheduled.analysis_id,
        scheduled.generation,
        scheduled.deadline_at,
    )
    terminal = aa._terminal_pending(copy.deepcopy(call.audio_metrics), "timeout")

    def write():
        try:
            if boundary == "commit_envelope":
                commit_envelope(str(call.id), 0, input.analysis_id, terminal)
            elif boundary == "commit_result":
                aa._commit_result(input, terminal, provenance=call.audio_provenance)
            elif boundary == "finalizer":
                aa._finalize_audio_analysis_failure(
                    aa.FinalizeFailureInput(
                        str(call.id), input.analysis_id, 0, "timeout"
                    )
                )
            else:
                return aa._schedule_audio_analysis(
                    aa.ScheduleAudioAnalysisInput(str(call.id))
                ).skip_reason
        except StaleAnalysisError as exc:
            return exc.reason
        return None

    return write


@contextmanager
def blocked_audio_write(call, boundary, ancestor):
    """Connection A owns a parent while connection B enters the real writer."""
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    write = audio_write(call, boundary)
    execution = call.test_execution
    parent = execution.run_test if ancestor == "run" else execution
    pids = Queue()

    def worker():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            return write()
        finally:
            connections.close_all()

    # The parent transaction must exit before joining the blocked worker, also
    # when an assertion fails; otherwise test cleanup would itself deadlock.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            if ancestor == "execution":
                # run_regrade.regrade_run_evals claims this row with an UPDATE
                # and later updates the call in the same transaction.
                type(parent).objects.filter(pk=parent.pk).update(status="evaluating")
            else:
                type(parent).objects.select_for_update(of=("self",)).get(pk=parent.pk)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                parent_pid = cursor.fetchone()[0]
            future = pool.submit(worker)
            worker_pid = pids.get(timeout=10)
            assert worker_pid != parent_pid
            deadline = monotonic() + 10
            with connection.cursor() as cursor:
                while True:
                    cursor.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                        [parent_pid, worker_pid],
                    )
                    if cursor.fetchone()[0]:
                        break
                    if future.done():
                        pytest.fail(f"Audio writer did not wait: {future.result()}")
                    assert monotonic() < deadline, (
                        "Audio writer never blocked on parent"
                    )
                    Event().wait(0.01)
            yield parent, future
        # Surface worker errors only after A has released its locks.
        future.result(timeout=20)


def assert_child_unlocked(child):
    state = None
    try:
        with transaction.atomic():
            type(child).objects.select_for_update(of=("self",), nowait=True).get(
                pk=child.pk
            )
    except DatabaseError as exc:
        state = (
            getattr(exc.__cause__, "sqlstate", None)
            or getattr(exc.__cause__, "pgcode", None)
            or type(exc).__name__
        )
    assert state is None, (
        f"Audio acquired {type(child).__name__} before its parent: SQLSTATE {state}; "
        "the parent writer cannot finish its child update"
    )


@pytest.mark.parametrize("ancestor", ["run", "execution"])
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_parent_first_locks_leave_regrade_call_unlocked(
    eligible_call, ancestor, boundary
):
    with blocked_audio_write(eligible_call, boundary, ancestor) as (_, future):
        if ancestor == "run":
            assert_child_unlocked(eligible_call.test_execution)
        assert_child_unlocked(eligible_call)
    assert future.result() is None
    eligible_call.refresh_from_db()
    expected = "pending" if boundary == "schedule" else "failed"
    assert eligible_call.audio_metrics["state"] == expected


@pytest.mark.parametrize("ancestor", ["run", "execution"])
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_parent_deleted_while_waiting_blocks_write(eligible_call, ancestor, boundary):
    with blocked_audio_write(eligible_call, boundary, ancestor) as (parent, future):
        original = copy.deepcopy(eligible_call.audio_metrics)
        type(parent).objects.filter(pk=parent.pk).update(deleted=True)
    expected = {
        "schedule": "missing_call",
        "finalizer": None,
    }.get(boundary, "tombstone")
    assert future.result() == expected
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == original


@pytest.mark.parametrize("boundary", ["commit_envelope", "commit_result", "finalizer"])
@pytest.mark.parametrize("change", ["generation", "identity"])
def test_identity_rechecked_after_parent_wait(eligible_call, boundary, change):
    with blocked_audio_write(eligible_call, boundary, "execution") as (_, future):
        original = copy.deepcopy(eligible_call.audio_metrics)
        if change == "generation":
            CallExecution.objects.filter(pk=eligible_call.pk).update(
                audio_analysis_generation=1
            )
        else:
            original["analysis_id"] = "replacement"
            CallExecution.objects.filter(pk=eligible_call.pk).update(
                audio_metrics=original
            )
    assert future.result() == (None if boundary == "finalizer" else f"stale_{change}")
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == original


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("change", ["run", "execution"])
def test_parent_ids_rechecked_after_wait(eligible_call, boundary, change):
    with blocked_audio_write(eligible_call, boundary, "run") as (run, future):
        original = copy.deepcopy(eligible_call.audio_metrics)
        execution = eligible_call.test_execution
        if change == "run":
            replacement = type(run).objects.create(
                name="Replacement run",
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                agent_definition_id=run.agent_definition_id,
            )
            type(execution).objects.filter(pk=execution.pk).update(run_test=replacement)
        else:
            replacement = type(execution).objects.create(
                run_test=run, agent_definition_id=execution.agent_definition_id
            )
            CallExecution.objects.filter(pk=eligible_call.pk).update(
                test_execution=replacement
            )
    expected = {
        "schedule": "missing_call",
        "finalizer": None,
    }.get(boundary, "stale_identity")
    assert future.result() == expected
    eligible_call.refresh_from_db()
    assert eligible_call.audio_metrics == original
