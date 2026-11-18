"""Grading chosen calls of a finished run again through the re-grade service:
which calls are used, the snapshot saved inside the claim, the placeholder
written over a harness verdict, and what a failed dispatch puts back.
"""

from __future__ import annotations

import contextlib
import copy
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from structlog.testing import capture_logs

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
    HostedHarnessJob,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.services.kept_harness_entry import KEPT_KEY, pending_placeholder
from simulate.services.run_regrade import (
    RegradeAlreadyRunning,
    RegradeDispatchFailed,
    RegradeNoCompletedCall,
    RegradeNothingToGrade,
    regrade_run_evals,
)

COMPLETED = TestExecution.ExecutionStatus.COMPLETED
EVALUATING = TestExecution.ExecutionStatus.EVALUATING
RUNNING = TestExecution.ExecutionStatus.RUNNING
CALL_COMPLETED = CallExecution.CallStatus.COMPLETED
CALL_FAILED = CallExecution.CallStatus.FAILED

# Values no log line may carry: they stand for what a call said and scored.
SECRET_REASON = "reason-that-must-never-be-logged"
SECRET_METADATA = "metadata-that-must-never-be-logged"


@pytest.fixture
def run_test(db, organization, workspace):
    return RunTest.objects.create(
        name="Regrade calls", organization=organization, workspace=workspace
    )


@pytest.fixture
def scenario(db, organization, workspace):
    return Scenarios.objects.create(
        name="Scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def mapped_eval(run_test, organization):
    """An eval with its own mapping, which the platform can always grade."""
    template = EvalTemplate.objects.create(
        name="politeness_regrade_calls",
        config={"required_keys": ["output"], "output": "Pass/Fail"},
        organization=organization,
    )
    return SimulateEvalConfig.objects.create(
        name="Politeness",
        eval_template=template,
        run_test=run_test,
        config={},
        mapping={"output": "transcript"},
    )


@pytest.fixture
def dispatch():
    """Spy on the one grading job the service queues.

    Patches the attribute on the task object itself, which the service's
    module-level import shares.
    """
    with patch(
        "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
    ) as spy:
        yield spy


def _run(run_test, status=COMPLETED):
    return TestExecution.objects.create(
        run_test=run_test, status=status, total_scenarios=1, total_calls=1
    )


def _call(run, scenario, *, status=CALL_COMPLETED, eval_outputs=None, **fields):
    return CallExecution.objects.create(
        test_execution=run,
        scenario=scenario,
        phone_number="+1234567890",
        status=status,
        eval_outputs=eval_outputs if eval_outputs is not None else {},
        call_metadata={"eval_started": True, "eval_completed": True},
        **fields,
    )


def _harness_entry():
    """A verdict the harness wrote during the call."""
    return {
        "name": "Politeness",
        "output": "Passed",
        "output_type": "Pass/Fail",
        "reason": "The agent stayed polite.",
        "status": "completed",
        "source": "harness",
    }


def _platform_verdict(score=0.9):
    return {"output": score, "output_type": "score", "status": "completed"}


def _regrade(run_test, runs, configs, **keywords):
    return regrade_run_evals(
        run_test,
        [str(run.id) for run in runs],
        {str(run.id): COMPLETED for run in runs},
        [str(config.id) for config in configs],
        harness_run=True,
        **keywords,
    )


def _ids(*calls):
    return [str(call.id) for call in calls]


def _stored(call):
    call.refresh_from_db()
    return {
        "eval_outputs": call.eval_outputs,
        "call_metadata": call.call_metadata,
        "status": call.status,
    }


def _dispatched_call_ids(dispatch):
    dispatch.assert_called_once()
    call_ids, _ = dispatch.call_args.kwargs["args"]
    return sorted(call_ids)


@pytest.mark.django_db
def test_whole_runs_are_regraded_as_before(run_test, scenario, mapped_eval, dispatch):
    run = _run(run_test)
    calls = [
        _call(run, scenario),
        _call(run, scenario),
        _call(run, scenario, status=CALL_FAILED),
    ]

    queued = _regrade(run_test, [run], [mapped_eval])

    assert sorted(queued) == sorted(_ids(*calls))
    assert _dispatched_call_ids(dispatch) == sorted(_ids(*calls))
    for call in calls:
        call.refresh_from_db()
        assert call.eval_outputs == {str(mapped_eval.id): {"status": "pending"}}
    run.refresh_from_db()
    assert run.status == EVALUATING
    assert not CallExecutionSnapshot.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("selected", [False, True], ids=["whole-run", "selected-calls"])
def test_an_empty_eval_list_is_refused_before_anything_is_written(
    run_test, scenario, dispatch, django_assert_num_queries, selected
):
    run = _run(run_test)
    call = _call(run, scenario, eval_outputs={"other": _platform_verdict()})
    keywords = (
        {"call_execution_ids": _ids(call), "snapshot_eval_outputs": True}
        if selected
        else {}
    )
    before = _stored(call)

    with django_assert_num_queries(0), pytest.raises(RegradeNothingToGrade):
        _regrade(run_test, [run], [], **keywords)

    dispatch.assert_not_called()
    assert _stored(call) == before
    run.refresh_from_db()
    assert run.status == COMPLETED
    assert not CallExecutionSnapshot.objects.exists()


@pytest.mark.django_db
def test_only_the_selected_completed_calls_are_regraded(
    run_test, scenario, mapped_eval, organization, workspace, dispatch
):
    run = _run(run_test)
    first = _call(run, scenario, eval_outputs={"other": _platform_verdict()})
    second = _call(run, scenario)
    failed = _call(run, scenario, status=CALL_FAILED)
    unselected = _call(run, scenario, eval_outputs={"other": _platform_verdict()})
    other_run_test = RunTest.objects.create(
        name="Other run test", organization=organization, workspace=workspace
    )
    elsewhere = _call(_run(other_run_test), scenario)
    untouched = {call.id: _stored(call) for call in (failed, unselected, elsewhere)}

    queued = _regrade(
        run_test,
        [run],
        [mapped_eval],
        call_execution_ids=_ids(first, second, failed, elsewhere),
    )

    assert sorted(queued) == sorted(_ids(first, second))
    assert _dispatched_call_ids(dispatch) == sorted(_ids(first, second))
    for call in (first, second):
        call.refresh_from_db()
        assert call.eval_outputs[str(mapped_eval.id)] == {"status": "pending"}
        assert call.call_metadata["eval_started"] is True
    first.refresh_from_db()
    assert first.eval_outputs["other"] == _platform_verdict()
    for call in (failed, unselected, elsewhere):
        assert _stored(call) == untouched[call.id]


@pytest.mark.django_db
@pytest.mark.parametrize("selection", ["only-failed", "empty-list"])
def test_a_selection_with_no_completed_call_is_refused(
    run_test, scenario, mapped_eval, dispatch, selection
):
    run = _run(run_test)
    failed = _call(run, scenario, status=CALL_FAILED)
    # Today's whole-run check would pass this run because of this call.
    unselected = _call(run, scenario)
    before = {call.id: _stored(call) for call in (failed, unselected)}
    call_execution_ids = _ids(failed) if selection == "only-failed" else []

    with pytest.raises(RegradeNoCompletedCall) as refused:
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=call_execution_ids,
            snapshot_eval_outputs=True,
        )

    assert str(refused.value) == (
        "Nothing to grade again: no call in this run completed."
    )
    dispatch.assert_not_called()
    run.refresh_from_db()
    assert run.status == COMPLETED
    for call in (failed, unselected):
        assert _stored(call) == before[call.id]
    assert not CallExecutionSnapshot.objects.exists()


