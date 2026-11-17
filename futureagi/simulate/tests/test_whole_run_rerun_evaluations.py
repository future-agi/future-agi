"""What the LiveKit result of a re-run call evaluates and charges.

A finished LiveKit result dispatches its call's evaluations once
(``_dispatch_evaluations_once``): the claim sets the call's ``eval_started``
stamp, and a result that finds the stamp set dispatches nothing. A re-run of
chosen calls clears that stamp with the call's other evaluation stamps, and a
re-run of a whole run now does too, under a row lock. The re-run's result then
dispatches each evaluation once, and each evaluation is charged once. A second
claim for the same result dispatches nothing. Only the re-run's own calls are
reset: a call of another run keeps its stamps and its stored results, and a
re-run call's earlier results stay in its snapshot, not graded or charged again.

The resets and the dispatch are called directly, not through the re-run
endpoints. The evaluation job runs inline. ``run_eval_func``, which checks
each evaluation against usage, logs it and emits its usage event, is a stand-in
that counts one usage event per evaluation and returns a made-up verdict.
Every value here is made up.
"""

from types import SimpleNamespace

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import CallExecution, CallExecutionSnapshot, SimulateEvalConfig
from simulate.models.test_execution import TestExecution as SimulationRun
from simulate.services.alk_simulate_ingestion import _dispatch_evaluations_once
from simulate.services.harness_evals import runnable_eval_config_ids
from simulate.services.test_executor import _run_simulate_evaluations_task
from simulate.tests.test_alk_simulate_ingestion import (  # noqa: F401 - fixtures
    agent_definition as agent_definition,
)
from simulate.tests.test_alk_simulate_ingestion import run_test as run_test
from simulate.tests.test_alk_simulate_ingestion import scenario as scenario
from simulate.tests.test_alk_simulate_ingestion import (  # noqa: F401 - fixtures
    simulator_agent as simulator_agent,
)
from simulate.views.run_test import CallExecutionRerunView
from simulate.views.run_test import TestExecutionRerunView as WholeRunRerunView

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# The verdict the stand-in judge returns for every evaluation.
_MADE_UP_VERDICT = {
    "output": "Passed",
    "reason": "made-up reason",
    "output_type": "Pass/Fail",
}
# What the evaluation job reads as the call's transcript and recordings.
_MADE_UP_TRANSCRIPT = {
    "transcript": (
        "user: Is my made-up order on its way?\nassistant: Yes, it ships today."
    ),
    "voice_recording": "",
    "assistant_recording": "",
    "customer_recording": "",
    "stereo_recording": "",
    "user_chat_transcript": "",
    "assistant_chat_transcript": "",
}
_NO_SKIP = SimpleNamespace(processing_skipped=False, processing_skip_reason="")


def _made_up_transcript(_executor, _call_execution, *_args, **_kwargs):
    return dict(_MADE_UP_TRANSCRIPT)


class _Recorder:
    """The evaluation jobs results dispatch, each run here as it is
    dispatched, and the usage events their evaluations are charged."""

    def __init__(self):
        self.jobs = []
        self.usage = []
        self._running_call_id = None

    def start_job(self, args=None, kwargs=None, **_options):
        """Stands in for the queue: records the job, then runs it."""
        call_id, eval_config_ids = args
        job = SimpleNamespace(
            call_id=call_id, eval_config_ids=list(eval_config_ids), finished=None
        )
        self.jobs.append(job)
        self._running_call_id = call_id
        try:
            job.finished = _run_simulate_evaluations_task._original_func(
                *args, **(kwargs or {})
            )
        finally:
            self._running_call_id = None
        return SimpleNamespace(id=f"made-up-job-{len(self.jobs)}")

    def charge(self, *, template, **_kwargs):
        """Stands in for run_eval_func, which checks the usage, logs the call
        and emits one usage event for each evaluation it runs."""
        self.usage.append((self._running_call_id, template.name))
        return dict(_MADE_UP_VERDICT)

    def tally(self):
        """(jobs dispatched, evaluations those jobs carry, usage events)."""
        evaluations = sum(len(job.eval_config_ids) for job in self.jobs)
        return (len(self.jobs), evaluations, len(self.usage))

    def clear(self):
        self.jobs.clear()
        self.usage.clear()


