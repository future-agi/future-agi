"""Nothing paid starts by itself on a simulation run.

The run's written summary (``run_eval_summary_task``, a chain of model calls)
and the Fix My Agent analysis are made only when a person asks: the summary's
Refresh button, or the Fix My Agent refresh, whose queued job makes a missing
summary first. Finishing a run, a late verdict, a rerun or opening a page
starts neither, and no web request runs a model inline.

The settle, finalizer, chat-monitor and late-verdict paths are pinned in
``test_scoring_status.py``, ``test_temporal_activities.py`` and
``test_chat_simulation.py``; this file pins the pages, the buttons, the
optimiser job, and the list of code that may start the summary at all.
"""

from __future__ import annotations

import ast
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status

from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from simulate.models import (
    AgentDefinition,
    AgentOptimiser,
    AgentOptimiserRun,
    CallExecution,
    RunTest,
    Scenarios,
    TestExecution,
)
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.services.scoring_status import (
    SUMMARY_JOB_GIVE_UP,
    summary_status_once_graded,
)
from simulate.utils.agent_optimiser import (
    _OPTIMISER_RUN_GIVE_UP,
    OPTIMISER_RUN_TIME_LIMIT_SECONDS,
)

Summary = EvalExplanationSummaryStatus
SUMMARY_URL = "/simulate/test-executions/{}/eval-explanation-summary/"
SUMMARY_REFRESH_URL = "/simulate/test-executions/{}/eval-explanation-summary/refresh/"
OPTIMISER_URL = "/simulate/test-executions/{}/optimiser-analysis/"
OPTIMISER_REFRESH_URL = "/simulate/test-executions/{}/optimiser-analysis/refresh/"
NO_RUN_MESSAGE = "No optimiser runs found. Trigger a refresh to start analysis."


def _is_write(sql):
    """A write to the run's own row or to the analysis tables."""
    statement = sql.lstrip().upper()
    return statement.startswith(("INSERT", "UPDATE", "DELETE")) and any(
        f'"{table}"'.upper() in statement
        for table in (
            "simulate_test_execution",
            "agent_optimiser",
            "agent_optimiser_run",
        )
    )