@pytest.mark.django_db
def test_a_selection_spanning_a_run_with_no_completed_call_is_refused(
    run_test, scenario, mapped_eval, dispatch
):
    graded_run = _run(run_test)
    graded = _call(graded_run, scenario)
    refused_run = _run(run_test)
    failed = _call(refused_run, scenario, status=CALL_FAILED)
    # Today's whole-run check would pass this run because of this call.
    unselected = _call(refused_run, scenario)
    before = {call.id: _stored(call) for call in (graded, failed, unselected)}

    with pytest.raises(RegradeNoCompletedCall) as refused:
        _regrade(
            run_test,
            [graded_run, refused_run],
            [mapped_eval],
            call_execution_ids=_ids(graded, failed),
            snapshot_eval_outputs=True,
        )

    assert str(refused.value) == (
        f"1 of the 2 selected runs has no completed call: {refused_run.id}."
    )
    dispatch.assert_not_called()
    for run in (graded_run, refused_run):
        run.refresh_from_db()
        assert run.status == COMPLETED
    for call in (graded, failed, unselected):
        assert _stored(call) == before[call.id]
    assert not CallExecutionSnapshot.objects.exists()


@pytest.mark.django_db
def test_a_snapshot_is_saved_per_call_inside_the_claim(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    harness_scored = _call(
        run,
        scenario,
        eval_outputs={
            str(mapped_eval.id): _harness_entry(),
            "other": _platform_verdict(),
        },
        provider_call_data={"vapi": {"call_id": "vapi-12345"}},
        overall_score=0.75,
        tool_outputs={"tool_call_accuracy": {"output": 1.0, "status": "completed"}},
    )
    platform_scored = _call(
        run,
        scenario,
        eval_outputs={str(mapped_eval.id): _platform_verdict(0.4)},
        overall_score=0.4,
    )
    failed = _call(run, scenario, status=CALL_FAILED)
    expected = {}
    for call in (harness_scored, platform_scored):
        call.refresh_from_db()
        expected[call.id] = {
            "eval_outputs": copy.deepcopy(call.eval_outputs),
            "provider_call_data": copy.deepcopy(call.provider_call_data),
            "overall_score": call.overall_score,
            "tool_outputs": copy.deepcopy(call.tool_outputs),
        }

    _regrade(
        run_test,
        [run],
        [mapped_eval],
        call_execution_ids=_ids(harness_scored, platform_scored, failed),
        snapshot_eval_outputs=True,
    )

    snapshots = list(CallExecutionSnapshot.objects.all())
    assert sorted(str(s.call_execution_id) for s in snapshots) == sorted(
        _ids(harness_scored, platform_scored)
    )
    for snapshot in snapshots:
        assert snapshot.rerun_type == CallExecutionSnapshot.RerunType.EVAL_ONLY
        assert {
            "eval_outputs": snapshot.eval_outputs,
            "provider_call_data": snapshot.provider_call_data,
            "overall_score": snapshot.overall_score,
            "tool_outputs": snapshot.tool_outputs,
        } == expected[snapshot.call_execution_id]
    harness_snapshot = next(
        s for s in snapshots if s.call_execution_id == harness_scored.id
    )
    assert harness_snapshot.eval_outputs[str(mapped_eval.id)] == _harness_entry()


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["lost-claim", "failed-write"])
def test_a_lost_claim_leaves_no_snapshot(
    run_test, scenario, mapped_eval, dispatch, failure
):
    run = _run(run_test)
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): _harness_entry()})
    before = _stored(call)

    if failure == "lost-claim":
        # Another request claimed the run after this one read it COMPLETED.
        TestExecution.objects.filter(id=run.id).update(status=EVALUATING)
        with pytest.raises(RegradeAlreadyRunning):
            _regrade(
                run_test,
                [run],
                [mapped_eval],
                call_execution_ids=_ids(call),
                snapshot_eval_outputs=True,
            )
        expected_status = EVALUATING
    else:
        with (
            patch.object(
                CallExecution.objects,
                "bulk_update",
                side_effect=OperationalError("statement timeout"),
            ),
            pytest.raises(OperationalError),
        ):
            _regrade(
                run_test,
                [run],
                [mapped_eval],
                call_execution_ids=_ids(call),
                snapshot_eval_outputs=True,
            )
        expected_status = COMPLETED

    dispatch.assert_not_called()
    assert not CallExecutionSnapshot.objects.exists()
    assert _stored(call) == before
    run.refresh_from_db()
    assert run.status == expected_status


