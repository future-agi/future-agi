"""
Unit tests for the scenario-completeness gate used by execute endpoints.
"""

import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.utils.scenario_completeness import check_scenarios_incomplete


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+1234567890",
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Sim Agent",
        prompt="Test simulator",
        organization=organization,
        workspace=workspace,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4-turbo",
    )


def _scenario(
    organization, workspace, agent_definition, simulator_agent, *, name, status_
):
    return Scenarios.objects.create(
        name=name,
        description="",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=status_,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    return rt


@pytest.mark.parametrize("scenario_ids", [[], None])
def test_returns_400_when_no_scenario_ids(run_test, scenario_ids):
    response = check_scenarios_incomplete(scenario_ids, run_test)

    assert response is not None
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.data["result"]
    assert payload["error"] == "No scenarios"
    assert payload["scenarios"] == []


def test_returns_none_when_all_scenarios_completed(
    db, run_test, organization, workspace, agent_definition, simulator_agent
):
    s1 = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="A",
        status_=StatusType.COMPLETED.value,
    )
    s2 = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="B",
        status_=StatusType.COMPLETED.value,
    )
    run_test.scenarios.add(s1, s2)

    assert check_scenarios_incomplete([s1.id, s2.id], run_test) is None


def test_returns_400_when_any_scenario_running(
    db, run_test, organization, workspace, agent_definition, simulator_agent
):
    s1 = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="Done",
        status_=StatusType.COMPLETED.value,
    )
    s2 = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="StillRunning",
        status_=StatusType.RUNNING.value,
    )
    run_test.scenarios.add(s1, s2)

    response = check_scenarios_incomplete([s1.id, s2.id], run_test)

    assert response is not None
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    payload = response.data["result"]
    assert payload["error"] == "Scenarios incomplete"
    names = [s["name"] for s in payload["scenarios"]]
    assert names == ["StillRunning"]
    assert payload["scenarios"][0]["status"] == StatusType.RUNNING.value


def test_failed_scenarios_also_block(
    db, run_test, organization, workspace, agent_definition, simulator_agent
):
    s = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="Broken",
        status_=StatusType.FAILED.value,
    )
    run_test.scenarios.add(s)

    response = check_scenarios_incomplete([s.id], run_test)

    assert response is not None
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["result"]["scenarios"][0]["status"] == StatusType.FAILED.value


def test_cross_org_scenarios_are_not_leaked(
    db, run_test, user, organization, workspace, agent_definition, simulator_agent
):
    """A scenario belonging to another org must NOT appear in the gate's
    response, even if its UUID is passed in."""
    other_org = Organization.objects.create(name="Other Org")
    other_ws = Workspace.objects.create(
        name="Other WS",
        organization=other_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    other_agent = AgentDefinition.objects.create(
        agent_name="Other Agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        contact_number="+0987654321",
        inbound=True,
        organization=other_org,
        workspace=other_ws,
        languages=["en"],
    )
    other_sim = SimulatorAgent.objects.create(
        name="Other Sim",
        prompt="x",
        organization=other_org,
        workspace=other_ws,
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4-turbo",
    )
    foreign = Scenarios.objects.create(
        name="ForeignSecret",
        description="",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=other_org,
        workspace=other_ws,
        agent_definition=other_agent,
        simulator_agent=other_sim,
        status=StatusType.RUNNING.value,
    )

    # Caller passes the foreign UUID as if it were one of theirs.
    response = check_scenarios_incomplete([foreign.id], run_test)

    # Gate scopes through run_test.scenarios → foreign UUID is not attached,
    # so it's silently filtered. No leak, no false 400.
    assert response is None


def test_unattached_scenarios_silently_ignored(
    db, run_test, organization, workspace, agent_definition, simulator_agent
):
    """Scenario in the same org but NOT attached to this run_test should not
    affect the gate."""
    unattached = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="Unattached",
        status_=StatusType.RUNNING.value,
    )

    response = check_scenarios_incomplete([unattached.id], run_test)

    assert response is None


def test_soft_deleted_scenarios_excluded(
    db, run_test, organization, workspace, agent_definition, simulator_agent
):
    s = _scenario(
        organization,
        workspace,
        agent_definition,
        simulator_agent,
        name="DeletedRunning",
        status_=StatusType.RUNNING.value,
    )
    run_test.scenarios.add(s)
    s.deleted = True
    s.save()

    response = check_scenarios_incomplete([s.id], run_test)

    # Deleted scenario is filtered out; gate doesn't catch it. The executor
    # downstream is responsible for rejecting deleted scenarios.
    assert response is None


def test_nonexistent_uuids_silently_ignored(run_test):
    response = check_scenarios_incomplete([uuid.uuid4()], run_test)
    assert response is None