# --- fixtures (copied, not imported, from the other simulate API tests) -------


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent under test",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Simulator",
        prompt="You are a caller.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent):
    return RunTest.objects.create(
        name="Run",
        description="Run under test",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def finished_run(db, run_test, simulator_agent, agent_definition):
    """A completed run that nobody has asked to analyse: no summary, the
    summary status as the run's completion leaves it."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        eval_explanation_summary=None,
        eval_explanation_summary_status=Summary.PENDING,
    )


@pytest.fixture
def scenario(db, organization, user, workspace, agent_definition):
    dataset = Dataset.no_workspace_objects.create(
        name="Scenario rows",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    return Scenarios.objects.create(
        name="Scenario",
        description="Scenario under test",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def finished_call(db, finished_run, scenario):
    return CallExecution.objects.create(
        test_execution=finished_run,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata={"eval_started": True, "eval_completed": True},
    )


@pytest.fixture
def no_paid_work():
    """Spies on every way the summary or the analysis could start or run."""
    with (
        patch(
            "simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"
        ) as summary_queued,
        patch(
            "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
            side_effect=AssertionError("the summary's model calls ran"),
        ) as summary_ran,
        patch(
            "simulate.tasks.agent_optimiser_tasks.execute_optimiser_run.delay"
        ) as analysis_queued,
    ):
        yield summary_queued, summary_ran, analysis_queued


# --- the summary pane ---------------------------------------------------------


# What the pane is told for each stored status. "pending" (nobody has asked
# since the verdicts last changed) is answered "not_requested", which the old
# pane does not poll on; a running job is answered running.
ANSWERED = {
    Summary.PENDING: "not_requested",
    Summary.FAILED: Summary.FAILED,
    Summary.COMPLETED: Summary.COMPLETED,
    Summary.RUNNING: Summary.RUNNING,
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored_status",
    [Summary.PENDING, Summary.FAILED, Summary.COMPLETED, Summary.RUNNING],
)
def test_opening_the_summary_starts_nothing_and_writes_nothing(
    auth_client, finished_run, no_paid_work, stored_status
):
    """Opening the summary of a run that has none returns what is stored. It
    queues no summary job, runs no model, and leaves the stored status alone,
    whatever that status is."""
    summary_queued, summary_ran, analysis_queued = no_paid_work
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=stored_status,
        eval_explanation_summary_last_updated=timezone.now(),
    )
    with CaptureQueriesContext(connection) as queries:
        response = auth_client.get(SUMMARY_URL.format(finished_run.id))

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["response"] is None
    assert result["status"] == ANSWERED[stored_status]
    summary_queued.assert_not_called()
    summary_ran.assert_not_called()
    analysis_queued.assert_not_called()
    stored = TestExecution.objects.get(id=finished_run.id)
    assert stored.eval_explanation_summary_status == stored_status
    writes = [q["sql"] for q in queries.captured_queries if _is_write(q["sql"])]
    assert writes == [], "a read writes nothing"


@pytest.mark.django_db
@pytest.mark.parametrize("stored_status", [Summary.PENDING, Summary.COMPLETED])
def test_summary_refresh_queues_one_job_and_shows_it_running(
    auth_client, finished_run, no_paid_work, stored_status
):
    """The Refresh button is the person asking. It queues the summary once,
    and the pane polls while the stored status says the job is on its way."""
    summary_queued, summary_ran, _ = no_paid_work
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=stored_status
    )

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    summary_queued.assert_called_once_with(args=(str(finished_run.id),))
    summary_ran.assert_not_called()
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.RUNNING


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored_status", [Summary.PENDING, Summary.COMPLETED, Summary.FAILED]
)
def test_summary_refresh_that_cannot_queue_puts_back_what_was_there(
    auth_client, finished_run, no_paid_work, stored_status
):
    """A refresh whose job could not be handed over says so (503) and puts
    back the summary's status as it was: a current summary does not turn out
    of date, and the pane stops waiting, so the person can press again."""
    summary_queued, _, _ = no_paid_work
    summary_queued.side_effect = TimeoutError("temporal dispatch timed out")
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary={"kept": True},
        eval_explanation_summary_status=stored_status,
    )

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == stored_status
    assert finished_run.eval_explanation_summary == {"kept": True}


GRADING = [
    TestExecution.ExecutionStatus.PENDING,
    TestExecution.ExecutionStatus.RUNNING,
    TestExecution.ExecutionStatus.EVALUATING,
    TestExecution.ExecutionStatus.CANCELLING,
]


@pytest.mark.django_db
@pytest.mark.parametrize("run_status", GRADING)
def test_summary_refresh_is_refused_while_the_run_is_graded(
    auth_client, finished_run, no_paid_work, run_status
):
    """A summary made while verdicts still arrive would miss them, so the
    Refresh is refused (409) until grading ends. Nothing is queued and the
    stored status is left as it was."""
    summary_queued, _, _ = no_paid_work
    TestExecution.objects.filter(id=finished_run.id).update(status=run_status)

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    summary_queued.assert_not_called()
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.PENDING


@pytest.mark.django_db
@pytest.mark.parametrize(
    "run_status",
    [TestExecution.ExecutionStatus.FAILED, TestExecution.ExecutionStatus.CANCELLED],
)
def test_a_run_that_ended_without_completing_can_still_be_summarised(
    auth_client, finished_run, no_paid_work, run_status
):
    summary_queued, _, _ = no_paid_work
    TestExecution.objects.filter(id=finished_run.id).update(status=run_status)

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    summary_queued.assert_called_once()


# --- Fix My Agent -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("has_optimiser", [False, True])
def test_opening_fix_my_agent_creates_nothing(
    auth_client, finished_run, finished_call, no_paid_work, has_optimiser
):
    """Opening the Fix My Agent drawer on a run never analysed returns the
    "trigger a refresh" message. It creates no optimiser and no optimiser
    run, queues no analysis and makes no summary."""
    summary_queued, summary_ran, analysis_queued = no_paid_work
    if has_optimiser:
        optimiser = AgentOptimiser.objects.create(
            name="Optimiser", configuration={"type": "simulation_analysis"}
        )
        TestExecution.objects.filter(id=finished_run.id).update(
            agent_optimiser=optimiser
        )
    optimisers_before = AgentOptimiser.objects.count()

    response = auth_client.get(OPTIMISER_URL.format(finished_run.id))

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["response"] is None
    assert result["message"] == NO_RUN_MESSAGE
    # The old drawer polls on pending and running: anything else stops its
    # loader, so it shows its empty state instead of spinning.
    assert result["status"] == "completed"
    assert AgentOptimiser.objects.count() == optimisers_before
    assert AgentOptimiserRun.objects.count() == 0
    finished_run.refresh_from_db()
    assert (finished_run.agent_optimiser is not None) == has_optimiser
    summary_queued.assert_not_called()
    summary_ran.assert_not_called()
    analysis_queued.assert_not_called()


@pytest.mark.django_db
def test_opening_fix_my_agent_shows_the_latest_analysis(
    auth_client, finished_run, no_paid_work
):
    """A run already analysed shows its latest analysis, unchanged."""
    _, _, analysis_queued = no_paid_work
    optimiser = AgentOptimiser.objects.create(
        name="Optimiser", configuration={"type": "simulation_analysis"}
    )
    TestExecution.objects.filter(id=finished_run.id).update(agent_optimiser=optimiser)
    AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        input_data={},
        status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        result={"agent_level": {"kept": True}},
    )

    response = auth_client.get(OPTIMISER_URL.format(finished_run.id))

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    assert result["response"] == {"agent_level": {"kept": True}}
    assert result["status"] == AgentOptimiserRun.OptimiserStatus.COMPLETED
    assert AgentOptimiserRun.objects.count() == 1
    analysis_queued.assert_not_called()


@pytest.mark.django_db
def test_fix_my_agent_refresh_queues_the_job_and_runs_no_model_inline(
    auth_client, finished_run, finished_call, no_paid_work
):
    """The Fix My Agent refresh is the person asking. On a run with no
    summary the request stores the analysis input without one and queues the
    job; the summary's model calls do not run inside the request."""
    summary_queued, summary_ran, analysis_queued = no_paid_work

    with patch(
        "simulate.utils.agent_optimiser.construct_scenarios_from_calls",
        return_value=[{"scenario_id": "s1", "calls": []}],
    ):
        response = auth_client.post(
            OPTIMISER_REFRESH_URL.format(finished_run.id), {}, format="json"
        )

    assert response.status_code == status.HTTP_200_OK, response.json()
    run = AgentOptimiserRun.objects.get()
    assert run.input_data["test_execution_id"] == str(finished_run.id)
    assert run.input_data["cluster_dict_by_eval"] is None
    analysis_queued.assert_called_once_with(str(run.id))
    summary_ran.assert_not_called()
    summary_queued.assert_not_called()


def _scenarios():
    return patch(
        "simulate.utils.agent_optimiser.construct_scenarios_from_calls",
        return_value=[{"scenario_id": "s1", "calls": []}],
    )


@pytest.mark.django_db
@pytest.mark.parametrize("run_status", GRADING)
def test_fix_my_agent_refresh_is_refused_while_the_run_is_graded(
    auth_client, finished_run, finished_call, no_paid_work, run_status
):
    _, _, analysis_queued = no_paid_work
    TestExecution.objects.filter(id=finished_run.id).update(status=run_status)

    with _scenarios():
        response = auth_client.post(
            OPTIMISER_REFRESH_URL.format(finished_run.id), {}, format="json"
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert AgentOptimiserRun.objects.count() == 0
    analysis_queued.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "waiting",
    [
        AgentOptimiserRun.OptimiserStatus.PENDING,
        AgentOptimiserRun.OptimiserStatus.RUNNING,
    ],
)
def test_a_second_fix_my_agent_refresh_while_one_is_on_its_way_starts_nothing(
    auth_client, finished_run, finished_call, no_paid_work, waiting
):
    """Each analysis is a chain of paid model calls: a refresh while one is
    still on its way starts no second one, and says the first one's status."""
    _, _, analysis_queued = no_paid_work
    optimiser = AgentOptimiser.objects.create(
        name="Optimiser", configuration={"type": "simulation_analysis"}
    )
    TestExecution.objects.filter(id=finished_run.id).update(agent_optimiser=optimiser)
    AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser, input_data={}, status=waiting
    )

    with _scenarios():
        response = auth_client.post(
            OPTIMISER_REFRESH_URL.format(finished_run.id), {}, format="json"
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["status"] == waiting
    assert AgentOptimiserRun.objects.count() == 1
    analysis_queued.assert_not_called()


@pytest.mark.django_db
def test_an_analysis_stuck_past_its_time_limit_does_not_block_a_refresh(
    auth_client, finished_run, finished_call, no_paid_work
):
    """A job killed at its time limit leaves its run running for ever; once
    that limit has passed, a refresh starts a new analysis."""
    _, _, analysis_queued = no_paid_work
    optimiser = AgentOptimiser.objects.create(
        name="Optimiser", configuration={"type": "simulation_analysis"}
    )
    TestExecution.objects.filter(id=finished_run.id).update(agent_optimiser=optimiser)
    stuck = AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        input_data={},
        status=AgentOptimiserRun.OptimiserStatus.RUNNING,
    )
    AgentOptimiserRun.objects.filter(id=stuck.id).update(
        created_at=timezone.now() - _OPTIMISER_RUN_GIVE_UP - timedelta(minutes=1)
    )

    with _scenarios():
        response = auth_client.post(
            OPTIMISER_REFRESH_URL.format(finished_run.id), {}, format="json"
        )

    assert response.status_code == status.HTTP_200_OK
    assert AgentOptimiserRun.objects.count() == 2
    analysis_queued.assert_called_once()