@pytest.mark.django_db
@pytest.mark.parametrize("selected", [True, False], ids=["selected-calls", "whole-run"])
def test_the_placeholder_keeps_a_harness_entry(
    run_test, scenario, mapped_eval, organization, dispatch, selected
):
    platform_template = EvalTemplate.objects.create(
        name="tone_regrade_calls",
        config={"required_keys": ["output"], "output": "Pass/Fail"},
        organization=organization,
    )
    platform_eval = SimulateEvalConfig.objects.create(
        name="Tone",
        eval_template=platform_template,
        run_test=run_test,
        config={},
        mapping={"output": "transcript"},
    )
    run = _run(run_test)
    entry = _harness_entry()
    call = _call(
        run,
        scenario,
        eval_outputs={
            str(mapped_eval.id): entry,
            str(platform_eval.id): _platform_verdict(),
        },
    )
    keywords = {"call_execution_ids": _ids(call)} if selected else {}

    _regrade(run_test, [run], [mapped_eval, platform_eval], **keywords)

    call.refresh_from_db()
    cell = call.eval_outputs[str(mapped_eval.id)]
    assert cell == pending_placeholder(entry)
    assert cell["status"] == "pending"
    assert cell[KEPT_KEY] == entry
    assert call.eval_outputs[str(platform_eval.id)] == {"status": "pending"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "started_before",
    [None, False, True],
    ids=["start-never-set", "not-started", "started"],
)
def test_a_dispatch_failure_resets_only_a_run_still_evaluating(
    run_test, scenario, mapped_eval, dispatch, started_before
):
    moved_on = _run(run_test)
    moved_on_call = _call(
        moved_on, scenario, eval_outputs={str(mapped_eval.id): _harness_entry()}
    )
    still_evaluating = _run(run_test)
    still_evaluating_call = _call(
        still_evaluating,
        scenario,
        eval_outputs={str(mapped_eval.id): _platform_verdict()},
    )
    for call in (moved_on_call, still_evaluating_call):
        # The request writes eval_started=True; the put-back must bring back
        # whatever the call held, even a key that was never there.
        metadata = {"eval_completed": True}
        if started_before is not None:
            metadata["eval_started"] = started_before
        CallExecution.objects.filter(id=call.id).update(call_metadata=metadata)
    outputs_before = {
        call.id: _stored(call)["eval_outputs"]
        for call in (moved_on_call, still_evaluating_call)
    }

    def an_sdk_batch_lands_then_the_broker_fails(*args, **kwargs):
        TestExecution.objects.filter(id=moved_on.id).update(status=RUNNING)
        raise ConnectionError("broker unreachable")

    dispatch.side_effect = an_sdk_batch_lands_then_the_broker_fails

    with pytest.raises(RegradeDispatchFailed) as failed:
        _regrade(
            run_test,
            [moved_on, still_evaluating],
            [mapped_eval],
            call_execution_ids=_ids(moved_on_call, still_evaluating_call),
            snapshot_eval_outputs=True,
        )

    assert failed.value.call_execution_count == 2
    moved_on.refresh_from_db()
    still_evaluating.refresh_from_db()
    assert moved_on.status == RUNNING
    assert still_evaluating.status == COMPLETED
    assert still_evaluating.picked_up_by_executor is False
    for run in (moved_on, still_evaluating):
        assert run.execution_metadata["eval_dispatch_failed"] == "broker unreachable"
    for call in (moved_on_call, still_evaluating_call):
        call.refresh_from_db()
        assert call.eval_outputs == outputs_before[call.id]
        assert call.call_metadata.get("eval_started") == started_before
        assert ("eval_started" in call.call_metadata) is (started_before is not None)
        assert call.call_metadata["eval_completed"] is True
        assert call.call_metadata["eval_dispatch_failed"] == "broker unreachable"
    # The re-grade did not happen, so nothing is left to compare against.
    assert not CallExecutionSnapshot.objects.exists()


@pytest.mark.django_db
def test_a_dispatch_failure_puts_back_the_harness_entry(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    entry = _harness_entry()
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): entry})
    dispatch.side_effect = ConnectionError("broker unreachable")

    with pytest.raises(RegradeDispatchFailed):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            snapshot_eval_outputs=True,
        )

    call.refresh_from_db()
    assert call.eval_outputs == {str(mapped_eval.id): entry}
    run.refresh_from_db()
    assert run.status == COMPLETED


@pytest.mark.django_db
@pytest.mark.parametrize("dispatched", [True, False], ids=["queued", "dispatch-failed"])
def test_regrade_logs_no_output_values(
    run_test, scenario, mapped_eval, dispatch, dispatched
):
    run = _run(run_test)
    entry = {**_harness_entry(), "reason": SECRET_REASON}
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): entry})
    CallExecution.objects.filter(id=call.id).update(
        call_metadata={
            "eval_started": True,
            "eval_completed": True,
            "transcript_note": SECRET_METADATA,
        }
    )
    if not dispatched:
        dispatch.side_effect = ConnectionError("broker unreachable")
    outcome = (
        contextlib.nullcontext() if dispatched else pytest.raises(RegradeDispatchFailed)
    )

    with capture_logs() as logs, outcome:
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            snapshot_eval_outputs=True,
        )

    events = [str(log.get("event")) for log in logs]
    if dispatched:
        assert any("Triggered new evaluations task" in event for event in events)
    else:
        assert "run_new_evals_dispatch_failed" in events
    for log in logs:
        assert SECRET_REASON not in repr(log)
        assert SECRET_METADATA not in repr(log)


