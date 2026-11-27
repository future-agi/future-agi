"""Run Simulation's Debug failures starts no analysis of a run too small to read,
one where nothing failed, or one whose calls have not all finished. An analysis the
run already has is still returned, and the run's KPIs say it has one."""

from unittest.mock import patch

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    AgentOptimiser,
    AgentOptimiserRun,
    Scenarios,
)
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.selectors.debug_failures import connected_call_count
from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

pytestmark = pytest.mark.django_db

VOICE = CallExecution.SimulationCallType.VOICE
TEXT = CallExecution.SimulationCallType.TEXT
COMPLETED = CallExecution.CallStatus.COMPLETED
FAILED = CallExecution.CallStatus.FAILED

TOO_FEW = "You need at least 15 connected calls to debug failures."
NOTHING_FAILED = "Nothing failed in this run, so there’s nothing to debug."
RUN_IN_PROGRESS = "Diagnosis is available once every call in this run finishes."
CALLS_UNFINISHED = (
    "Some calls in this run didn’t finish, so Debug failures can’t read it."
)

# 14 connected, one of them failing; the voice call that never ran is not connected.
FOURTEEN_CONNECTED = [
    *[(COMPLETED, 30)] * 13,
    (FAILED, 30),
    (COMPLETED, 0),
]
NONE_FAILING = [(COMPLETED, 30)] * 15
ELIGIBLE = [*[(COMPLETED, 30)] * 14, (FAILED, 30)]


def _run(
    organization, workspace, calls, status=TestExecution.ExecutionStatus.COMPLETED
):
    """A run with one voice call per (status, duration_seconds)."""
    run = RunTest.objects.create(
        name="Refusals", organization=organization, workspace=workspace
    )
    execution = TestExecution.objects.create(run_test=run, status=status)
    scenario = Scenarios.objects.create(
        name="Refund",
        source="Refund policy",
        organization=organization,
        workspace=workspace,
    )
    for call_status, duration in calls:
        CallExecution.objects.create(
            test_execution=execution,
            scenario=scenario,
            simulation_call_type=VOICE,
            status=call_status,
            duration_seconds=duration,
        )
    return execution


def _analysis_url(execution):
    return f"/simulate/test-executions/{execution.id}/optimiser-analysis/"


def _kpis(client, execution):
    response = client.get(f"/simulate/test-executions/{execution.id}/kpis/")
    assert response.status_code == 200
    return response.json()


def _analysed(execution):
    """Gives the run an earlier, completed analysis."""
    optimiser = AgentOptimiser.objects.create(name="Earlier")
    execution.agent_optimiser = optimiser
    execution.save(update_fields=["agent_optimiser"])
    AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        result={"insights": "Earlier analysis"},
    )


def _runs(execution):
    return AgentOptimiserRun.objects.filter(
        agent_optimiser__test_executions=execution
    ).count()


@pytest.fixture
def dispatch():
    """The analysis task's dispatch, and an input the run's calls would give."""
    with (
        patch.object(execute_optimiser_run, "delay") as delay,
        patch(
            "simulate.utils.agent_optimiser.prepare_simulation_analysis_input",
            side_effect=lambda execution_id: {"test_execution_id": execution_id},
        ),
    ):
        yield delay


def _refused(response, code, detail):
    assert response.status_code == 409
    assert response.json()["code"] == code
    assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    ("calls", "code", "detail"),
    [
        (FOURTEEN_CONNECTED, "investigation_too_few_calls", TOO_FEW),
        (NONE_FAILING, "investigation_nothing_failed", NOTHING_FAILED),
    ],
    ids=["too_few_calls", "nothing_failed"],
)
@pytest.mark.parametrize("route", ["read", "refresh"])
def test_refuses_to_start_an_analysis(
    auth_client, organization, workspace, dispatch, calls, code, detail, route
):
    execution = _run(organization, workspace, calls)

    if route == "read":
        response = auth_client.get(_analysis_url(execution))
    else:
        response = auth_client.post(
            f"{_analysis_url(execution)}refresh/", {}, format="json"
        )

    _refused(response, code, detail)
    assert _runs(execution) == 0
    dispatch.assert_not_called()