# --- the optimiser job --------------------------------------------------------


def _optimiser_run(test_execution, cluster_dict_by_eval):
    optimiser = AgentOptimiser.objects.create(
        name="Optimiser", configuration={"type": "simulation_analysis"}
    )
    TestExecution.objects.filter(id=test_execution.id).update(agent_optimiser=optimiser)
    return AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        input_data={
            "test_execution_id": str(test_execution.id),
            "scenarios": [{"scenario_id": "s1"}],
            "cluster_dict_by_eval": cluster_dict_by_eval,
        },
    )


@pytest.mark.django_db
def test_optimiser_job_makes_a_missing_summary_first(finished_run):
    """The analysis reads the run's summary. When the input has none, the
    queued job, which a person started, makes it before the analysis and
    hands it over; the run keeps the summary too."""
    from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

    run = _optimiser_run(finished_run, cluster_dict_by_eval=None)
    made = {"eval-1": [{"kind": "failure", "theme": "made inside the job"}]}
    seen = {}

    def _analysis(input_data):
        seen.update(input_data)
        return {"agent_level": {}}

    with (
        patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        patch(
            "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
            return_value=made,
        ) as summary_ran,
        patch(
            "simulate.tasks.agent_optimiser_tasks.execute_simulation_analysis",
            side_effect=_analysis,
        ),
    ):
        execute_optimiser_run(str(run.id))

    summary_ran.assert_called_once()
    assert seen["cluster_dict_by_eval"] == made
    run.refresh_from_db()
    assert run.status == AgentOptimiserRun.OptimiserStatus.COMPLETED
    assert run.input_data["cluster_dict_by_eval"] == made
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary == made
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED


def _analysed_with(run):
    from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

    seen = {}

    def _analysis(input_data):
        seen.update(input_data)
        return {"agent_level": {}}

    made = {"eval-1": [{"kind": "failure", "theme": "made inside the job"}]}
    with (
        patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        patch(
            "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
            return_value=made,
        ) as summary_ran,
        patch(
            "simulate.tasks.agent_optimiser_tasks.execute_simulation_analysis",
            side_effect=_analysis,
        ),
    ):
        execute_optimiser_run(str(run.id))
    return seen, summary_ran, made


@pytest.mark.django_db
@pytest.mark.parametrize("queued_with", [None, {"old": []}])
def test_optimiser_job_uses_the_runs_current_summary_when_it_starts(
    finished_run, queued_with
):
    """Freshness is read from the run when the job starts, not from what was
    queued: a current summary stored after the job was queued is used, and no
    second one is made."""
    current = {"eval-1": [{"kind": "failure", "theme": "current"}]}
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary=current,
        eval_explanation_summary_status=Summary.COMPLETED,
    )
    run = _optimiser_run(finished_run, cluster_dict_by_eval=queued_with)

    seen, summary_ran, _ = _analysed_with(run)

    summary_ran.assert_not_called()
    assert seen["cluster_dict_by_eval"] == current


@pytest.mark.django_db
@pytest.mark.parametrize("stored_status", [Summary.PENDING, Summary.FAILED])
def test_optimiser_job_remakes_a_summary_that_is_not_current(
    finished_run, stored_status
):
    """A summary marked out of date (made before the run's latest grading)
    is not analysed: the job makes a new one first, even when the queued
    input carried the old one."""
    old = {"eval-1": [{"kind": "failure", "theme": "before the regrade"}]}
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary=old,
        eval_explanation_summary_status=stored_status,
    )
    run = _optimiser_run(finished_run, cluster_dict_by_eval=old)

    seen, summary_ran, made = _analysed_with(run)

    summary_ran.assert_called_once()
    assert seen["cluster_dict_by_eval"] == made


# --- the summary job ------------------------------------------------------------


def _summarised(run, side_effect=None):
    from simulate.tasks.eval_summary_tasks import run_eval_summary_task

    made = {"eval-1": [{"kind": "failure", "theme": "made by the job"}]}

    def _clusters(*args, **kwargs):
        if side_effect:
            side_effect()
        return made

    with (
        patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        patch(
            "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
            side_effect=_clusters,
        ),
        patch(
            "simulate.tasks.eval_summary_tasks.run_eval_summary_task.apply_async"
        ) as queued,
    ):
        run_eval_summary_task(str(run.id))
    queued.assert_not_called()
    run.refresh_from_db()
    return made


@pytest.mark.django_db
def test_a_summary_that_saw_all_the_grading_is_current(finished_run, finished_call):
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    made = _summarised(finished_run)

    assert finished_run.eval_explanation_summary == made
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED


@pytest.mark.django_db
def test_a_summary_whose_grading_changed_while_it_ran_is_kept_out_of_date(
    finished_run, finished_call
):
    """A verdict that lands while the model calls run is not in the summary:
    the result is stored, marked out of date (pending), and nothing more is
    queued."""
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    def _late_verdict():
        CallExecution.objects.filter(id=finished_call.id).update(
            eval_outputs={"config-1": {"status": "Completed", "output": "Failed"}}
        )

    made = _summarised(finished_run, side_effect=_late_verdict)

    assert finished_run.eval_explanation_summary == made
    assert finished_run.eval_explanation_summary_status == Summary.PENDING


@pytest.mark.django_db
def test_a_verdict_seen_only_after_the_summary_was_stored_marks_it_out_of_date(
    finished_run, finished_call
):
    """A verdict committed between the job's last look and its write is
    caught by a look after the write."""
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    with patch(
        "simulate.tasks.eval_summary_tasks._grading_digest",
        side_effect=["before", "before", "after"],
    ):
        _summarised(finished_run)

    assert finished_run.eval_explanation_summary_status == Summary.PENDING


EARLIER = {"eval-1": [{"kind": "failure", "theme": "made by the other job"}]}