# --- lock order, the put-back, and the plan function -------------------------


def _sql_of(captured):
    return [query["sql"] for query in captured.captured_queries]


def _first_statement(statements, *needles, after=-1):
    """Index of the first statement past ``after`` holding every needle."""
    for index, sql in enumerate(statements):
        if index > after and all(needle in sql for needle in needles):
            return index
    raise AssertionError(f"no statement with {needles} after #{after}")


class _LockDepths:
    """The savepoint depth at every ``FOR UPDATE`` statement, to tell a lock
    taken inside the transaction from one taken outside it.

    These tests run inside the test's own transaction, so ``in_atomic_block``
    is always true. A lock outside the service's ``atomic()`` sits at the
    test's depth, and one inside it a savepoint deeper. In production that
    outside lock commits on its own and is released at once.
    """

    def __init__(self):
        self.outside = len(connection.savepoint_ids)
        self.depths = []
        # (is it the run test's lock, the savepoints opened since the test's
        # own) for each lock and each write to a run or a call.
        self.protected = []

    def __call__(self, execute, sql, params, many, context):
        is_lock = "FOR UPDATE" in sql
        if is_lock:
            self.depths.append(len(connection.savepoint_ids))
        if is_lock or sql.startswith(
            ('UPDATE "simulate_test_execution"', 'UPDATE "simulate_call_execution"')
        ):
            self.protected.append(
                (
                    is_lock and 'FROM "simulate_run_test"' in sql,
                    # A write batched without a savepoint pushes None.
                    tuple(
                        name
                        for name in connection.savepoint_ids[self.outside :]
                        if name is not None
                    ),
                )
            )
        return execute(sql, params, many, context)

    def assert_every_lock_is_inside_a_transaction(self):
        assert self.depths, "no locking statement was run"
        assert all(depth > self.outside for depth in self.depths), self.depths

    def assert_the_locks_and_writes_share_one_savepoint(self, run_test_lock=0):
        """A lock taken in a nested ``atomic()`` of its own sits deeper than
        the test's depth, so the depth check alone passes it. Every lock and
        write of one request must instead share the same outermost savepoint
        and the same innermost one: the request's single transaction.

        ``run_test_lock`` picks the request: the nth time the run test is
        locked starts it (the claim is 0, the put-back after it is 1).
        """
        starts = [i for i, (is_run_test, _) in enumerate(self.protected) if is_run_test]
        window = self.protected[starts[run_test_lock] :]
        assert window, "the request took no locks"
        assert all(names for _, names in window), window
        assert len({names[0] for _, names in window}) == 1, window
        assert len({names[-1] for _, names in window}) == 1, window


@contextlib.contextmanager
def _watching_lock_depths():
    watcher = _LockDepths()
    with connection.execute_wrapper(watcher):
        yield watcher


RUN_TEST_LOCK = ('FROM "simulate_run_test"', "FOR UPDATE")
RUN_LOCK = ('FROM "simulate_test_execution"', "FOR UPDATE")
CALL_LOCK = (
    'FROM "simulate_call_execution"',
    "FOR UPDATE OF",
    '"simulate_call_execution"."id" ASC',
)


@pytest.mark.django_db
def test_the_regrade_locks_the_run_test_then_claims_then_locks_the_calls(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    calls = [_call(run, scenario), _call(run, scenario)]

    with (
        CaptureQueriesContext(connection) as captured,
        _watching_lock_depths() as locks,
    ):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(*calls),
            snapshot_eval_outputs=True,
            enable_tool_evaluation=True,
        )

    sql = _sql_of(captured)
    run_test_lock = _first_statement(sql, *RUN_TEST_LOCK)
    claim = _first_statement(sql, 'UPDATE "simulate_test_execution"')
    call_lock = _first_statement(sql, *CALL_LOCK)
    call_write = _first_statement(sql, 'UPDATE "simulate_call_execution"')
    assert run_test_lock < claim < call_lock < call_write
    # A lock taken before the transaction opens is released at once, which
    # would let the order above mean nothing.
    locks.assert_every_lock_is_inside_a_transaction()
    locks.assert_the_locks_and_writes_share_one_savepoint()


@pytest.mark.django_db
def test_the_put_back_takes_the_same_locks_in_the_same_order(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): _harness_entry()})
    dispatch.side_effect = ConnectionError("broker unreachable")

    with (
        CaptureQueriesContext(connection) as captured,
        _watching_lock_depths() as locks,
        pytest.raises(RegradeDispatchFailed),
    ):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            snapshot_eval_outputs=True,
        )

    sql = _sql_of(captured)
    placeholders = _first_statement(sql, 'UPDATE "simulate_call_execution"')
    run_test_lock = _first_statement(sql, *RUN_TEST_LOCK, after=placeholders)
    run_lock = _first_statement(sql, *RUN_LOCK, after=run_test_lock)
    call_lock = _first_statement(sql, *CALL_LOCK, after=run_lock)
    put_back = _first_statement(
        sql, 'UPDATE "simulate_call_execution"', after=call_lock
    )
    assert run_test_lock < run_lock < call_lock < put_back
    locks.assert_every_lock_is_inside_a_transaction()
    locks.assert_the_locks_and_writes_share_one_savepoint(run_test_lock=1)


def _deadlock():
    """What Django raises when Postgres aborts this side of a deadlock."""
    from psycopg import errors

    error = OperationalError("deadlock detected")
    error.__cause__ = errors.DeadlockDetected("deadlock detected")
    return error