@pytest.fixture
def recorder(monkeypatch):
    # A whole-run re-run files the call for the LiveKit provider, which needs
    # no phone number id.
    monkeypatch.setenv("SYSTEM_VOICE_PROVIDER", "livekit")
    # The job closes connections between its steps; here it shares the
    # test's connection.
    monkeypatch.setattr(
        "simulate.services.test_executor.close_old_connections", lambda: None
    )
    monkeypatch.setattr(
        "simulate.services.test_executor.decide_processing_skip",
        lambda **_: _NO_SKIP,
    )
    monkeypatch.setattr(
        "simulate.services.test_executor.TestExecutor._get_call_transcript_data",
        _made_up_transcript,
    )
    recorder = _Recorder()
    monkeypatch.setattr(
        "simulate.services.test_executor.run_eval_func", recorder.charge
    )
    monkeypatch.setattr(
        _run_simulate_evaluations_task, "apply_async", recorder.start_job
    )
    return recorder


def _two_evaluations(run_test):
    """Two evaluations the run grades, each with a template of its own; both
    read the call's transcript."""
    configs = []
    for name in ("made-up-check-one", "made-up-check-two"):
        template = EvalTemplate.objects.create(
            name=name,
            config={"prompt": "Made-up instructions for a judge."},
            organization=run_test.organization,
        )
        configs.append(
            SimulateEvalConfig.objects.create(
                name=name,
                eval_template=template,
                run_test=run_test,
                mapping={"conversation": "transcript"},
                config={},
            )
        )
    return configs


def _finished_run(run_test):
    return SimulationRun.objects.create(
        run_test=run_test,
        status=SimulationRun.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=run_test.agent_definition,
        simulator_agent=run_test.simulator_agent,
    )


def _finished_call(execution, scenario):
    """A finished LiveKit voice call whose result has not dispatched its
    evaluations yet."""
    return CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        simulation_call_type=CallExecution.SimulationCallType.VOICE,
        status=CallExecution.CallStatus.COMPLETED,
        phone_number="+15550100000",
        duration_seconds=15,
        call_metadata={},
    )


def _fresh(call):
    return CallExecution.objects.get(id=call.id)


def _assert_graded(call, configs, recorder):
    """The one job ran to its end, stored a completed result for each
    evaluation and charged each evaluation once, all for ``call``; its
    dispatch did not fail."""
    row = _fresh(call)
    assert [job.finished for job in recorder.jobs] == [True]
    assert "eval_dispatch_failed" not in row.call_metadata
    stored = {
        str(config.id): (row.eval_outputs or {}).get(str(config.id), {}).get("status")
        for config in configs
    }
    assert stored == {str(config.id): StatusType.COMPLETED.value for config in configs}
    assert sorted(recorder.usage) == sorted(
        (str(call.id), config.eval_template.name) for config in configs
    )


def _first_result(call, configs, selected, recorder):
    """The call's first result dispatches its evaluations: one job carrying
    both, each charged once. Then the counts start again from zero."""
    assert _dispatch_evaluations_once(_fresh(call), eval_config_ids=selected) is True
    assert recorder.tally() == (1, 2, 2)
    _assert_graded(call, configs, recorder)
    assert _fresh(call).call_metadata["eval_started"] is True
    recorder.clear()


def _rerun_chosen_calls(call):
    """The reset a re-run of chosen calls makes (CallExecutionRerunView)."""
    row = CallExecution.objects.select_related("test_execution__run_test").get(
        id=call.id
    )
    CallExecutionRerunView()._clear_call_execution_data(
        row, row.test_execution.run_test
    )