@pytest.mark.django_db
def test_a_second_job_keeps_a_current_summary_another_job_stored(
    finished_run, finished_call
):
    """Two summary jobs on the same grading (a second press, or Fix My Agent
    making its own): the one that finishes second finds the other's current
    summary and leaves it current, instead of marking it out of date."""
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    def _other_job_finished():
        TestExecution.objects.filter(id=finished_run.id).update(
            eval_explanation_summary=EARLIER,
            eval_explanation_summary_status=Summary.COMPLETED,
        )

    _summarised(finished_run, side_effect=_other_job_finished)

    assert finished_run.eval_explanation_summary == EARLIER
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED


@pytest.mark.parametrize("moved_to", [Summary.PENDING, Summary.FAILED])
@pytest.mark.django_db
def test_a_summary_moved_off_running_on_unchanged_grading_is_current(
    finished_run, finished_call, moved_to
):
    """Moved off running while it ran (another job failed, or a refresh that
    could not be queued put back what was there), on grading that did not
    change: this job's summary is the current one."""
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    def _moved():
        TestExecution.objects.filter(id=finished_run.id).update(
            eval_explanation_summary_status=moved_to
        )

    made = _summarised(finished_run, side_effect=_moved)

    assert finished_run.eval_explanation_summary == made
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED


@pytest.mark.django_db
def test_a_summary_moved_off_running_whose_grading_changed_is_out_of_date(
    finished_run, finished_call
):
    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=Summary.RUNNING
    )

    def _moved_and_regraded():
        TestExecution.objects.filter(id=finished_run.id).update(
            eval_explanation_summary_status=Summary.FAILED
        )
        CallExecution.objects.filter(id=finished_call.id).update(
            eval_outputs={"config-1": {"status": "Completed", "output": "Failed"}}
        )

    made = _summarised(finished_run, side_effect=_moved_and_regraded)

    assert finished_run.eval_explanation_summary == made
    assert finished_run.eval_explanation_summary_status == Summary.PENDING


# --- a summary job that never finished, and two at once ----------------------

LOST = timedelta(seconds=1)  # past the give-up by this much


def _summary(run, status_, stamp, summary=None):
    TestExecution.objects.filter(id=run.id).update(
        eval_explanation_summary=summary,
        eval_explanation_summary_status=status_,
        eval_explanation_summary_last_updated=stamp,
    )


def _lost_stamps():
    """A running summary whose job is gone: stamped before the give-up, or
    with no stamp at all (left running before the stamp existed)."""
    return [timezone.now() - SUMMARY_JOB_GIVE_UP - LOST, None]


@pytest.mark.django_db
@pytest.mark.parametrize("lost", ["old", "none"])
def test_a_running_summary_whose_job_is_gone_is_answered_failed(
    auth_client, finished_run, no_paid_work, lost
):
    """A summary job killed with its worker leaves the status running. Past
    the give-up the pane is told failed, so it stops waiting; what is stored
    is not changed by the read."""
    stamp = _lost_stamps()[0 if lost == "old" else 1]
    _summary(finished_run, Summary.RUNNING, stamp, summary={"kept": []})

    result = auth_client.get(SUMMARY_URL.format(finished_run.id)).json()["result"]

    assert result["status"] == Summary.FAILED
    assert result["response"] == {"kept": []}
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.RUNNING


@pytest.mark.django_db
def test_an_out_of_date_summary_is_shown_and_not_waited_for(
    auth_client, finished_run, no_paid_work
):
    """A summary made before the latest verdicts (pending) is still shown,
    told as not_requested: nobody has asked, and nothing is coming."""
    _summary(finished_run, Summary.PENDING, timezone.now(), summary={"old": []})

    result = auth_client.get(SUMMARY_URL.format(finished_run.id)).json()["result"]

    assert result["status"] == "not_requested"
    assert result["response"] == {"old": []}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stored, stamp_age, expected",
    [
        (Summary.RUNNING, timedelta(minutes=5), (Summary.RUNNING, True)),
        (Summary.RUNNING, SUMMARY_JOB_GIVE_UP + LOST, (Summary.FAILED, True)),
        (Summary.RUNNING, None, (Summary.FAILED, True)),
        (Summary.PENDING, timedelta(minutes=5), (Summary.PENDING, False)),
        (Summary.COMPLETED, timedelta(days=3), (Summary.COMPLETED, True)),
    ],
)
def test_the_sdk_reads_a_lost_summary_job_as_failed(
    finished_run, stored, stamp_age, expected
):
    """The SDK's summary fields read a lost job as failed (someone asked; it
    did not finish), so a script stops waiting."""
    from sdk.views.analytics import _summary_fields

    stamp = None if stamp_age is None else timezone.now() - stamp_age
    _summary(finished_run, stored, stamp)
    finished_run.refresh_from_db()

    fields = _summary_fields(finished_run)

    assert (
        fields["eval_explanation_summary_status"],
        fields["eval_explanation_summary_requested"],
    ) == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "stamp_age, expected",
    [
        (timedelta(minutes=5), Summary.RUNNING),
        (SUMMARY_JOB_GIVE_UP + LOST, Summary.PENDING),
        (None, Summary.PENDING),
    ],
)
def test_grading_ends_with_a_lost_summary_job_marked_pending(
    finished_run, stamp_age, expected
):
    """When grading ends, a summary a person is waiting for keeps running;
    one whose job is gone is marked pending, as nobody is making it."""
    stamp = None if stamp_age is None else timezone.now() - stamp_age
    _summary(finished_run, Summary.RUNNING, stamp)

    TestExecution.objects.filter(id=finished_run.id).update(
        eval_explanation_summary_status=summary_status_once_graded()
    )

    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == expected


