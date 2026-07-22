"""
API tests for Test Execution Rerun endpoints.

Tests cover:
- CallExecutionRerunView: POST /simulate/test-executions/<uuid>/rerun-calls/
- TestExecutionRerunView: POST /simulate/run-tests/<uuid>/rerun-test-executions/
"""

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios, SimulateEvalConfig
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.simulator_agent import SimulatorAgent
from simulate.models.test_execution import (
    CallExecution,
    CallExecutionSnapshot,
    EvalExplanationSummaryStatus,
    TestExecution,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a test agent definition."""
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
    """Create a test simulator agent."""
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
    """Create a dataset for scenarios."""
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
    """Create a test scenario."""
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario description",
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
    """Create a test run test."""
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
    """Create a test execution."""
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
    """Create a completed call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-123",
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
def call_execution_2(db, test_execution, scenario):
    """Create a second completed call execution."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-456",
        eval_outputs={"eval1": {"score": 0.7}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


@pytest.fixture
def test_execution_2(db, run_test, simulator_agent, agent_definition):
    """Create a second test execution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )


@pytest.fixture
def call_execution_te2(db, test_execution_2, scenario):
    """Create a call execution for test_execution_2."""
    return CallExecution.objects.create(
        test_execution=test_execution_2,
        scenario=scenario,
        phone_number="+1234567890",
        status=CallExecution.CallStatus.COMPLETED,
        service_provider_call_id="vapi-test-789",
        eval_outputs={"eval1": {"score": 0.8}},
        call_metadata={
            "base_prompt": "You are a test agent",
            "voice_settings": {"provider": "elevenlabs"},
            "call_direction": "inbound",
            "eval_started": True,
            "eval_completed": True,
        },
    )