def _rerun_whole_run(call):
    """The reset a re-run of a whole run makes (TestExecutionRerunView), over
    every call of the call's run."""
    execution = SimulationRun.objects.select_related("run_test").get(
        id=call.test_execution_id
    )
    rerun = WholeRunRerunView()._prepare_call_executions_for_rerun_bulk(
        CallExecution.objects.filter(test_execution=execution),
        "call_and_eval",
        execution.run_test,
    )
    assert rerun == ([str(call.id)], [])


def _rerun_call_finished(call):
    """The re-run's call finishes and its result is stored, as the first
    one's was."""
    CallExecution.objects.filter(id=call.id).update(
        status=CallExecution.CallStatus.COMPLETED, duration_seconds=15
    )


@pytest.mark.parametrize(
    "rerun",
    [_rerun_chosen_calls, _rerun_whole_run],
    ids=["chosen-calls", "whole-run"],
)
def test_the_livekit_result_of_a_rerun_call_runs_and_charges_each_evaluation_once(
    run_test, scenario, recorder, rerun
):
    configs = _two_evaluations(run_test)
    selected = runnable_eval_config_ids(run_test.id)
    call = _finished_call(_finished_run(run_test), scenario)
    _first_result(call, configs, selected, recorder)

    rerun(call)
    _rerun_call_finished(call)
    dispatched = _dispatch_evaluations_once(_fresh(call), eval_config_ids=selected)

    # One job carrying both evaluations, and one usage event for each.
    assert (dispatched, recorder.tally()) == (True, (1, 2, 2))
    _assert_graded(call, configs, recorder)


def test_the_livekit_result_of_a_whole_run_rerun_dispatches_its_evaluations_once(
    run_test, scenario, recorder
):
    configs = _two_evaluations(run_test)
    selected = runnable_eval_config_ids(run_test.id)
    call = _finished_call(_finished_run(run_test), scenario)
    _first_result(call, configs, selected, recorder)

    _rerun_whole_run(call)
    _rerun_call_finished(call)
    # The same result delivered again, holding the row as it was before the
    # first claim.
    redelivered = _fresh(call)
    first = _dispatch_evaluations_once(_fresh(call), eval_config_ids=selected)
    second = _dispatch_evaluations_once(redelivered, eval_config_ids=selected)

    assert (first, second, recorder.tally()) == (True, False, (1, 2, 2))
    _assert_graded(call, configs, recorder)
    assert _fresh(call).call_metadata["eval_started"] is True


def test_a_whole_run_rerun_grades_and_charges_only_its_own_calls_again(
    run_test, scenario, recorder
):
    configs = _two_evaluations(run_test)
    selected = runnable_eval_config_ids(run_test.id)
    rerun_call = _finished_call(_finished_run(run_test), scenario)
    # A call of another run of the same simulation, which is not re-run.
    other_call = _finished_call(_finished_run(run_test), scenario)
    for call in (rerun_call, other_call):
        _first_result(call, configs, selected, recorder)
    earlier_results = _fresh(rerun_call).eval_outputs
    other = _fresh(other_call)
    other_before = (other.status, other.call_metadata, other.eval_outputs)

    _rerun_whole_run(rerun_call)
    _rerun_call_finished(rerun_call)
    # The re-run call's result arrives; the other call's earlier result is
    # delivered again.
    dispatched = [
        _dispatch_evaluations_once(_fresh(call), eval_config_ids=selected)
        for call in (rerun_call, other_call)
    ]

    # Only the re-run call is graded again, one usage event per evaluation;
    # neither call's earlier results are charged again.
    assert (dispatched, recorder.tally()) == ([True, False], (1, 2, 2))
    _assert_graded(rerun_call, configs, recorder)
    # The other run's call keeps its stamps and its stored results.
    other = _fresh(other_call)
    assert (other.status, other.call_metadata, other.eval_outputs) == other_before
    # The re-run call's earlier results are kept, as stored, in its snapshot.
    snapshot = CallExecutionSnapshot.objects.get(
        call_execution_id=rerun_call.id,
        rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
    )
    assert snapshot.eval_outputs == earlier_results