@pytest.mark.django_db
def test_a_refresh_stamps_when_the_summary_was_asked_for(
    auth_client, finished_run, no_paid_work
):
    before = timezone.now()

    auth_client.post(SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json")

    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.RUNNING
    assert finished_run.eval_explanation_summary_last_updated >= before


@pytest.mark.django_db
def test_a_second_summary_refresh_while_one_is_on_its_way_starts_nothing(
    auth_client, finished_run, no_paid_work
):
    """Each summary is a chain of paid model calls: a press while one is on
    its way queues nothing and says so."""
    summary_queued, _, _ = no_paid_work
    asked = timezone.now() - timedelta(minutes=5)
    _summary(finished_run, Summary.RUNNING, asked)

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["message"] == "A summary is already on its way"
    summary_queued.assert_not_called()
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_last_updated == asked


@pytest.mark.django_db
@pytest.mark.parametrize("lost", ["old", "none"])
def test_a_summary_refresh_after_its_job_was_lost_starts_a_new_one(
    auth_client, finished_run, no_paid_work, lost
):
    summary_queued, _, _ = no_paid_work
    stamp = _lost_stamps()[0 if lost == "old" else 1]
    _summary(finished_run, Summary.RUNNING, stamp)

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    summary_queued.assert_called_once()


@pytest.mark.django_db
def test_a_refresh_that_cannot_queue_puts_back_the_stamp_too(
    auth_client, finished_run, no_paid_work
):
    summary_queued, _, _ = no_paid_work
    summary_queued.side_effect = TimeoutError("temporal dispatch timed out")
    made_at = timezone.now() - timedelta(days=2)
    _summary(finished_run, Summary.COMPLETED, made_at, summary={"kept": True})

    response = auth_client.post(
        SUMMARY_REFRESH_URL.format(finished_run.id), {}, format="json"
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED
    assert finished_run.eval_explanation_summary_last_updated == made_at


@pytest.mark.django_db
def test_the_summary_job_stamps_when_it_starts(finished_run, finished_call):
    """The job stamps its own start, so a job that waited in the queue is
    timed from when it began, not from the press."""
    asked = timezone.now() - timedelta(minutes=40)
    _summary(finished_run, Summary.RUNNING, asked)
    seen = {}

    def _look():
        row = TestExecution.objects.get(id=finished_run.id)
        seen["status"] = row.eval_explanation_summary_status
        seen["stamp"] = row.eval_explanation_summary_last_updated

    _summarised(finished_run, side_effect=_look)

    assert seen["status"] == Summary.RUNNING
    assert seen["stamp"] > asked


@pytest.mark.django_db
def test_a_failing_summary_job_keeps_a_current_summary_another_job_stored(
    finished_run, finished_call
):
    """Two jobs: one stores a current summary, the other then fails. The
    failure leaves the current summary current."""
    _summary(finished_run, Summary.RUNNING, timezone.now())

    def _other_job_finished_then_this_one_fails():
        TestExecution.objects.filter(id=finished_run.id).update(
            eval_explanation_summary=EARLIER,
            eval_explanation_summary_status=Summary.COMPLETED,
        )
        raise RuntimeError("model call failed")

    _summarised(finished_run, side_effect=_other_job_finished_then_this_one_fails)

    assert finished_run.eval_explanation_summary == EARLIER
    assert finished_run.eval_explanation_summary_status == Summary.COMPLETED


@pytest.mark.django_db
def test_a_failing_summary_job_marks_its_summary_failed(finished_run, finished_call):
    _summary(finished_run, Summary.RUNNING, timezone.now(), summary={"old": []})

    def _fails():
        raise RuntimeError("model call failed")

    _summarised(finished_run, side_effect=_fails)

    assert finished_run.eval_explanation_summary_status == Summary.FAILED
    assert finished_run.eval_explanation_summary == {"old": []}


@pytest.mark.django_db
def test_a_summary_job_whose_first_read_fails_does_not_raise(finished_run):
    """A read that fails with something other than not-found (a dropped
    connection) is handled: the job does not raise out of its own handler,
    so a Fix My Agent job calling it inline is not failed by it."""
    from django.db import OperationalError

    from simulate.tasks.eval_summary_tasks import run_eval_summary_task

    _summary(finished_run, Summary.RUNNING, timezone.now())
    with (
        patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        patch(
            "simulate.tasks.eval_summary_tasks.TestExecution.objects.get",
            side_effect=OperationalError("connection dropped"),
        ),
    ):
        run_eval_summary_task(str(finished_run.id))

    finished_run.refresh_from_db()
    assert finished_run.eval_explanation_summary_status == Summary.FAILED


@pytest.mark.django_db
def test_fix_my_agent_does_not_analyse_an_old_summary_when_the_remake_fails(
    finished_run,
):
    """The job remakes an out-of-date summary first. When that fails, the
    old summary is not passed on as if current: the analysis gets none."""
    from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

    old = {"eval-1": [{"kind": "failure", "theme": "before the regrade"}]}
    _summary(finished_run, Summary.PENDING, timezone.now(), summary=old)
    run = _optimiser_run(finished_run, cluster_dict_by_eval=old)
    seen = {}

    def _analysis(input_data):
        seen.update(input_data)
        return {"agent_level": {}}

    with (
        patch("tfc.temporal.drop_in.decorator.close_old_connections"),
        patch(
            "simulate.tasks.eval_summary_tasks._get_cluster_dict_by_eval",
            side_effect=RuntimeError("model call failed"),
        ),
        patch(
            "simulate.tasks.agent_optimiser_tasks.execute_simulation_analysis",
            side_effect=_analysis,
        ),
    ):
        execute_optimiser_run(str(run.id))

    assert seen["cluster_dict_by_eval"] is None
    run.refresh_from_db()
    assert run.status == AgentOptimiserRun.OptimiserStatus.COMPLETED


def test_the_optimiser_limit_covers_a_summary_made_inside_it():
    """The Fix My Agent job may make the summary first (up to the summary
    job's own 1800 s) and then analyse (up to 3600 s); a summary running
    inside it is given up on no sooner than the job itself."""
    assert OPTIMISER_RUN_TIME_LIMIT_SECONDS >= 1800 + 3600
    assert SUMMARY_JOB_GIVE_UP >= _OPTIMISER_RUN_GIVE_UP


# --- the grading digest ----------------------------------------------------------


@pytest.mark.django_db
def test_the_grading_digest_is_made_in_the_database(finished_run, finished_call):
    """The digest is one aggregate per table: no call row is read into the
    job, however many calls the run has."""
    from simulate.tasks.eval_summary_tasks import _grading_digest

    with CaptureQueriesContext(connection) as queries:
        _grading_digest(finished_run)

    sqls = [q["sql"] for q in queries.captured_queries]
    assert sqls and all("md5(" in sql.lower() for sql in sqls), sqls


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    ["status_with_no_verdicts", "verdict", "new_call", "config"],
)
def test_the_grading_digest_sees_each_change_the_summary_reads(
    finished_run, finished_call, scenario, change
):
    from simulate.models import SimulateEvalConfig
    from simulate.tasks.eval_summary_tasks import _grading_digest

    CallExecution.objects.filter(id=finished_call.id).update(eval_outputs=None)
    before = _grading_digest(finished_run)
    assert _grading_digest(finished_run) == before

    if change == "status_with_no_verdicts":
        CallExecution.objects.filter(id=finished_call.id).update(
            status=CallExecution.CallStatus.FAILED
        )
    elif change == "verdict":
        CallExecution.objects.filter(id=finished_call.id).update(
            eval_outputs={"config-1": {"output": "Failed"}}
        )
    elif change == "new_call":
        CallExecution.objects.create(
            test_execution=finished_run,
            scenario=scenario,
            phone_number="+1234567891",
            status=CallExecution.CallStatus.COMPLETED,
        )
    else:
        from model_hub.models.evals_metric import EvalTemplate

        template = EvalTemplate.objects.create(
            name="digest eval",
            config={},
            organization=finished_run.run_test.organization,
        )
        SimulateEvalConfig.objects.create(
            run_test=finished_run.run_test,
            name="new",
            eval_template=template,
            config={},
        )

    assert _grading_digest(finished_run) != before


# --- Fix My Agent: two presses at once, and a run whose job is gone ------------


@pytest.mark.django_db
def test_a_fix_my_agent_run_whose_job_is_gone_is_answered_failed(
    auth_client, finished_run, no_paid_work
):
    """A run left pending or running by a killed job is told failed past the
    give-up, so the old drawer stops polling it."""
    optimiser = AgentOptimiser.objects.create(
        name="Optimiser", configuration={"type": "simulation_analysis"}
    )
    TestExecution.objects.filter(id=finished_run.id).update(agent_optimiser=optimiser)
    stuck = AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        input_data={},
        status=AgentOptimiserRun.OptimiserStatus.RUNNING,
    )
    AgentOptimiserRun.objects.filter(id=stuck.id).update(
        created_at=timezone.now() - _OPTIMISER_RUN_GIVE_UP - LOST
    )

    result = auth_client.get(OPTIMISER_URL.format(finished_run.id)).json()["result"]

    assert result["status"] == "failed"
    assert result["message"] == (
        "The last analysis stopped before it finished. "
        "Trigger a refresh to start a new one."
    )
    stuck.refresh_from_db()
    assert stuck.status == AgentOptimiserRun.OptimiserStatus.RUNNING


