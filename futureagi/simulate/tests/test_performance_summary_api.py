"""API tests for GET /simulate/test-executions/<uuid>/performance-summary/."""

import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import CallExecution, TestExecution

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        description="Test agent for simulation",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Test Simulator Agent",
        prompt="You are a test simulator agent.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
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
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Password Reset",
        description="Password reset scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario, simulator_agent):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run description",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def test_execution(db, run_test, simulator_agent, agent_definition):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=2,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-123",
        overall_score=8.9,
        eval_outputs={"eval1": {"score": 0.9}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


@pytest.fixture
def failed_call_execution(db, test_execution, scenario):
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.FAILED,
        service_provider_call_id="vapi-test-fail",
        eval_outputs={},
        call_metadata={},
    )


# ============================================================================
# PerformanceSummaryView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetPerformanceSummary:
    """Tests for GET /simulate/test-executions/<uuid>/performance-summary/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/performance-summary/"

    def test_get_performance_summary_returns_summary(
        self,
        auth_client,
        test_execution,
        call_execution,
        failed_call_execution,
    ):
        """Happy path: populated call executions produce metrics and scenarios."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert "test_run_performance_metrics" in data
        metrics = data["test_run_performance_metrics"]
        assert metrics["total_test_runs"] == 2
        assert metrics["pass_rate"] == 50.0
        assert metrics["latest_fail_rate"] == 50.0

        assert "top_performing_scenarios" in data
        scenarios = data["top_performing_scenarios"]
        assert isinstance(scenarios, list)
        assert len(scenarios) >= 1
        top = scenarios[0]
        # PerformanceSummarySerializer coerces scenario dict values to strings.
        assert top["scenario_name"] == "Password Reset"
        assert int(top["test_count"]) == 2
        assert float(top["performance_score"]) == 8.9

    def test_get_performance_summary_unauthenticated_returns_401(
        self, api_client, test_execution
    ):
        """Unauthenticated request must not leak summary data."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = api_client.get(url)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        body = response.json()
        assert "test_run_performance_metrics" not in body
        assert "top_performing_scenarios" not in body

    def test_get_performance_summary_not_found_returns_404(self, auth_client):
        """Unknown test_execution_id returns a not-found error."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert "test_run_performance_metrics" not in body
        assert "top_performing_scenarios" not in body

    def test_get_performance_summary_other_workspace_returns_404(
        self,
        auth_client,
        organization,
        user,
        agent_definition,
        simulator_agent,
        scenario,
    ):
        """Test executions in another workspace of the same org must be hidden."""
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Performance Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_run_test = RunTest.no_workspace_objects.create(
            name="Hidden Run",
            description="Hidden run in other workspace",
            agent_definition=agent_definition,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=other_workspace,
        )
        hidden_run_test.scenarios.add(scenario)
        hidden_execution = TestExecution.no_workspace_objects.create(
            run_test=hidden_run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            total_calls=1,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
        )
        CallExecution.no_workspace_objects.create(
            test_execution=hidden_execution,
            scenario=scenario,
            phone_number="+1234567890",
            status=CallExecution.CallStatus.COMPLETED,
            service_provider_call_id="vapi-hidden-999",
            overall_score=9.9,
        )

        url = self.URL_TEMPLATE.format(hidden_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert "test_run_performance_metrics" not in body
        assert "top_performing_scenarios" not in body

        hidden_execution.refresh_from_db()
        hidden_run_test.refresh_from_db()
        assert hidden_run_test.workspace_id == other_workspace.id
        assert hidden_execution.status == TestExecution.ExecutionStatus.COMPLETED
