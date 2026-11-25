"""Stored optimiser input keeps built-in metrics and evaluation bindings apart.

POST /simulate/test-executions/<id>/optimiser-analysis/refresh/ stores
``input_data["aggregate_metrics"]`` on a new AgentOptimiserRun. The analysis
task dispatch is mocked, so no model is called.
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import StatusType
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import (
    AgentDefinition,
    AgentOptimiserRun,
    CallExecution,
    RunTest,
    Scenarios,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

BUILT_IN_KEYS = {
    "total_calls",
    "success_rate",
    "avg_score",
    "avg_response",
    "calls_attempted",
    "connected_calls",
    "calls_connected_percentage",
    "avg_agent_latency",
    "avg_user_interruption_count",
    "agent_talk_percentage",
    "customer_talk_percentage",
    "failed_calls",
}


@pytest.fixture
def run_setup(db, organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Optimiser Names Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15550001000",
        inbound=True,
        description="Agent for optimiser input tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name="Optimiser Names Scenario",
        description="Scenario for optimiser input tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.SCRIPT,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name="Optimiser Names Run",
        description="Run for optimiser input tests",
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        completed_calls=2,
        agent_definition=agent,
        eval_explanation_summary={},
    )
    template = EvalTemplate.objects.create(
        name="Optimiser Names Template", config={}, organization=organization
    )
    return test_execution, scenario, run_test, template


def _binding(run_setup, name):
    _, _, run_test, template = run_setup
    return str(
        SimulateEvalConfig.objects.create(
            eval_template=template, name=name, run_test=run_test
        ).id
    )


def _pass_fail(name, passed):
    return {
        "name": name,
        "output": "Passed" if passed else "Failed",
        "output_type": "Pass/Fail",
    }


def _create_calls(run_setup, per_call_eval_outputs):
    test_execution, scenario, _, _ = run_setup
    for index, (overall_score, eval_outputs) in enumerate(per_call_eval_outputs):
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            phone_number=f"+1555100{index:04d}",
            status=CallExecution.CallStatus.COMPLETED,
            overall_score=overall_score,
            response_time_ms=1000 + 200 * index,
            duration_seconds=60,
            eval_outputs=eval_outputs,
        )


def _refresh_and_read_stored_aggregate(auth_client, test_execution):
    with patch.object(execute_optimiser_run, "delay") as dispatch:
        response = auth_client.post(
            f"/simulate/test-executions/{test_execution.id}/optimiser-analysis/refresh/",
            {},
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.content
    test_execution.refresh_from_db(fields=["agent_optimiser"])
    runs = list(
        AgentOptimiserRun.objects.filter(agent_optimiser=test_execution.agent_optimiser)
    )
    assert len(runs) == 1
    dispatch.assert_called_once_with(str(runs[0].id))
    return runs[0].input_data["aggregate_metrics"]


@pytest.mark.integration
@pytest.mark.api
class TestOptimiserStoredAggregateMetricNames:
    def test_evaluation_named_like_a_built_in_does_not_replace_it(
        self, auth_client, run_setup
    ):
        score = _binding(run_setup, "Score")
        _create_calls(
            run_setup,
            [
                (
                    8.0,
                    {score: {"name": "Score", "output": 0.2, "output_type": "score"}},
                ),
                (
                    6.0,
                    {score: {"name": "Score", "output": 0.2, "output_type": "score"}},
                ),
            ],
        )

        stored = _refresh_and_read_stored_aggregate(auth_client, run_setup[0])

        assert stored["avg_score"] == 7.0
        assert stored["avg_response"] == 1100
        assert stored["avg_score_2"] == 20.0
        assert set(stored) == BUILT_IN_KEYS | {"avg_score_2"}

    def test_bindings_sharing_a_display_name_are_averaged_separately(
        self, auth_client, run_setup
    ):
        first, second = sorted(
            [_binding(run_setup, "Tone"), _binding(run_setup, "Tone")]
        )
        _create_calls(
            run_setup,
            [
                (
                    8.0,
                    {
                        first: _pass_fail("Tone", True),
                        second: _pass_fail("Tone", False),
                    },
                ),
                (
                    6.0,
                    {
                        first: _pass_fail("Tone", True),
                        second: _pass_fail("Tone", False),
                    },
                ),
            ],
        )

        stored = _refresh_and_read_stored_aggregate(auth_client, run_setup[0])

        assert stored["avg_tone"] == 100.0
        assert stored.get("avg_tone_2") == 0.0
        assert set(stored) == BUILT_IN_KEYS | {"avg_tone", "avg_tone_2"}

    def test_names_differing_only_in_case_do_not_overwrite_each_other(
        self, auth_client, run_setup
    ):
        upper = _binding(run_setup, "Tone")
        lower = _binding(run_setup, "tone")
        _create_calls(
            run_setup,
            [
                (
                    8.0,
                    {
                        upper: _pass_fail("Tone", True),
                        lower: _pass_fail("tone", False),
                    },
                ),
            ],
        )

        stored = _refresh_and_read_stored_aggregate(auth_client, run_setup[0])

        expected = (
            {"avg_tone": 100.0, "avg_tone_2": 0.0}
            if upper < lower
            else {"avg_tone": 0.0, "avg_tone_2": 100.0}
        )
        assert {key: stored.get(key) for key in expected} == expected
        assert set(stored) == BUILT_IN_KEYS | set(expected)

    def test_a_suffix_never_takes_a_key_another_evaluation_name_produces(
        self, auth_client, run_setup
    ):
        first, second = sorted(
            [_binding(run_setup, "Tone"), _binding(run_setup, "Tone")]
        )
        tone_two = _binding(run_setup, "Tone 2")
        _create_calls(
            run_setup,
            [
                (
                    8.0,
                    {
                        first: _pass_fail("Tone", True),
                        second: _pass_fail("Tone", False),
                        tone_two: {
                            "name": "Tone 2",
                            "output": 0.5,
                            "output_type": "score",
                        },
                    },
                ),
            ],
        )

        stored = _refresh_and_read_stored_aggregate(auth_client, run_setup[0])

        assert stored["avg_tone_2"] == 50.0
        assert stored["avg_tone"] == 100.0
        assert stored.get("avg_tone_3") == 0.0
        assert set(stored) == BUILT_IN_KEYS | {"avg_tone", "avg_tone_2", "avg_tone_3"}