@contextlib.contextmanager
def _put_back_deadlocks(dispatch, times, error=None):
    """Make the call lock fail ``times`` times once the dispatch has failed,
    as it does when a grader finishing a call meets the put-back."""
    dispatch_failed = []

    def broker_down(*args, **kwargs):
        dispatch_failed.append(True)
        raise ConnectionError("broker unreachable")

    dispatch.side_effect = broker_down
    left = [times]

    def wrapper(execute, sql, params, many, context):
        if (
            dispatch_failed
            and left[0]
            and "FOR UPDATE" in sql
            and 'FROM "simulate_call_execution"' in sql
        ):
            left[0] -= 1
            raise error or _deadlock()
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        yield


@pytest.mark.django_db
def test_a_put_back_that_loses_a_deadlock_runs_again_and_answers_as_a_failed_dispatch(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): _harness_entry()})
    before = _stored(call)

    with _put_back_deadlocks(dispatch, times=1), pytest.raises(RegradeDispatchFailed):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            snapshot_eval_outputs=True,
        )

    after = _stored(call)
    assert after["eval_outputs"] == before["eval_outputs"]
    assert "eval_dispatch_failed" in after["call_metadata"]
    assert not CallExecutionSnapshot.objects.exists()
    run.refresh_from_db()
    assert run.status == COMPLETED


@pytest.mark.django_db
def test_a_put_back_that_deadlocks_twice_still_answers_as_a_failed_dispatch(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    call = _call(run, scenario, eval_outputs={str(mapped_eval.id): _harness_entry()})

    with (
        _put_back_deadlocks(dispatch, times=2),
        pytest.raises(RegradeDispatchFailed) as failed,
    ):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            snapshot_eval_outputs=True,
        )

    # The caller must not tell the user the earlier results are back.
    assert failed.value.put_back is False

    # Each failed attempt rolled back whole, so the claim stands as it was
    # committed: no marker, no half-restored cell.
    after = _stored(call)
    assert after["eval_outputs"][str(mapped_eval.id)]["status"] == "pending"
    assert "eval_dispatch_failed" not in after["call_metadata"]
    run.refresh_from_db()
    assert run.status == EVALUATING
    assert "eval_dispatch_failed" not in (run.execution_metadata or {})


@pytest.mark.django_db
def test_a_put_back_that_fails_for_another_database_reason_is_not_swallowed(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    call = _call(run, scenario)

    with (
        _put_back_deadlocks(dispatch, times=1, error=OperationalError("timeout")),
        pytest.raises(OperationalError),
    ):
        _regrade(run_test, [run], [mapped_eval], call_execution_ids=_ids(call))


@pytest.mark.django_db
def test_the_add_eval_put_back_takes_the_run_test_lock_first(
    run_test, scenario, mapped_eval
):
    from simulate.services.harness_run_evals import (
        EVAL_QUEUED_KEY,
        _clear_queued_stamps,
    )

    run = _run(run_test)
    call = _call(run, scenario)
    CallExecution.objects.filter(id=call.id).update(
        call_metadata={EVAL_QUEUED_KEY: {str(mapped_eval.id): "2026-01-01T00:00:00Z"}}
    )

    with (
        CaptureQueriesContext(connection) as captured,
        _watching_lock_depths() as locks,
    ):
        _clear_queued_stamps(
            _ids(call), str(mapped_eval.id), test_execution_id=str(run.id)
        )

    sql = _sql_of(captured)
    run_test_lock = _first_statement(sql, *RUN_TEST_LOCK)
    call_lock = _first_statement(sql, 'FROM "simulate_call_execution"', "FOR UPDATE")
    assert run_test_lock < call_lock
    locks.assert_every_lock_is_inside_a_transaction()
    locks.assert_the_locks_and_writes_share_one_savepoint()
    call.refresh_from_db()
    assert call.call_metadata[EVAL_QUEUED_KEY] == {}


@pytest.mark.django_db
def test_add_eval_with_an_already_bound_eval_locks_the_run_test_first(
    run_test, scenario, mapped_eval
):
    from simulate.services.harness_run_evals import queue_eval_for_finished_calls

    run = _run(run_test)
    _call(run, scenario)
    _call(run, scenario)

    # The eval is already bound, so the view never reaches add_selected_eval,
    # which is where the run test's lock is otherwise taken.
    with (
        CaptureQueriesContext(connection) as captured,
        _watching_lock_depths() as locks,
    ):
        counts = queue_eval_for_finished_calls(run, mapped_eval)

    assert counts["queued"] == 2
    sql = _sql_of(captured)
    run_test_lock = _first_statement(sql, *RUN_TEST_LOCK)
    call_lock = _first_statement(sql, *CALL_LOCK)
    run_write = _first_statement(sql, 'UPDATE "simulate_test_execution"')
    assert run_test_lock < call_lock < run_write
    locks.assert_every_lock_is_inside_a_transaction()
    locks.assert_the_locks_and_writes_share_one_savepoint()


@pytest.mark.django_db
def test_a_failed_dispatch_leaves_no_snapshot_and_a_retry_adds_one_per_call(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    calls = [_call(run, scenario), _call(run, scenario)]
    keywords = {"call_execution_ids": _ids(*calls), "snapshot_eval_outputs": True}
    dispatch.side_effect = ConnectionError("broker unreachable")

    for _ in range(2):
        with pytest.raises(RegradeDispatchFailed):
            _regrade(run_test, [run], [mapped_eval], **keywords)
        assert not CallExecutionSnapshot.objects.exists()

    dispatch.side_effect = None
    _regrade(run_test, [run], [mapped_eval], **keywords)
    assert CallExecutionSnapshot.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "other_call_still_graded", [True, False], ids=["still-graded", "nothing-open"]
)
def test_a_put_back_returns_the_run_to_completed_only_when_no_call_is_still_graded(
    run_test, scenario, mapped_eval, dispatch, other_call_still_graded
):
    run = _run(run_test)
    regraded = _call(run, scenario)
    # Another writer's config is open on a call this request did not select.
    other = _call(run, scenario, eval_outputs={"other": {"status": "pending"}})
    CallExecution.objects.filter(id=other.id).update(
        call_metadata={
            "eval_started": True,
            "eval_completed": not other_call_still_graded,
        }
    )
    dispatch.side_effect = ConnectionError("broker unreachable")

    with pytest.raises(RegradeDispatchFailed):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(regraded),
            snapshot_eval_outputs=True,
        )

    run.refresh_from_db()
    # A run that read completed while a call of it was still being graded
    # would never be settled when that grading finished.
    assert run.status == (EVALUATING if other_call_still_graded else COMPLETED)
    assert run.execution_metadata["eval_dispatch_failed"] == "broker unreachable"
    regraded.refresh_from_db()
    assert "eval_dispatch_failed" in regraded.call_metadata


