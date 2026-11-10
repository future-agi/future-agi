"""Grading chosen calls of a finished run again through the re-grade service:
which calls are used, the snapshot saved inside the claim, the placeholder
written over a harness verdict, and what a failed dispatch puts back.
"""

from __future__ import annotations

import contextlib
import copy
from unittest.mock import patch

import pytest
from django.db import OperationalError
from structlog.testing import capture_logs

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    CallExecution,
    CallExecutionSnapshot,
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

    assert queued == 3
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

    assert queued == 2
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
def test_a_dispatch_failure_resets_only_a_run_still_evaluating(
    run_test, scenario, mapped_eval, dispatch
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
        assert call.call_metadata["eval_started"] is False
        assert call.call_metadata["eval_completed"] is True
        assert call.call_metadata["eval_dispatch_failed"] == "broker unreachable"
    # The snapshots hold the state the placeholders replaced, so they stay.
    assert CallExecutionSnapshot.objects.count() == 2


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
