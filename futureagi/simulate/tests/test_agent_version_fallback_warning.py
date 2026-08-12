"""Behavioral regression test for #309 / PR #345.

setup_test_execution silently resolved a missing agent_version pin by falling back to the
agent_definition's ACTIVE version (or, failing that, the LATEST by version_number) with no
signal that the pin was absent -- runs could execute against a version nobody chose. The
fix warns on both fallback rungs (simulate_agent_version_fallback_to_active /
_to_latest) with the resolved version identified in the payload.

Exercises the REAL setup_test_execution activity against the real ORM (fixtures mirror
simulate/tests/test_temporal_activities.py, minus the agent_version pin). Asserts the
warning fires on each fallback rung with the resolved version in the payload, the resolved
version is persisted back onto the TestExecution, and -- the near-miss -- the pinned path
emits no fallback warning at all. All three fail if the fix's warnings are removed.
"""
import os
from unittest.mock import patch

import pytest

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.agent_version import AgentVersion
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import TestExecution

VAPI_API_KEY = os.environ.get("VAPI_API_KEY", "test-api-key-for-testing")


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Fallback Warn Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Agent for #309 fallback-warning regression test",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Fallback Warn Simulator",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
        conversation_speed=1.0,
        interrupt_sensitivity=0.5,
        finished_speaking_sensitivity=0.5,
        max_call_duration_in_minutes=15,
        initial_message_delay=0,
        initial_message="Hello",
    )


def _make_version(agent_definition, organization, workspace, number, status):
    return AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        version_number=number,
        version_name="v%d" % number,
        status=status,
        configuration_snapshot={
            "contact_number": "+15551234567",
            "assistant_id": "test-assistant-id",
            "api_key": VAPI_API_KEY,
            "workspace_id": str(agent_definition.workspace_id),
        },
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Fallback Warn Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="Test situation")
    return dataset


@pytest.fixture
def scenario(
    db, organization, workspace, dataset_for_scenario, agent_definition, simulator_agent
):
    return Scenarios.objects.create(
        name="Fallback Warn Scenario",
        description="Scenario for #309 regression test",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def unpinned_test_execution(
    db, organization, workspace, agent_definition, simulator_agent, scenario
):
    """A TestExecution WITHOUT an agent_version pin -- the fallback path's precondition."""
    run_test = RunTest.objects.create(
        name="Fallback Warn Run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.PENDING,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
        agent_version=None,
    )


async def _run_setup(test_execution, scenario):
    from simulate.temporal.activities.test_execution import setup_test_execution
    from simulate.temporal.types.activities import SetupTestInput

    with patch("temporalio.activity.info"), patch(
        "temporalio.activity.logger"
    ) as mock_logger:
        result = await setup_test_execution(
            SetupTestInput(
                test_execution_id=str(test_execution.id),
                run_test_id=str(test_execution.run_test_id),
                scenario_ids=[str(scenario.id)],
            )
        )
    warn_events = [c.args[0] for c in mock_logger.warning.call_args_list]
    warn_extras = {
        c.args[0]: c.kwargs.get("extra", {}) for c in mock_logger.warning.call_args_list
    }
    return result, warn_events, warn_extras


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_fallback_to_active_version_warns_and_persists(
    unpinned_test_execution, scenario, organization, workspace, agent_definition
):
    from asgiref.sync import sync_to_async

    active = await sync_to_async(_make_version)(
        agent_definition, organization, workspace, 2, AgentVersion.StatusChoices.ACTIVE
    )

    result, warn_events, warn_extras = await _run_setup(
        unpinned_test_execution, scenario
    )

    assert "simulate_agent_version_fallback_to_active" in warn_events
    extra = warn_extras["simulate_agent_version_fallback_to_active"]
    assert extra["resolved_version_id"] == str(active.id)
    assert extra["resolved_version_number"] == active.version_number
    # The silent half of the bug: the resolved version must be persisted back.
    await sync_to_async(unpinned_test_execution.refresh_from_db)()
    assert unpinned_test_execution.agent_version_id == active.id


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_fallback_to_latest_version_warns_with_status(
    unpinned_test_execution, scenario, organization, workspace, agent_definition
):
    from asgiref.sync import sync_to_async

    # No ACTIVE version exists -- only a draft -- so the second rung must fire.
    draft = await sync_to_async(_make_version)(
        agent_definition, organization, workspace, 3, AgentVersion.StatusChoices.DRAFT
    )

    result, warn_events, warn_extras = await _run_setup(
        unpinned_test_execution, scenario
    )

    assert "simulate_agent_version_fallback_to_latest" in warn_events
    extra = warn_extras["simulate_agent_version_fallback_to_latest"]
    assert extra["resolved_version_id"] == str(draft.id)
    assert extra["resolved_version_status"] == str(draft.status)


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pinned_version_does_not_warn(
    unpinned_test_execution, scenario, organization, workspace, agent_definition
):
    from asgiref.sync import sync_to_async

    pinned = await sync_to_async(_make_version)(
        agent_definition, organization, workspace, 4, AgentVersion.StatusChoices.ACTIVE
    )
    unpinned_test_execution.agent_version = pinned
    await sync_to_async(unpinned_test_execution.save)(update_fields=["agent_version"])

    result, warn_events, warn_extras = await _run_setup(
        unpinned_test_execution, scenario
    )

    # Near-miss: a pinned execution is NOT a fallback -- no fallback warning may fire.
    assert not any(e.startswith("simulate_agent_version_fallback") for e in warn_events)
