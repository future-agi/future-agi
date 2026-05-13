import json

import pytest

from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution
from simulate.models.test_execution import TestExecution as SimTestExecution
from simulate.utils.test_execution_utils import TestExecutionUtils


def _persist_column_order(test_execution, scenario, simulate_eval_config, output_type):
    test_execution.scenario_ids = [str(scenario.id)]
    test_execution.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "id": "scenario_context",
                "column_name": "Scenario Context",
                "visible": True,
                "data_type": "text",
                "type": "scenario_dataset_column",
                "scenario_id": str(scenario.id),
            },
            {
                "id": str(simulate_eval_config.id),
                "column_name": simulate_eval_config.name,
                "visible": True,
                "type": "evaluation",
                "eval_config": {"output": output_type},
            },
        ],
    }
    test_execution.save(update_fields=["scenario_ids", "execution_metadata"])


def _eval_filter(eval_config_id, filter_type, filter_op, filter_value):
    return [
        {
            "column_id": str(eval_config_id),
            "filter_config": {
                "filter_type": filter_type,
                "filter_op": filter_op,
                "filter_value": filter_value,
            },
        }
    ]


def _result_ids(response):
    return {item["id"] for item in response.data["results"]}


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Filter Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Filter Simulator",
        prompt="You are testing filters.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace, agent_definition, simulator_agent):
    return Scenarios.objects.create(
        name="Filter Scenario",
        source="Customer needs help.",
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status="completed",
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent, scenario):
    run_test = RunTest.objects.create(
        name="Filter Run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return run_test


@pytest.fixture
def test_execution(db, run_test, agent_definition, simulator_agent):
    return SimTestExecution.objects.create(
        run_test=run_test,
        status=SimTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
    )


@pytest.fixture
def eval_template(db, organization, workspace):
    return EvalTemplate.objects.create(
        name="Filter Eval",
        organization=organization,
        workspace=workspace,
        config={"output": "Pass/Fail"},
    )


@pytest.fixture
def simulate_eval_config(db, run_test, eval_template):
    return SimulateEvalConfig.objects.create(
        run_test=run_test,
        eval_template=eval_template,
        name="Resolution Quality",
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestTestExecutionFilters:
    def test_filters_historical_eval_column_from_column_order(
        self, test_execution, scenario, simulate_eval_config
    ):
        passed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Passed",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )
        failed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Failed",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )

        filtered = TestExecutionUtils()._apply_filters(
            CallExecution.objects.filter(test_execution=test_execution),
            [
                {
                    "column_id": str(simulate_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Failed",
                    },
                }
            ],
            [],
            eval_configs_map={},
            column_order=[
                {
                    "id": str(simulate_eval_config.id),
                    "type": "evaluation",
                    "eval_config": {"output": "Pass/Fail"},
                }
            ],
        )

        assert list(filtered) == [failed_call]
        assert passed_call not in filtered

    def test_pass_fail_filter_matches_legacy_fail_alias(
        self, test_execution, scenario, simulate_eval_config
    ):
        failed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Fail",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )

        filtered = TestExecutionUtils()._apply_filters(
            CallExecution.objects.filter(test_execution=test_execution),
            [
                {
                    "column_id": str(simulate_eval_config.id),
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Failed",
                    },
                }
            ],
            [],
            eval_configs_map={str(simulate_eval_config.id): simulate_eval_config},
        )

        assert list(filtered) == [failed_call]

    def test_score_eval_filter_converts_percent_to_stored_decimal(
        self, test_execution, scenario, simulate_eval_config
    ):
        simulate_eval_config.eval_template.config = {"output": "score"}
        simulate_eval_config.eval_template.save(update_fields=["config"])
        high_score_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": 0.82,
                    "output_type": "score",
                    "status": "completed",
                }
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": 0.72,
                    "output_type": "score",
                    "status": "completed",
                }
            },
        )

        filtered = TestExecutionUtils()._apply_filters(
            CallExecution.objects.filter(test_execution=test_execution),
            [
                {
                    "column_id": str(simulate_eval_config.id),
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "greater_than",
                        "filter_value": 80,
                    },
                }
            ],
            [],
            eval_configs_map={str(simulate_eval_config.id): simulate_eval_config},
        )

        assert list(filtered) == [high_score_call]


@pytest.mark.django_db
class TestTestExecutionFilterAPI:
    def test_api_filters_historical_eval_column_from_column_order(
        self, auth_client, test_execution, scenario, simulate_eval_config
    ):
        _persist_column_order(
            test_execution, scenario, simulate_eval_config, "Pass/Fail"
        )
        simulate_eval_config.deleted = True
        simulate_eval_config.save(update_fields=["deleted"])

        passed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Passed",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )
        failed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Failed",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )

        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    _eval_filter(
                        simulate_eval_config.id,
                        "text",
                        "equals",
                        "Failed",
                    )
                ),
                "limit": 20,
            },
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert _result_ids(response) == {str(failed_call.id)}
        assert str(passed_call.id) not in _result_ids(response)

    def test_api_pass_fail_filter_matches_legacy_fail_alias(
        self, auth_client, test_execution, scenario, simulate_eval_config
    ):
        _persist_column_order(
            test_execution, scenario, simulate_eval_config, "Pass/Fail"
        )
        failed_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Fail",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": "Pass",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        )

        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    _eval_filter(
                        simulate_eval_config.id,
                        "text",
                        "equals",
                        "Failed",
                    )
                ),
                "limit": 20,
            },
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert _result_ids(response) == {str(failed_call.id)}

    def test_api_score_eval_filter_converts_percent_to_stored_decimal(
        self, auth_client, test_execution, scenario, simulate_eval_config
    ):
        simulate_eval_config.eval_template.config = {"output": "score"}
        simulate_eval_config.eval_template.save(update_fields=["config"])
        _persist_column_order(test_execution, scenario, simulate_eval_config, "score")
        high_score_call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": 0.82,
                    "output_type": "score",
                    "status": "completed",
                }
            },
        )
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status=CallExecution.CallStatus.COMPLETED,
            eval_outputs={
                str(simulate_eval_config.id): {
                    "output": 0.72,
                    "output_type": "score",
                    "status": "completed",
                }
            },
        )

        response = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    _eval_filter(
                        simulate_eval_config.id,
                        "number",
                        "greater_than",
                        80,
                    )
                ),
                "limit": 20,
            },
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert _result_ids(response) == {str(high_score_call.id)}
