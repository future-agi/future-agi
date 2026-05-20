"""Reusable factory helpers for simulate-app tests.

Designed as composable building blocks. Anything you can build with these
should be wireable into an integration test without re-stitching the
underlying FK chains.

Why a module of plain functions instead of pytest fixtures: factories are
called multiple times per test (e.g. "create 3 columns") which is awkward
with fixtures. Plain functions compose naturally; tests that need them
declared at fixture level can wrap them via a one-line conftest fixture.
"""

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Persona, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution


def make_persona(organization, workspace, **overrides):
    """Create a Persona row with sane defaults; overrides win."""
    defaults = dict(
        name="Test Persona",
        organization=organization,
        workspace=workspace,
        persona_type=Persona.PersonaType.WORKSPACE,
    )
    defaults.update(overrides)
    return Persona.objects.create(**defaults)


def make_agent_definition(organization, workspace, *, agent_type=None):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=agent_type or AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="Test agent",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


def make_simulator_agent(organization, workspace, **overrides):
    defaults = dict(
        name="Sim",
        prompt="You are a simulator",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )
    defaults.update(overrides)
    return SimulatorAgent.objects.create(**defaults)


def make_dataset_with_columns(organization, workspace, user, column_specs):
    """Create a Dataset with the named columns and a single Row.

    `column_specs` is a list of (column_name, cell_value) tuples. Returns
    (dataset, row, columns_by_name).
    """
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    columns_by_name = {}
    column_order = []
    for col_name, _ in column_specs:
        col = Column.objects.create(
            dataset=dataset,
            name=col_name,
            data_type="text",
            source=SourceChoices.OTHERS.value,
        )
        columns_by_name[col_name] = col
        column_order.append(str(col.id))
    dataset.column_order = column_order
    dataset.save()

    row = Row.objects.create(dataset=dataset, order=0)
    for col_name, cell_value in column_specs:
        Cell.objects.create(
            dataset=dataset,
            column=columns_by_name[col_name],
            row=row,
            value=cell_value,
        )
    return dataset, row, columns_by_name


def make_scenario(
    organization, workspace, agent_definition, *, dataset=None, metadata=None
):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario description",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
        metadata=metadata or {},
    )


def make_run_test(
    organization, workspace, agent_definition, *, simulator_agent=None, scenarios=None
):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run description",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    if scenarios:
        for scenario in scenarios:
            rt.scenarios.add(scenario)
    return rt


def make_test_execution(
    run_test, *, agent_definition, simulator_agent=None, status=None
):
    return TestExecution.objects.create(
        run_test=run_test,
        status=status or TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


def make_call_execution(test_execution, scenario, *, row_id=None, row_data=None):
    call_metadata = {}
    if row_id is not None:
        call_metadata["row_id"] = str(row_id)
    if row_data is not None:
        call_metadata["row_data"] = row_data
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        call_metadata=call_metadata,
    )