@pytest.mark.django_db
def test_each_runs_put_back_guard_excludes_only_its_own_restored_calls(
    run_test, scenario, mapped_eval, dispatch
):
    """The guard is one UPDATE per run. If every UPDATE carried the ids of
    every run's restored calls, a request over many runs would cost runs times
    calls while it holds all the locks."""
    runs = [_run(run_test) for _ in range(3)]
    calls_of = {run.id: [_call(run, scenario), _call(run, scenario)] for run in runs}
    all_calls = [call for calls in calls_of.values() for call in calls]
    dispatch.side_effect = ConnectionError("broker unreachable")

    with (
        CaptureQueriesContext(connection) as captured,
        pytest.raises(RegradeDispatchFailed),
    ):
        _regrade(
            run_test,
            runs,
            [mapped_eval],
            call_execution_ids=_ids(*all_calls),
            snapshot_eval_outputs=True,
        )

    guards = [
        sql
        for sql in _sql_of(captured)
        if sql.startswith('UPDATE "simulate_test_execution" SET "status"')
        and 'FROM "simulate_call_execution"' in sql
    ]
    assert len(guards) == len(runs), guards
    for run in runs:
        # Ids reach the SQL as dashless hex.
        (guard,) = [sql for sql in guards if f"'{run.id.hex}'" in sql]
        own = {call.id for call in calls_of[run.id]}
        mentioned = {call.id for call in all_calls if f"'{call.id.hex}'" in guard}
        assert mentioned == own
    for run in runs:
        run.refresh_from_db()
        assert run.status == COMPLETED


@pytest.mark.django_db
def test_passing_over_runs_needs_the_calls_to_judge_them_by(
    run_test, scenario, mapped_eval, dispatch
):
    """Without call ids, the runs that would be passed over are never worked
    out, yet their calls would be stamped and dispatched."""
    claimed = _run(run_test)
    unclaimed = _run(run_test)
    kept = _call(claimed, scenario)
    other = _call(unclaimed, scenario, status=CALL_FAILED)

    with pytest.raises(TypeError):
        _regrade(
            run_test,
            [claimed, unclaimed],
            [mapped_eval],
            skip_runs_without_a_completed_call=True,
        )

    dispatch.assert_not_called()
    assert _stored(kept)["eval_outputs"] == {}
    assert _stored(other)["eval_outputs"] == {}
    for run in (claimed, unclaimed):
        run.refresh_from_db()
        assert run.status == COMPLETED


def _a_call_changes_before_the_lock(change):
    """Patch the service's ``atomic`` so ``change()`` runs once, just before
    the claim's transaction opens: after the plan, before the lock."""
    from django.db import transaction as db_transaction

    real_atomic = db_transaction.atomic
    seen = []

    def atomic_after_a_change(*args, **kwargs):
        if not seen:
            seen.append(True)
            change()
        return real_atomic(*args, **kwargs)

    return patch(
        "simulate.services.run_regrade.transaction.atomic",
        side_effect=atomic_after_a_change,
    )


@pytest.mark.django_db
def test_the_service_reports_the_calls_it_stamped(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    kept = _call(run, scenario)
    deleted = _call(run, scenario)

    with _a_call_changes_before_the_lock(
        lambda: CallExecution.objects.filter(id=deleted.id).update(deleted=True)
    ):
        stamped = _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(kept, deleted),
        )

    assert stamped == _ids(kept)
    assert _dispatched_call_ids(dispatch) == _ids(kept)


@pytest.mark.django_db
def test_a_run_that_lost_its_completed_calls_before_the_lock_can_be_passed_over(
    run_test, scenario, mapped_eval, dispatch
):
    kept_run = _run(run_test)
    kept = _call(kept_run, scenario)
    lost_run = _run(run_test)
    lost = _call(lost_run, scenario)
    lost_before = _stored(lost)

    with _a_call_changes_before_the_lock(
        lambda: CallExecution.objects.filter(id=lost.id).update(
            status=CallExecution.CallStatus.PENDING
        )
    ):
        stamped = _regrade(
            run_test,
            [kept_run, lost_run],
            [mapped_eval],
            call_execution_ids=_ids(kept, lost),
            snapshot_eval_outputs=True,
            skip_runs_without_a_completed_call=True,
        )

    assert stamped == _ids(kept)
    assert _dispatched_call_ids(dispatch) == _ids(kept)
    kept_run.refresh_from_db()
    lost_run.refresh_from_db()
    assert kept_run.status == EVALUATING
    # Left as it was: no claim, no column, no snapshot.
    assert lost_run.status == COMPLETED
    assert lost_run.picked_up_by_executor is False
    assert "column_order" not in (lost_run.execution_metadata or {})
    assert _stored(lost)["eval_outputs"] == lost_before["eval_outputs"]
    assert not CallExecutionSnapshot.objects.filter(call_execution=lost).exists()
    assert CallExecutionSnapshot.objects.filter(call_execution=kept).count() == 1