@pytest.mark.django_db(transaction=True)
def test_two_fix_my_agent_refreshes_at_once_start_one_analysis(
    user, workspace, finished_run, finished_call
):
    """Two presses at the same moment: the second waits for the first to
    create its run, then finds it on its way and starts nothing."""
    from conftest import WorkspaceAwareAPIClient
    from simulate.utils import agent_optimiser

    first_inside = threading.Event()
    release_first = threading.Event()
    calls = {"n": 0}
    real_prepare = agent_optimiser.prepare_simulation_analysis_input

    def _prepare(test_execution_id):
        calls["n"] += 1
        if calls["n"] == 1:
            first_inside.set()
            release_first.wait(timeout=10)
        return real_prepare(test_execution_id) or {"test_execution_id": "x"}

    answers = []

    def _press():
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        try:
            answers.append(
                client.post(
                    OPTIMISER_REFRESH_URL.format(finished_run.id), {}, format="json"
                )
            )
        finally:
            client.stop_workspace_injection()
            connection.close()

    with (
        patch(
            "simulate.utils.agent_optimiser.prepare_simulation_analysis_input",
            side_effect=_prepare,
        ),
        patch(
            "simulate.tasks.agent_optimiser_tasks.execute_optimiser_run.delay"
        ) as analysis_queued,
    ):
        first = threading.Thread(target=_press)
        first.start()
        assert first_inside.wait(timeout=10)
        second = threading.Thread(target=_press)
        second.start()
        second.join(timeout=1)  # held at the lock while the first is inside
        release_first.set()
        first.join(timeout=20)
        second.join(timeout=20)

    assert [a.status_code for a in answers] == [200, 200]
    assert AgentOptimiserRun.objects.count() == 1
    analysis_queued.assert_called_once()