# ============================================================================
# CallExecutionRerunView Tests (existing endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionRerunView:
    """Tests for POST /simulate/test-executions/<uuid>/rerun-calls/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/rerun-calls/"

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_select_all(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
        call_execution_2,
    ):
        """Test eval_only rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "eval_only"
        assert data["success_count"] == 2
        assert data["failure_count"] == 0
        assert len(data["successful_reruns"]) == 2

        # Verify Temporal workflow was started
        assert mock_rerun.call_count == 1

        # Verify snapshots were created
        assert CallExecutionSnapshot.objects.count() == 2

        # Verify test execution status updated
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_specific_ids(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
        call_execution_2,
    ):
        """Test eval_only rerun with specific call_execution_ids."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 1
        assert str(call_execution.id) in data["successful_reruns"]

    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_eval_only_tolerates_dispatch_failure(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 0
        assert data["failure_count"] == 1
        assert data["failed_reruns"][0]["call_execution_id"] == str(call_execution.id)
        assert "temporal dispatch timed out" in data["failed_reruns"][0]["error"]
        assert data["dispatch_error"] == "temporal dispatch timed out"
        assert "async dispatch failed" in data["message"]
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.eval_outputs == {"eval1": {"score": 0.9}}
        assert call_execution.call_metadata["eval_completed"] is True

    @pytest.mark.requires_ee
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_call_and_eval_select_all(
        self, mock_rerun, auth_client, test_execution, call_execution
    ):
        """Test call_and_eval rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "call_and_eval"
        assert data["success_count"] == 1

        # Verify test execution status updated - RUNNING because Temporal workflow started
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING

    @pytest.mark.requires_ee
    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_call_and_eval_marks_failed_when_dispatch_fails(
        self,
        mock_rerun,
        auth_client,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "call_and_eval",
                "call_execution_ids": [str(call_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success_count"] == 0
        assert data["failure_count"] == 1
        assert data["failed_reruns"][0]["call_execution_id"] == str(call_execution.id)
        assert "temporal dispatch timed out" in data["failed_reruns"][0]["error"]
        assert data["dispatch_error"] == "temporal dispatch timed out"
        assert "async dispatch failed" in data["message"]
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.FAILED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.status == CallExecution.CallStatus.FAILED
        assert "Rerun dispatch failed" in call_execution.ended_reason
        assert (
            CreateCallExecution.objects.filter(call_execution=call_execution).count()
            == 0
        )

    def test_rerun_missing_params(self, auth_client, test_execution):
        """Test rerun with neither select_all nor call_execution_ids."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_invalid_rerun_type(self, auth_client, test_execution):
        """Test rerun with invalid rerun_type."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "invalid", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_rejects_unknown_fields(
        self, mock_rerun, auth_client, test_execution
    ):
        """Unknown request fields should fail before any rerun work starts."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]
        mock_rerun.assert_not_called()

    def test_rerun_nonexistent_test_execution(self, auth_client):
        """Test rerun with non-existent test_execution_id returns error."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # get_object_or_404 raises Http404, caught by generic except -> 500
        assert response.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def test_rerun_unauthenticated(self, api_client, test_execution):
        """Test rerun without authentication."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = api_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # DRF returns 403 Forbidden for unauthenticated requests by default
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_no_call_executions(self, mock_rerun, auth_client, test_execution):
        """Test rerun when test execution has no call executions."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# TestExecutionRerunView Tests (new endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionRerunView:
    """Tests for POST /simulate/run-tests/<uuid>/rerun-test-executions/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/rerun-test-executions/"

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_select_all(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        call_execution_2,
        test_execution_2,
        call_execution_te2,
    ):
        """Test eval_only rerun with select_all=True across multiple test executions."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "eval_only"
        assert data["total_test_executions"] == 2
        assert data["overall_success_count"] == 3  # 2 from te1 + 1 from te2
        assert data["overall_failure_count"] == 0
        assert len(data["results"]) == 2

        # Verify rerun_call_executions was called for each test execution
        assert mock_rerun.call_count == 2

        # Verify snapshots were created
        assert CallExecutionSnapshot.objects.count() == 3

        # Verify both test executions updated
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert test_execution_2.status == TestExecution.ExecutionStatus.EVALUATING

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_eval_only_specific_ids(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test eval_only rerun with specific test_execution_ids."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total_test_executions"] == 1
        assert data["overall_success_count"] == 1

        # Only test_execution should be updated, not test_execution_2
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.EVALUATING
        assert test_execution_2.status == TestExecution.ExecutionStatus.COMPLETED

    @patch(
        "simulate.temporal.client.rerun_call_executions",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_rerun_test_execution_tolerates_dispatch_failure(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["overall_success_count"] == 0
        assert data["overall_failure_count"] == 1
        assert data["results"][0]["success_count"] == 0
        assert data["results"][0]["failure_count"] == 1
        assert data["results"][0]["dispatch_error"] == "temporal dispatch timed out"
        assert data["results"][0]["failed_reruns"][0]["call_execution_id"] == str(
            call_execution.id
        )
        assert (
            "temporal dispatch timed out"
            in data["results"][0]["failed_reruns"][0]["error"]
        )
        mock_rerun.assert_called_once()

        test_execution.refresh_from_db()
        call_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["rerun_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.eval_outputs == {"eval1": {"score": 0.9}}
        assert call_execution.call_metadata["eval_completed"] is True

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_select_all_with_exclusion(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test select_all=True with test_execution_ids acting as exclusion list."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Only test_execution_2 should be processed (test_execution excluded)
        assert data["total_test_executions"] == 1
        assert data["results"][0]["test_execution_id"] == str(test_execution_2.id)

    @pytest.mark.requires_ee
    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_call_and_eval_select_all(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test call_and_eval rerun with select_all=True."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["rerun_type"] == "call_and_eval"
        assert data["total_test_executions"] == 2
        assert data["overall_success_count"] == 2

        # Verify both test executions set to RUNNING (Temporal workflow started)
        test_execution.refresh_from_db()
        test_execution_2.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.RUNNING
        assert test_execution_2.status == TestExecution.ExecutionStatus.RUNNING

    def test_rerun_missing_params(self, auth_client, run_test):
        """Test rerun with neither select_all nor test_execution_ids."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rerun_invalid_rerun_type(self, auth_client, run_test):
        """Test rerun with invalid rerun_type."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "invalid", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_rejects_unknown_fields(self, mock_rerun, auth_client, run_test):
        """Unknown request fields should fail before any rerun work starts."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "select_all": True,
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]
        mock_rerun.assert_not_called()

    def test_rerun_nonexistent_run_test(self, auth_client):
        """Test rerun with non-existent run_test_id returns error."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # get_object_or_404 raises Http404, caught by generic except -> 500
        assert response.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def test_rerun_unauthenticated(self, api_client, run_test):
        """Test rerun without authentication."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = api_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        # DRF returns 403 Forbidden for unauthenticated requests by default
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_rerun_skips_test_execution_with_no_calls(
        self,
        mock_rerun,
        auth_client,
        run_test,
        test_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test that test executions with no call executions are skipped."""
        mock_rerun.return_value = {"workflow_id": "test-workflow-id", "merged": False}
        # test_execution has no call executions, test_execution_2 has one
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"rerun_type": "eval_only", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total_test_executions"] == 2

        # Find the skipped result
        skipped = [r for r in data["results"] if r.get("skipped")]
        assert len(skipped) == 1
        assert skipped[0]["test_execution_id"] == str(test_execution.id)

    def test_rerun_no_test_executions_found(self, auth_client, run_test):
        """Test rerun when no test executions match."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "rerun_type": "eval_only",
                "test_execution_ids": [str(uuid.uuid4())],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# RunTestEvalExplanationSummaryView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalExplanationSummary:
    """Tests for GET /simulate/test-executions/<uuid>/eval-explanation-summary/"""

    URL_TEMPLATE = "/simulate/test-executions/{}/eval-explanation-summary/"

    def test_get_eval_explanation_summary_returns_summary(
        self, auth_client, test_execution
    ):
        """Populated summary is returned with status and last_updated."""
        summary_payload = {
            "total_evals": 3,
            "buckets": [
                {"label": "pass", "count": 2},
                {"label": "fail", "count": 1},
            ],
        }
        last_updated = timezone.now()
        test_execution.eval_explanation_summary = summary_payload
        test_execution.eval_explanation_summary_last_updated = last_updated
        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.COMPLETED
        )
        test_execution.save(
            update_fields=[
                "eval_explanation_summary",
                "eval_explanation_summary_last_updated",
                "eval_explanation_summary_status",
            ]
        )

        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["response"] == summary_payload
        assert result["status"] == EvalExplanationSummaryStatus.COMPLETED
        assert result["last_updated"] is not None

        # Status should not be flipped back to PENDING when summary already exists
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )

    def test_get_eval_explanation_summary_unauthenticated_returns_401(
        self, api_client, test_execution
    ):
        """Unauthenticated request is rejected before hitting the view logic."""
        url = self.URL_TEMPLATE.format(test_execution.id)
        response = api_client.get(url)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        body = response.json()
        assert "detail" in body or body.get("status") is False

    def test_get_eval_explanation_summary_not_found_returns_404(self, auth_client):
        """Unknown test-execution id returns 404 with the standard error body."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert body.get("status") is False

    def test_get_eval_explanation_summary_other_workspace_returns_404(
        self,
        auth_client,
        organization,
        user,
        agent_definition,
        simulator_agent,
    ):
        """A test-execution scoped to another workspace of the same org is not visible."""
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other EvalSummary Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_run_test = RunTest.no_workspace_objects.create(
            name="Hidden Run Test",
            description="Hidden run test in another workspace.",
            agent_definition=agent_definition,
            simulator_agent=simulator_agent,
            organization=organization,
            workspace=other_workspace,
        )
        hidden_summary = {"total_evals": 42, "buckets": []}
        hidden_last_updated = timezone.now()
        hidden_test_execution = TestExecution.no_workspace_objects.create(
            run_test=hidden_run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            total_scenarios=1,
            total_calls=1,
            simulator_agent=simulator_agent,
            agent_definition=agent_definition,
            eval_explanation_summary=hidden_summary,
            eval_explanation_summary_last_updated=hidden_last_updated,
            eval_explanation_summary_status=EvalExplanationSummaryStatus.COMPLETED,
        )

        url = self.URL_TEMPLATE.format(hidden_test_execution.id)
        response = auth_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        body = response.json()
        assert body.get("status") is False

        # Target row untouched
        hidden_test_execution.refresh_from_db()
        assert hidden_test_execution.eval_explanation_summary == hidden_summary
        assert (
            hidden_test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.COMPLETED
        )


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionRuntimeContracts:
    """Request validation tests for related test-execution actions."""

    def test_column_order_update_accepts_canonical_body(
        self, auth_client, test_execution
    ):
        url = f"/simulate/test-executions/{test_execution.id}/column-order/"
        column_order = [
            {"id": "status", "column_name": "Status", "visible": True},
            {"id": "latency", "column_name": "Latency", "visible": False},
        ]

        response = auth_client.put(
            url,
            {"column_order": column_order},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["column_order"] == column_order
        test_execution.refresh_from_db()
        assert test_execution.execution_metadata["column_order"] == column_order

    def test_column_order_update_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = f"/simulate/test-executions/{test_execution.id}/column-order/"
        response = auth_client.put(
            url,
            {
                "column_order": [
                    {"id": "status", "column_name": "Status", "visible": True}
                ],
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @patch("simulate.views.run_test.TestExecutor")
    def test_cancel_accepts_empty_body(
        self, mock_test_executor, auth_client, test_execution
    ):
        mock_test_executor.return_value.cancel_test.return_value = {
            "success": True,
            "message": "Cancellation initiated",
            "test_execution_id": str(test_execution.id),
        }
        url = f"/simulate/test-executions/{test_execution.id}/cancel/"

        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.CANCELLING

    def test_cancel_rejects_unknown_fields(self, auth_client, test_execution):
        url = f"/simulate/test-executions/{test_execution.id}/cancel/"
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_eval_summary_refresh_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/"
            "eval-explanation-summary/refresh/"
        )
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @patch(
        "simulate.views.run_test.run_eval_summary_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_eval_summary_refresh_tolerates_dispatch_failure(
        self, mock_apply_async, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/"
            "eval-explanation-summary/refresh/"
        )

        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert "marked pending" in response.data["result"]["message"]
        mock_apply_async.assert_called_once_with(args=(str(test_execution.id),))
        test_execution.refresh_from_db()
        assert (
            test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )

    def test_optimiser_refresh_rejects_unknown_fields(
        self, auth_client, test_execution
    ):
        url = (
            f"/simulate/test-executions/{test_execution.id}/optimiser-analysis/refresh/"
        )
        response = auth_client.post(
            url,
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_run_new_evals_rejects_unknown_fields(self, auth_client, run_test):
        url = f"/simulate/run-tests/{run_test.id}/run-new-evals/"
        response = auth_client.post(
            url,
            {
                "select_all": True,
                "eval_config_ids": [],
                "legacy_extra": "should-not-be-accepted",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    @patch(
        "simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_run_new_evals_tolerates_dispatch_failure(
        self,
        mock_apply_async,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        organization,
    ):
        eval_template = EvalTemplate.objects.create(
            name="dispatch failure eval",
            config={"output": "Pass/Fail"},
            organization=organization,
        )
        eval_config = SimulateEvalConfig.objects.create(
            name="dispatch failure config",
            eval_template=eval_template,
            run_test=run_test,
            config={},
            mapping={},
        )
        url = f"/simulate/run-tests/{run_test.id}/run-new-evals/"

        response = auth_client.post(
            url,
            {
                "test_execution_ids": [str(test_execution.id)],
                "eval_config_ids": [str(eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "marked pending" in response.data["message"]
        assert response.data["call_execution_count"] == 1
        mock_apply_async.assert_called_once()

        call_execution.refresh_from_db()
        test_execution.refresh_from_db()
        assert test_execution.status == TestExecution.ExecutionStatus.COMPLETED
        assert test_execution.picked_up_by_executor is False
        assert (
            test_execution.execution_metadata["eval_dispatch_failed"]
            == "temporal dispatch timed out"
        )
        assert call_execution.call_metadata["eval_started"] is False
        assert (
            call_execution.call_metadata["eval_dispatch_failed"]
            == "temporal dispatch timed out"
        )


# ============================================================================
# TestExecutionRerunSerializer Tests
# ============================================================================


@pytest.mark.unit
class TestTestExecutionRerunSerializer:
    """Tests for TestExecutionRerunSerializer validation."""

    def test_valid_with_select_all(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={"rerun_type": "eval_only", "select_all": True}
        )
        assert serializer.is_valid()

    def test_valid_with_test_execution_ids(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={
                "rerun_type": "call_and_eval",
                "test_execution_ids": [str(uuid.uuid4())],
            }
        )
        assert serializer.is_valid()

    def test_invalid_missing_both(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(data={"rerun_type": "eval_only"})
        assert not serializer.is_valid()

    def test_invalid_rerun_type(self):
        from simulate.serializers.test_execution import TestExecutionRerunSerializer

        serializer = TestExecutionRerunSerializer(
            data={"rerun_type": "bad_type", "select_all": True}
        )
        assert not serializer.is_valid()


# ============================================================================
# TestExecutionBulkDeleteView Tests (new endpoint)
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionBulkDeleteView:
    """Tests for POST /simulate/run-tests/<uuid>/delete-test-executions/"""

    URL_TEMPLATE = "/simulate/run-tests/{}/delete-test-executions/"

    def test_delete_select_all(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test deleting all test executions with select_all=True."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 2
        assert len(data["deleted_ids"]) == 2

        # Verify they're actually deleted
        assert TestExecution.objects.filter(run_test=run_test).count() == 0
        # Verify cascade deleted call executions
        assert (
            CallExecution.objects.filter(test_execution__run_test=run_test).count() == 0
        )

    def test_delete_specific_ids(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test deleting specific test executions by IDs."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"test_execution_ids": [str(test_execution.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 1
        assert str(test_execution.id) in data["deleted_ids"]

        # test_execution_2 should still exist
        assert TestExecution.objects.filter(id=test_execution_2.id).exists()
        assert not TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_select_all_with_exclusion(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
        test_execution_2,
        call_execution_te2,
    ):
        """Test select_all=True with test_execution_ids as exclusion list."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {
                "select_all": True,
                "test_execution_ids": [str(test_execution.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["deleted_count"] == 1
        assert str(test_execution_2.id) in data["deleted_ids"]

        # test_execution should still exist (excluded)
        assert TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_blocks_running_executions(
        self,
        auth_client,
        run_test,
        test_execution,
        call_execution,
    ):
        """Test that running test executions cannot be deleted."""
        test_execution.status = TestExecution.ExecutionStatus.RUNNING
        test_execution.save()

        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # Verify nothing was deleted
        assert TestExecution.objects.filter(id=test_execution.id).exists()

    def test_delete_missing_params(self, auth_client, run_test):
        """Test delete with neither select_all nor test_execution_ids."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_rejects_unknown_fields(self, auth_client, run_test):
        """Unknown request fields should fail before matching executions."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"select_all": True, "legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_delete_nonexistent_run_test(self, auth_client):
        """Test delete with non-existent run_test_id."""
        url = self.URL_TEMPLATE.format(uuid.uuid4())
        response = auth_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def test_delete_unauthenticated(self, api_client, run_test):
        """Test delete without authentication."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = api_client.post(
            url,
            {"select_all": True},
            format="json",
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_delete_no_matching_test_executions(self, auth_client, run_test):
        """Test delete when no test executions match."""
        url = self.URL_TEMPLATE.format(run_test.id)
        response = auth_client.post(
            url,
            {"test_execution_ids": [str(uuid.uuid4())]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionDeleteView:
    """Tests for DELETE /simulate/test-executions/<uuid>/delete/.

    Happy-path child-cascade coverage lives in
    ``test_call_execution_action_scope.TestCallExecutionActionScope
    ::test_test_execution_delete_soft_deletes_child_call_execution``, and
    cross-tenant coverage lives beside it. This class fills the remaining
    not-found + unauthenticated gaps.
    """

    def test_test_execution_delete_not_found_returns_404(self, auth_client):
        response = auth_client.delete(
            f"/simulate/test-executions/{uuid.uuid4()}/delete/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(response.content).lower()

    def test_test_execution_delete_unauthenticated_returns_401(
        self, api_client, test_execution
    ):
        response = api_client.delete(
            f"/simulate/test-executions/{test_execution.id}/delete/"
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        test_execution.refresh_from_db()
        assert test_execution.deleted is False


# ============================================================================
# RunTestEvalExplanationSummaryRefreshView Tests
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestEvalExplanationSummaryRefreshView:
    """Tests for POST /simulate/test-executions/<uuid>/eval-explanation-summary/refresh/"""

    URL_TEMPLATE = (
        "/simulate/test-executions/{}/eval-explanation-summary/refresh/"
    )

    @patch(
        "simulate.views.run_test.run_eval_summary_task.apply_async",
        side_effect=TimeoutError("temporal dispatch timed out"),
    )
    def test_eval_explanation_refresh_marks_failed_when_dispatch_fails(
        self, mock_apply_async, auth_client, test_execution
    ):
        # Seed a terminal state so we can prove the view moved it off COMPLETED
        # and did not leave it stuck mid-refresh when dispatch blew up.
        test_execution.eval_explanation_summary_status = (
            EvalExplanationSummaryStatus.COMPLETED
        )
        test_execution.save(update_fields=["eval_explanation_summary_status"])

        url = self.URL_TEMPLATE.format(test_execution.id)
        response = auth_client.post(url, {}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert "marked pending" in response.data["result"]["message"]
        mock_apply_async.assert_called_once_with(args=(str(test_execution.id),))

        test_execution.refresh_from_db()
        # Should be PENDING or FAILED; must not be stuck in a live state like
        # RUNNING or lie about COMPLETED.
        assert test_execution.eval_explanation_summary_status in {
            EvalExplanationSummaryStatus.PENDING,
            EvalExplanationSummaryStatus.FAILED,
        }
        assert test_execution.eval_explanation_summary_status not in {
            EvalExplanationSummaryStatus.RUNNING,
            EvalExplanationSummaryStatus.COMPLETED,
        }


@pytest.mark.integration
@pytest.mark.api
class TestOptimiserAnalysisRefreshView:
    URL_TEMPLATE = "/simulate/test-executions/{}/optimiser-analysis/refresh/"

    @patch("simulate.utils.agent_optimiser.prepare_simulation_analysis_input")
    @patch("simulate.tasks.agent_optimiser_tasks.execute_optimiser_run")
    def test_optimiser_analysis_refresh_marks_failed_when_dispatch_fails(
        self,
        mock_task,
        mock_prepare,
        auth_client,
        test_execution,
        call_execution,
    ):
        from simulate.models import AgentOptimiserRun

        mock_prepare.return_value = {"test_execution_id": str(test_execution.id)}
        mock_task.delay.side_effect = TimeoutError("temporal dispatch timed out")

        response = auth_client.post(
            self.URL_TEMPLATE.format(test_execution.id), {}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert response.data["result"]["status"] == AgentOptimiserRun.OptimiserStatus.FAILED
        mock_task.delay.assert_called_once()

        run = AgentOptimiserRun.objects.order_by("-created_at").first()
        assert run is not None
        assert run.status == AgentOptimiserRun.OptimiserStatus.FAILED
        assert (run.metadata or {}).get("error", {}).get("dispatch_error") == (
            "temporal dispatch timed out"
        )


# ============================================================================