@pytest.mark.django_db
def test_a_run_claimed_by_another_request_before_the_claim_can_be_passed_over(
    run_test, scenario, mapped_eval, dispatch
):
    mine = _run(run_test)
    taken = _run(run_test)
    kept = _call(mine, scenario)
    other = _call(taken, scenario)
    other_before = _stored(other)
    claimed_elsewhere = []

    with _a_call_changes_before_the_lock(
        lambda: TestExecution.objects.filter(id=taken.id).update(status=EVALUATING)
    ):
        stamped = _regrade(
            run_test,
            [mine, taken],
            [mapped_eval],
            call_execution_ids=_ids(kept, other),
            snapshot_eval_outputs=True,
            skip_runs_without_a_completed_call=True,
            claimed_elsewhere=claimed_elsewhere,
        )

    assert stamped == _ids(kept)
    assert claimed_elsewhere == [str(taken.id)]
    assert _dispatched_call_ids(dispatch) == _ids(kept)
    mine.refresh_from_db()
    taken.refresh_from_db()
    assert mine.status == EVALUATING
    # The other request's claim is untouched: no column, no snapshot, no
    # placeholder from this one.
    assert taken.status == EVALUATING
    assert "column_order" not in (taken.execution_metadata or {})
    assert _stored(other)["eval_outputs"] == other_before["eval_outputs"]
    assert not CallExecutionSnapshot.objects.filter(call_execution=other).exists()
    assert CallExecutionSnapshot.objects.filter(call_execution=kept).count() == 1


@pytest.mark.django_db
def test_every_run_claimed_elsewhere_still_refuses_when_runs_can_be_passed_over(
    run_test, scenario, mapped_eval, dispatch
):
    runs = [_run(run_test), _run(run_test)]
    calls = [_call(run, scenario) for run in runs]

    with _a_call_changes_before_the_lock(
        lambda: TestExecution.objects.filter(id__in=[r.id for r in runs]).update(
            status=EVALUATING
        )
    ):
        with pytest.raises(RegradeAlreadyRunning):
            _regrade(
                run_test,
                runs,
                [mapped_eval],
                call_execution_ids=_ids(*calls),
                skip_runs_without_a_completed_call=True,
            )

    dispatch.assert_not_called()


@pytest.mark.django_db
def test_a_run_claimed_elsewhere_still_refuses_a_request_that_named_its_runs(
    run_test, scenario, mapped_eval, dispatch
):
    mine = _run(run_test)
    taken = _run(run_test)
    calls = [_call(mine, scenario), _call(taken, scenario)]

    with _a_call_changes_before_the_lock(
        lambda: TestExecution.objects.filter(id=taken.id).update(status=EVALUATING)
    ):
        with pytest.raises(RegradeAlreadyRunning):
            _regrade(
                run_test, [mine, taken], [mapped_eval], call_execution_ids=_ids(*calls)
            )

    dispatch.assert_not_called()
    mine.refresh_from_db()
    assert mine.status == COMPLETED


@pytest.mark.django_db
def test_every_run_lost_before_the_lock_refuses_even_when_runs_can_be_passed_over(
    run_test, scenario, mapped_eval, dispatch
):
    run = _run(run_test)
    call = _call(run, scenario)

    with (
        _a_call_changes_before_the_lock(
            lambda: CallExecution.objects.filter(id=call.id).update(
                status=CallExecution.CallStatus.PENDING
            )
        ),
        pytest.raises(RegradeNoCompletedCall),
    ):
        _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(call),
            skip_runs_without_a_completed_call=True,
        )

    run.refresh_from_db()
    assert run.status == COMPLETED
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_a_call_reset_before_the_lock_gets_no_placeholder(
    run_test, scenario, mapped_eval, dispatch
):
    from django.db import transaction as db_transaction

    run = _run(run_test)
    reset = _call(run, scenario, eval_outputs={"other": _platform_verdict()})
    kept = _call(run, scenario)
    reset_before = _stored(reset)
    real_atomic = db_transaction.atomic
    seen = []

    def atomic_after_a_reset(*args, **kwargs):
        if not seen:
            seen.append(True)
            CallExecution.objects.filter(id=reset.id).update(
                status=CallExecution.CallStatus.PENDING
            )
        return real_atomic(*args, **kwargs)

    with patch(
        "simulate.services.run_regrade.transaction.atomic",
        side_effect=atomic_after_a_reset,
    ):
        queued = _regrade(
            run_test,
            [run],
            [mapped_eval],
            call_execution_ids=_ids(reset, kept),
            snapshot_eval_outputs=True,
        )

    assert queued == _ids(kept)
    assert _dispatched_call_ids(dispatch) == _ids(kept)
    reset.refresh_from_db()
    assert reset.eval_outputs == reset_before["eval_outputs"]
    assert reset.call_metadata == reset_before["call_metadata"]
    assert CallExecutionSnapshot.objects.filter(call_execution=reset).count() == 0
    assert CallExecutionSnapshot.objects.filter(call_execution=kept).count() == 1


def _hosted_job(run, state):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=run.run_test.organization,
        workspace=run.run_test.workspace,
        run_id=uuid.uuid4(),
        idempotency_key=f"plan-{uuid.uuid4()}",
        request_digest=f"sha256:{'0' * 64}",
        schema_version="1.4",
        payload={},
        state=state,
        seed=1,
        scenario_count=1,
        artifact_level="standard",
        max_artifact_bytes=1,
        deadline_at=timezone.now() + timedelta(hours=1),
        run_test=run.run_test,
        test_execution=run,
    )


@pytest.fixture
def result_column(run_test, organization):
    """A result column only the harness can judge: the platform can't grade it."""
    template = EvalTemplate.objects.create(
        name="refund_claim_regrade_calls",
        config={"required_keys": ["conversation"], "output": "Pass/Fail"},
        owner="user",
        organization=organization,
    )
    return SimulateEvalConfig.objects.create(
        name="Refund claim",
        eval_template=template,
        run_test=run_test,
        config={},
        mapping={},
    )