def test_starts_an_analysis_of_15_connected_calls_and_a_failure(
    auth_client, organization, workspace, dispatch
):
    execution = _run(organization, workspace, ELIGIBLE)

    response = auth_client.post(
        f"{_analysis_url(execution)}refresh/", {}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "pending"
    assert _runs(execution) == 1
    dispatch.assert_called_once()


def test_opening_a_run_the_rule_allows_starts_nothing(
    auth_client, organization, workspace, dispatch
):
    # Each analysis is a chain of paid model calls: only the refresh starts one.
    execution = _run(organization, workspace, ELIGIBLE)

    response = auth_client.get(_analysis_url(execution))

    assert response.status_code == 200
    assert response.json()["result"]["response"] is None
    assert _runs(execution) == 0
    dispatch.assert_not_called()


def test_returns_the_analysis_a_blocked_run_already_has(
    auth_client, organization, workspace, dispatch
):
    execution = _run(organization, workspace, NONE_FAILING)
    _analysed(execution)

    # The run page opens it: the KPIs say the run has an analysis.
    assert _kpis(auth_client, execution)["has_debug_failures_analysis"] is True
    read = auth_client.get(_analysis_url(execution))
    refresh = auth_client.post(f"{_analysis_url(execution)}refresh/", {}, format="json")

    assert read.status_code == 200
    assert read.json()["result"]["response"] == {"insights": "Earlier analysis"}
    # A refresh is a new analysis, so the rule refuses it.
    _refused(refresh, "investigation_nothing_failed", NOTHING_FAILED)
    assert _runs(execution) == 1
    dispatch.assert_not_called()


def _ask(client, execution, route):
    if route == "read":
        return client.get(_analysis_url(execution))
    return client.post(f"{_analysis_url(execution)}refresh/", {}, format="json")


@pytest.mark.parametrize(
    "status",
    [TestExecution.ExecutionStatus.CANCELLED, TestExecution.ExecutionStatus.FAILED],
)
@pytest.mark.parametrize("route", ["read", "refresh"])
def test_starts_an_analysis_of_a_stopped_run(
    auth_client, organization, workspace, dispatch, status, route
):
    execution = _run(organization, workspace, ELIGIBLE, status=status)

    response = _ask(auth_client, execution, route)

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "pending"
    assert _runs(execution) == 1
    dispatch.assert_called_once()


@pytest.mark.parametrize(
    "status",
    [
        TestExecution.ExecutionStatus.PENDING,
        TestExecution.ExecutionStatus.RUNNING,
        TestExecution.ExecutionStatus.EVALUATING,
        TestExecution.ExecutionStatus.CANCELLING,
    ],
)
@pytest.mark.parametrize("route", ["read", "refresh"])
def test_refuses_to_start_an_analysis_of_a_run_still_in_progress(
    auth_client, organization, workspace, dispatch, status, route
):
    execution = _run(organization, workspace, ELIGIBLE, status=status)

    response = _ask(auth_client, execution, route)

    _refused(response, "execution_not_completed", RUN_IN_PROGRESS)
    assert _runs(execution) == 0
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        TestExecution.ExecutionStatus.COMPLETED,
        TestExecution.ExecutionStatus.CANCELLED,
        TestExecution.ExecutionStatus.FAILED,
    ],
)
@pytest.mark.parametrize("route", ["read", "refresh"])
def test_refuses_to_start_an_analysis_of_a_finished_run_with_an_open_call(
    auth_client, organization, workspace, dispatch, status, route
):
    open_call = (CallExecution.CallStatus.ONGOING, 0)
    execution = _run(organization, workspace, [*ELIGIBLE, open_call], status=status)

    response = _ask(auth_client, execution, route)

    _refused(response, "execution_calls_unfinished", CALLS_UNFINISHED)
    assert _runs(execution) == 0
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [TestExecution.ExecutionStatus.RUNNING, TestExecution.ExecutionStatus.CANCELLED],
)
def test_returns_the_analysis_a_run_already_has_whatever_its_status(
    auth_client, organization, workspace, dispatch, status
):
    execution = _run(organization, workspace, ELIGIBLE, status=status)
    _analysed(execution)

    read = auth_client.get(_analysis_url(execution))

    assert read.status_code == 200
    assert read.json()["result"]["response"] == {"insights": "Earlier analysis"}
    assert _runs(execution) == 1
    dispatch.assert_not_called()


def test_a_cancelled_runs_finished_calls_are_analysed(
    auth_client, organization, workspace
):
    execution = _run(
        organization,
        workspace,
        [*ELIGIBLE, (CallExecution.CallStatus.CANCELLED, 0)],
        status=TestExecution.ExecutionStatus.CANCELLED,
    )
    # Its eval explanations are summarised already, so no summary is computed.
    execution.eval_explanation_summary = {}
    execution.save(update_fields=["eval_explanation_summary"])
    analysis = {"agent_level": {}, "system_level": {}}

    # The task's body runs inline, without the activity wrapper's connection
    # handling; only the analysis agent's model call is stubbed.
    with (
        patch.object(
            execute_optimiser_run,
            "delay",
            side_effect=execute_optimiser_run._original_func,
        ),
        patch("simulate.utils.agent_optimiser.SimulationAnalysisAgent") as agent,
    ):
        agent.return_value.analyze.return_value = analysis
        response = auth_client.get(_analysis_url(execution))

    assert response.status_code == 200
    run = AgentOptimiserRun.objects.get(agent_optimiser__test_executions=execution)
    assert run.status == AgentOptimiserRun.OptimiserStatus.COMPLETED
    assert run.result == analysis
    scenarios = agent.return_value.analyze.call_args.kwargs["simulation_data"][
        "scenarios"
    ]
    assert {scenario["call_execution_id"] for scenario in scenarios} == {
        str(call_id)
        for call_id in execution.calls.filter(status=COMPLETED).values_list(
            "id", flat=True
        )
    }


