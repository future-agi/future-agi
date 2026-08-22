"""Regression tests for simulation call-execution numeric filters."""

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.utils.test_execution_utils import TestExecutionUtils


@pytest.fixture
def filter_test_data(db, organization, user, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name="Filter Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1230001111",
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator_agent = SimulatorAgent.objects.create(
        name="Filter Simulator Agent",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Filter Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    column = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(column.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=column, row=row, value="Test situation")
    scenario = Scenarios.objects.create(
        name="Filter Test Scenario",
        description="Scenario for numeric filter tests",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name="Filter Run Test",
        description="Run for numeric filter tests",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )
    calls = [
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            response_time_ms=None,
            avg_agent_latency_ms=None,
            customer_cost_cents=None,
            cost_cents=None,
        ),
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            response_time_ms=500,
            avg_agent_latency_ms=100,
            customer_cost_cents=None,
            cost_cents=5,
        ),
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            response_time_ms=1000,
            avg_agent_latency_ms=200,
            customer_cost_cents=10,
            cost_cents=None,
        ),
    ]
    return calls


def _apply_numeric_filter(calls, column_id, filter_op):
    errors = []
    queryset = TestExecutionUtils()._apply_filters(
        CallExecution.objects.filter(test_execution=calls[0].test_execution),
        [
            {
                "column_id": column_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": filter_op,
                    "filter_value": None,
                },
            }
        ],
        errors,
        {},
    )
    assert errors == []
    return set(queryset.values_list("id", flat=True))


@pytest.mark.django_db
def test_number_filters_support_null_operators(filter_test_data):
    calls = filter_test_data

    assert _apply_numeric_filter(calls, "avg_agent_latency_ms", "is_null") == {
        calls[0].id
    }
    assert _apply_numeric_filter(calls, "avg_agent_latency_ms", "is_not_null") == {
        calls[1].id,
        calls[2].id,
    }


@pytest.mark.django_db
def test_any_cost_filter_treats_all_cost_fields_as_one_nullable_value(
    filter_test_data,
):
    calls = filter_test_data

    assert _apply_numeric_filter(calls, "cost", "is_null") == {calls[0].id}
    assert _apply_numeric_filter(calls, "cost", "is_not_null") == {
        calls[1].id,
        calls[2].id,
    }


@pytest.mark.django_db
def test_response_time_filter_supports_null_operators_without_a_value(
    filter_test_data,
):
    calls = filter_test_data

    assert _apply_numeric_filter(calls, "responseTime", "is_null") == {calls[0].id}
    assert _apply_numeric_filter(calls, "responseTime", "is_not_null") == {
        calls[1].id,
        calls[2].id,
    }