def _plan(run_test, runs, *, select_all=False, calls=None):
    from simulate.services.run_regrade import plan_harness_regrade

    execution_ids = [str(run.id) for run in runs]
    return plan_harness_regrade(
        run_test,
        execution_ids,
        calls
        if calls is not None
        else CallExecution.objects.filter(test_execution_id__in=execution_ids),
        select_all=select_all,
    )


@pytest.mark.django_db
def test_the_plan_splits_graded_from_left_out_and_picks_the_gradable_evals(
    run_test, scenario, mapped_eval, result_column
):
    run = _run(run_test)
    done = _call(run, scenario)
    failed = _call(run, scenario, status=CALL_FAILED)

    plan = _plan(run_test, [run])

    assert plan.execution_ids == [str(run.id)]
    assert plan.previous_statuses == {str(run.id): COMPLETED}
    assert plan.graded == {str(run.id): _ids(done)}
    assert plan.left_out == {str(run.id): _ids(failed)}
    assert plan.skipped == {}
    assert plan.eval_config_ids == [str(mapped_eval.id)]


@pytest.mark.django_db
def test_the_plan_reports_runs_that_did_not_complete_by_status(
    run_test, scenario, mapped_eval
):
    from simulate.services.run_regrade import HarnessRegradeRunsNotCompleted

    good = _run(run_test)
    _call(good, scenario)
    failed = _run(run_test, TestExecution.ExecutionStatus.FAILED)
    _call(failed, scenario)

    with pytest.raises(HarnessRegradeRunsNotCompleted) as refused:
        _plan(run_test, [good, failed])

    assert refused.value.statuses == {str(failed.id): "failed"}


@pytest.mark.django_db
def test_the_plan_reports_runs_with_no_completed_call_by_id(
    run_test, scenario, mapped_eval
):
    from simulate.services.run_regrade import HarnessRegradeNoCompletedCall

    good = _run(run_test)
    _call(good, scenario)
    empty = _run(run_test)
    _call(empty, scenario, status=CALL_FAILED)

    with pytest.raises(HarnessRegradeNoCompletedCall) as refused:
        _plan(run_test, [good, empty])

    assert refused.value.execution_ids == [str(empty.id)]


@pytest.mark.django_db
def test_the_plan_reports_runs_whose_job_is_still_finishing_by_id(
    run_test, scenario, mapped_eval
):
    from simulate.services.run_regrade import HarnessRegradeStillFinishing

    run = _run(run_test)
    _call(run, scenario)
    _hosted_job(run, HostedHarnessJob.State.FINALIZING)

    with pytest.raises(HarnessRegradeStillFinishing) as refused:
        _plan(run_test, [run])

    assert refused.value.execution_ids == [str(run.id)]


@pytest.mark.django_db
def test_the_plan_refuses_when_no_eval_is_gradable(run_test, scenario, result_column):
    from simulate.services.run_regrade import HarnessRegradeNothingToGrade

    run = _run(run_test)
    _call(run, scenario)

    with pytest.raises(HarnessRegradeNothingToGrade):
        _plan(run_test, [run])


@pytest.mark.django_db
def test_select_all_skips_the_runs_it_cannot_grade_and_says_why(
    run_test, scenario, mapped_eval
):
    good = _run(run_test)
    done = _call(good, scenario)
    failed = _run(run_test, TestExecution.ExecutionStatus.FAILED)
    _call(failed, scenario)
    evaluating = _run(run_test, EVALUATING)
    _call(evaluating, scenario)
    finishing = _run(run_test)
    _call(finishing, scenario)
    _hosted_job(finishing, HostedHarnessJob.State.CLEANING_UP)
    empty = _run(run_test)
    _call(empty, scenario, status=CALL_FAILED)

    plan = _plan(
        run_test, [good, failed, evaluating, finishing, empty], select_all=True
    )

    assert plan.execution_ids == [str(good.id)]
    assert plan.previous_statuses == {str(good.id): COMPLETED}
    assert plan.graded == {str(good.id): _ids(done)}
    assert plan.skipped == {
        str(failed.id): "The run is failed, not completed.",
        str(evaluating.id): "The run is evaluating, not completed.",
        str(finishing.id): "The run's job is still finishing.",
        str(empty.id): "No call in this run completed.",
    }


@pytest.mark.django_db
def test_select_all_with_nothing_gradable_is_refused_by_the_spec_rule(
    run_test, scenario, mapped_eval
):
    """With ``select_all`` and no run left to grade, the answer is 409 (not
    completed) while no COMPLETED run exists, and 400 (no completed call) as
    soon as one does. A FAILED run beside a COMPLETED run with no completed
    call used to answer 409 and now answers 400; that is the rule, not a
    slip.
    """
    from simulate.services.run_regrade import (
        HarnessRegradeNoCompletedCall,
        HarnessRegradeRunsNotCompleted,
    )

    failed = _run(run_test, TestExecution.ExecutionStatus.FAILED)
    _call(failed, scenario)
    with pytest.raises(HarnessRegradeRunsNotCompleted):
        _plan(run_test, [failed], select_all=True)

    empty = _run(run_test)
    _call(empty, scenario, status=CALL_FAILED)
    with pytest.raises(HarnessRegradeNoCompletedCall) as refused:
        _plan(run_test, [failed, empty], select_all=True)
    assert refused.value.execution_ids == [str(empty.id)]


@pytest.mark.django_db
def test_the_plan_with_no_runs_says_so(run_test, scenario, mapped_eval):
    from simulate.services.run_regrade import HarnessRegradeNoRuns

    for select_all in (False, True):
        with pytest.raises(HarnessRegradeNoRuns):
            _plan(run_test, [], select_all=select_all)