def test_kpis_count_the_failing_calls_of_a_finished_run(
    auth_client, organization, workspace
):
    execution = _run(organization, workspace, ELIGIBLE)

    response = auth_client.get(f"/simulate/test-executions/{execution.id}/kpis/")

    assert response.status_code == 200
    assert response.json()["failing_calls"] == 1


def test_kpis_count_a_completed_call_that_failed_its_eval(
    auth_client, organization, workspace
):
    # Unlike failed_calls (call status), the failing count reads the call's outcome.
    execution = _run(organization, workspace, NONE_FAILING)
    binding = SimulateEvalConfig.objects.create(
        name="Goal",
        eval_template=EvalTemplate.objects.create(
            name="Goal template", organization=organization, config={}
        ),
        run_test=execution.run_test,
        config={},
    )
    url = f"/simulate/test-executions/{execution.id}/kpis/"
    call = execution.calls.first()

    def grade(output):
        call.eval_outputs = {
            str(binding.id): {
                "name": "Goal",
                "output_type": "Pass/Fail",
                "output": output,
            }
        }
        call.save(update_fields=["eval_outputs"])
        return auth_client.get(url).json()

    passed, failed = grade("Passed"), grade("Failed")

    assert (passed["failing_calls"], passed["failed_calls"]) == (0, 0)
    assert (failed["failing_calls"], failed["failed_calls"]) == (1, 0)


def test_kpis_send_no_failing_count_for_a_live_run(
    auth_client, organization, workspace
):
    execution = _run(
        organization, workspace, ELIGIBLE, status=TestExecution.ExecutionStatus.RUNNING
    )

    response = auth_client.get(f"/simulate/test-executions/{execution.id}/kpis/")

    assert response.status_code == 200
    assert response.json()["failing_calls"] is None


def test_kpis_say_a_finished_run_has_an_analysis(auth_client, organization, workspace):
    execution = _run(organization, workspace, ELIGIBLE)
    _analysed(execution)

    assert _kpis(auth_client, execution)["has_debug_failures_analysis"] is True


def test_kpis_say_a_refused_read_left_no_analysis(
    auth_client, organization, workspace, dispatch
):
    execution = _run(organization, workspace, NONE_FAILING)

    refused = auth_client.get(_analysis_url(execution))
    execution.refresh_from_db()

    assert refused.status_code == 409
    # A read creates nothing, refused or not.
    assert execution.agent_optimiser_id is None
    assert _kpis(auth_client, execution)["has_debug_failures_analysis"] is False


def test_kpis_send_no_analysis_flag_for_a_live_run(
    auth_client, organization, workspace
):
    execution = _run(
        organization, workspace, ELIGIBLE, status=TestExecution.ExecutionStatus.RUNNING
    )
    _analysed(execution)

    assert _kpis(auth_client, execution)["has_debug_failures_analysis"] is None


@pytest.mark.parametrize(
    ("last_call", "rule_count", "card_count"),
    [
        # The voice run's card counts calls that ran for some time; the rule also
        # counts a chat that completed.
        ({"simulation_call_type": TEXT, "duration_seconds": None}, 15, 14),
        # The card reads deleted calls too; the rule reads live calls only.
        (
            {"simulation_call_type": VOICE, "duration_seconds": 30, "deleted": True},
            14,
            15,
        ),
    ],
    ids=["completed_chat", "deleted_call"],
)
def test_kpis_send_the_connected_count_the_rule_reads(
    auth_client, organization, workspace, last_call, rule_count, card_count
):
    execution = _run(organization, workspace, [(COMPLETED, 30)] * 14)
    execution.run_test.agent_definition = AgentDefinition.objects.create(
        organization=organization,
        workspace=workspace,
        agent_name="Voice agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12025550100",
        inbound=True,
        languages=["en"],
    )
    execution.run_test.save(update_fields=["agent_definition"])
    CallExecution.objects.create(
        test_execution=execution,
        scenario=execution.calls.first().scenario,
        status=COMPLETED,
        **last_call,
    )

    kpis = _kpis(auth_client, execution)

    assert kpis["debug_failures_connected_calls"] == rule_count
    assert connected_call_count(execution) == rule_count
    # The Connected card keeps its own count.
    assert kpis["connected_calls"] == card_count


def test_kpis_send_no_rule_connected_count_for_a_live_run(
    auth_client, organization, workspace
):
    execution = _run(
        organization, workspace, ELIGIBLE, status=TestExecution.ExecutionStatus.RUNNING
    )

    assert _kpis(auth_client, execution)["debug_failures_connected_calls"] is None