# --- the scan below must see every way of naming the summary -------------------


def test_the_scan_sees_an_aliased_import_and_a_name_in_a_string():
    source = (
        "from simulate.tasks.eval_summary_tasks import run_eval_summary_task as rs\n"
        "def by_alias():\n"
        "    rs.apply_async(args=('x',))\n"
        "def by_string(module):\n"
        "    getattr(module, 'run_eval_summary_task')('x')\n"
    )
    found = {"run_eval_summary_task": set()}

    _record_source(source, "somewhere.py", found)

    assert found["run_eval_summary_task"] == {
        ("somewhere.py", "by_alias"),
        ("somewhere.py", "by_string"),
    }


# --- who may start the summary at all -----------------------------------------

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {"tests", "migrations", ".venv", "venv", "node_modules", "__pycache__"}

# Each name, and the only functions that may use it. Anything else that
# queues, calls or hands over the summary is a new way for paid work to start
# by itself, and must be a person's request before it is added here.
ALLOWED_USERS = {
    "run_eval_summary_task": {
        # The summary pane's Refresh button.
        ("simulate/views/run_test.py", "RunTestEvalExplanationSummaryRefreshView.post"),
        # The Fix My Agent job, which a person's refresh queued.
        ("simulate/tasks/agent_optimiser_tasks.py", "_with_run_summary"),
    },
    "_get_cluster_dict_by_eval": {
        ("simulate/tasks/eval_summary_tasks.py", "run_eval_summary_task"),
    },
}


def _record_users(node, scope, path, found, aliases):
    """Record, under each name in ``found``, the function that uses it: by
    its name, by an alias it was imported as, or as a string (a lookup by
    name, such as ``getattr`` or a start by activity name)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _record_users(child, (*scope, child.name), path, found, aliases)
            continue
        name = None
        if isinstance(child, ast.Name):
            name = aliases.get(child.id, child.id)
        elif isinstance(child, ast.Attribute):
            name = child.attr
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            name = child.value
        if name in found:
            found[name].add((path, ".".join(scope)))
        _record_users(child, scope, path, found, aliases)


def _record_source(text, path, found):
    """Record the users of each name in ``found`` in one file's source."""
    tree = ast.parse(text)
    aliases = {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname and alias.name in found
    }
    _record_users(tree, (), path, found, aliases)


def _users_of(names):
    """Every function (by file and dotted name) that uses one of ``names``,
    outside imports, tests and migrations."""
    found = {name: set() for name in names}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if SKIPPED_DIRS.intersection(relative.parts[:-1]):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not any(name in text for name in names):
            continue
        _record_source(text, relative.as_posix(), found)
    return found


def test_only_a_persons_request_starts_the_summary():
    """The summary is queued or run only by the summary's Refresh button and
    by the Fix My Agent job a person started. No completion, verdict, rerun,
    finalizer, monitor or page read uses it."""
    found = _users_of(set(ALLOWED_USERS))
    for name, allowed in ALLOWED_USERS.items():
        assert found[name] == allowed, name
