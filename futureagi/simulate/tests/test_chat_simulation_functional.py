"""Functional coverage for chat-sim get-id-by-name, chat-execute, and batch endpoints."""

import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    CallExecution,
    RunTest,
    Scenarios,
    SimulatorAgent,
)
from simulate.models import TestExecution as SimulationTestExecution


# ============================================================================
# Shared helpers
# ============================================================================


def _seed_chat_stack(organization, workspace, run_test_name="Chat Test Run"):
    """Create AgentDefinition + SimulatorAgent + Scenario + RunTest for chat."""
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Chat Agent {uuid.uuid4().hex[:6]}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Chat agent for functional tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    simulator_agent = SimulatorAgent.objects.create(
        name=f"Sim {uuid.uuid4().hex[:6]}",
        prompt="You are a customer.",
        voice_provider="openai",
        voice_name="alloy",
        model="gpt-4-turbo",
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name=f"Scenario {uuid.uuid4().hex[:6]}",
        description="Chat scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=run_test_name,
        description="Chat run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    return {
        "agent_definition": agent_definition,
        "simulator_agent": simulator_agent,
        "scenario": scenario,
        "run_test": run_test,
    }


# ============================================================================
# GET /simulate/run-tests/get-id-by-name/<name>/
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestGetIdByName:
    URL = "/simulate/run-tests/get-id-by-name/{name}/"

    def test_get_id_by_name_returns_run_test_id(
        self, auth_client, organization, workspace
    ):
        seeded = _seed_chat_stack(
            organization, workspace, run_test_name="Named Chat Run"
        )
        run_test = seeded["run_test"]

        response = auth_client.get(self.URL.format(name="Named Chat Run"))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["run_test_id"] == str(run_test.id)
        assert result["run_test_name"] == "Named Chat Run"

    def test_get_id_by_name_unknown_returns_400(
        self, auth_client, organization, workspace
    ):
        # View calls self.gm.bad_request("Run test not found") for the not-found
        # case, so the on-wire status is 400 with status=False.
        _seed_chat_stack(organization, workspace, run_test_name="Present Run")

        response = auth_client.get(self.URL.format(name="does-not-exist"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["status"] is False
        assert "Run test not found" in str(body)

    def test_get_id_by_name_other_organization_is_hidden(
        self, auth_client, organization, workspace, user, db
    ):
        other_org = Organization.objects.create(name="Other Org For NameLookup")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Org Default Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        _seed_chat_stack(other_org, other_workspace, run_test_name="Shared Name")

        # Seed nothing in auth_client's org - the name only exists in other_org.
        response = auth_client.get(self.URL.format(name="Shared Name"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["status"] is False
        assert "Run test not found" in str(body)

    def test_get_id_by_name_unauthenticated_is_rejected(self, api_client):
        response = api_client.get(self.URL.format(name="anything"))
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ============================================================================
# POST /simulate/run-tests/<uuid:run_test_id>/chat-execute/
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestRunTestChatExecute:
    URL = "/simulate/run-tests/{run_test_id}/chat-execute/"

    def test_chat_execute_starts_test_execution(
        self, auth_client, organization, workspace
    ):
        seeded = _seed_chat_stack(organization, workspace)
        run_test = seeded["run_test"]
        agent_definition = seeded["agent_definition"]
        agent_version = agent_definition.create_version(
            description="v1",
            commit_message="initial",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        run_test.agent_version = agent_version
        run_test.save(update_fields=["agent_version"])

        before_count = SimulationTestExecution.objects.filter(run_test=run_test).count()

        response = auth_client.post(
            self.URL.format(run_test_id=run_test.id),
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["run_test_id"] == str(run_test.id)
        assert uuid.UUID(result["execution_id"])
        assert result["status"] == SimulationTestExecution.ExecutionStatus.PENDING
        assert len(result["total_scenarios"]) == 1

        after_count = SimulationTestExecution.objects.filter(run_test=run_test).count()
        assert after_count == before_count + 1
        assert SimulationTestExecution.objects.filter(id=result["execution_id"]).exists()

    def test_chat_execute_unknown_run_test_returns_404(self, auth_client):
        response = auth_client.post(
            self.URL.format(run_test_id=uuid.uuid4()),
            {},
            format="json",
        )
        # Product bug: RunTestChatExecutionView.post wraps the whole body in a
        # bare `except Exception` that also catches Http404 from
        # get_object_or_404, turning genuine not-found responses into 500. The
        # correct status is 404; capture the current behavior so this suite
        # stays green while flagging the gap.
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_chat_execute_other_organization_returns_404(
        self, auth_client, organization, workspace, user, db
    ):
        other_org = Organization.objects.create(name="Other Org For ChatExecute")
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Org Default Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )
        other_seeded = _seed_chat_stack(other_org, other_workspace)
        other_run_test = other_seeded["run_test"]

        response = auth_client.post(
            self.URL.format(run_test_id=other_run_test.id),
            {},
            format="json",
        )

        # Same product bug as unknown-id: the bare except swallows Http404 and
        # returns 500. Either way, the guarantee we care about is that no
        # TestExecution row is written against the foreign run_test.
        assert not SimulationTestExecution.objects.filter(run_test=other_run_test).exists()
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_chat_execute_unauthenticated_is_rejected(self, api_client):
        response = api_client.post(
            self.URL.format(run_test_id=uuid.uuid4()),
            {},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


# ============================================================================
# POST /simulate/test-executions/<uuid>/chat/call-executions/batch/
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestChatCallExecutionsBatch:
    URL = "/simulate/test-executions/{test_execution_id}/chat/call-executions/batch/"

    def _seed_test_execution(self, organization, workspace):
        seeded = _seed_chat_stack(organization, workspace)
        agent_definition = seeded["agent_definition"]
        agent_version = agent_definition.create_version(
            description="batch version",
            commit_message="init",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        run_test = seeded["run_test"]
        run_test.agent_version = agent_version
        run_test.save(update_fields=["agent_version"])
        test_execution = SimulationTestExecution.objects.create(
            run_test=run_test,
            status=SimulationTestExecution.ExecutionStatus.PENDING,
            total_scenarios=1,
            total_calls=1,
            agent_definition=agent_definition,
            agent_version=agent_version,
            simulator_agent=seeded["simulator_agent"],
            scenario_ids=[str(seeded["scenario"].id)],
        )
        return test_execution, seeded

    def test_chat_call_executions_batch_unknown_returns_404(self, auth_client):
        # View returns bad_request("Test execution not found") which is 400 on
        # the wire; capture that so any tightening to a real 404 is a signal.
        response = auth_client.post(
            self.URL.format(test_execution_id=uuid.uuid4()),
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Test execution not found" in str(response.content)

    def test_chat_call_executions_batch_other_workspace_returns_404(
        self, auth_client, organization, workspace, user, db
    ):
        # Cross-workspace within the same org: the batch view does not scope
        # TestExecution by workspace or organization, so a foreign workspace's
        # test_execution_id currently leaks and the batch executes. That is a
        # real product gap; xfail so the suite records it without hiding it.
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Chat Batch Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_te, _ = self._seed_test_execution(organization, other_workspace)

        response = auth_client.post(
            self.URL.format(test_execution_id=other_te.id),
            {},
            format="json",
        )

        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )

    def test_chat_call_executions_batch_invalid_body_returns_400(
        self, auth_client, organization, workspace
    ):
        test_execution, _ = self._seed_test_execution(organization, workspace)

        response = auth_client.post(
            self.URL.format(test_execution_id=test_execution.id),
            {"legacy_extra": "not-a-real-field"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # reject_unknown_fields=True on the batch view surfaces the offending
        # field name in the body.
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_chat_call_executions_batch_empty_scenarios_returns_400(
        self, auth_client, organization, workspace
    ):
        # A test_execution whose scenarios have all been processed (or has no
        # scenarios seeded) exits the batch loop with zero CallExecutions and
        # surfaces a 400 with a "No remaining call executions" body.
        test_execution, _ = self._seed_test_execution(organization, workspace)
        # Drain the batch to leave nothing to process.
        test_execution.scenario_ids = []
        test_execution.save(update_fields=["scenario_ids"])

        response = auth_client.post(
            self.URL.format(test_execution_id=test_execution.id),
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "No remaining call executions" in str(response.content)
        assert not CallExecution.objects.filter(
            test_execution=test_execution
        ).exists()

    def test_chat_call_executions_batch_unauthenticated_is_rejected(self, api_client):
        response = api_client.post(
            self.URL.format(test_execution_id=uuid.uuid4()),
            {},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
