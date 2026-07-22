import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, CallExecution, RunTest, Scenarios
from simulate.models.test_execution import (
    EvalExplanationSummaryStatus,
    TestExecution as SimulationTestExecution,
)


def _create_text_call_execution(organization, workspace):
    agent_definition = AgentDefinition.objects.create(
        agent_name=f"Scoped Chat Agent {workspace.id}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="Agent for scoped call execution action tests",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"Scoped Scenario {workspace.id}",
        description="Scenario for scoped call execution action tests",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"Scoped Run {workspace.id}",
        description="Run for scoped call execution action tests",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = SimulationTestExecution.objects.create(
        run_test=run_test,
        status=SimulationTestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
        agent_definition=agent_definition,
    )
    call_execution = CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
    )
    return call_execution


@pytest.fixture
def text_call_execution(db, organization, workspace):
    return _create_text_call_execution(organization, workspace)


@pytest.fixture
def other_workspace_text_call_execution(db, organization, user):
    other_workspace = Workspace.objects.create(
        name="Other scoped simulation workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    return _create_text_call_execution(organization, other_workspace)


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionActionScope:
    def test_branch_analysis_post_without_scenario_graph_returns_404(
        self, auth_client, text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/call-executions/{text_call_execution.id}/branch-analysis/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "No scenario graph" in str(response.content)

    def test_branch_analysis_post_rejects_unknown_body_fields(
        self, auth_client, text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/call-executions/{text_call_execution.id}/branch-analysis/",
            {"legacy_extra": "should-not-be-accepted"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["status"] is False
        assert response.data["details"]["legacy_extra"] == ["Unknown field."]

    def test_branch_analysis_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        get_response = auth_client.get(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/branch-analysis/"
        )
        post_response = auth_client.post(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/branch-analysis/",
            {},
            format="json",
        )

        assert get_response.status_code == status.HTTP_404_NOT_FOUND
        assert post_response.status_code == status.HTTP_404_NOT_FOUND

    def test_chat_send_message_status_guard_for_accessible_completed_call(
        self, auth_client, text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/call-executions/{text_call_execution.id}/chat/send-message/",
            {
                "initiate_chat": False,
                "messages": [{"role": "user", "content": "hello"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not running or evaluating" in str(response.content)

    def test_chat_send_message_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/chat/send-message/",
            {
                "initiate_chat": False,
                "messages": [{"role": "user", "content": "hello"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Call execution not found" in str(response.content)

    def test_test_execution_delete_soft_deletes_child_call_execution(
        self, auth_client, text_call_execution
    ):
        response = auth_client.delete(
            f"/simulate/test-executions/{text_call_execution.test_execution_id}/delete/"
        )

        text_call_execution.refresh_from_db()
        text_call_execution.test_execution.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK
        assert text_call_execution.test_execution.deleted is True
        assert text_call_execution.deleted is True
        assert text_call_execution.deleted_at is not None

    def test_test_execution_delete_rejects_other_workspace_test_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.delete(
            f"/simulate/test-executions/{other_workspace_text_call_execution.test_execution_id}/delete/"
        )

        other_workspace_text_call_execution.refresh_from_db()
        other_workspace_text_call_execution.test_execution.refresh_from_db()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert other_workspace_text_call_execution.test_execution.deleted is False
        assert other_workspace_text_call_execution.deleted is False

    def test_call_execution_delete_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.delete(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/delete/"
        )

        other_workspace_text_call_execution.refresh_from_db()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert other_workspace_text_call_execution.deleted is False

    @patch("simulate.views.run_test.TestExecutor")
    def test_run_test_execute_rejects_other_workspace_run_test(
        self, mock_test_executor, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{other_workspace_text_call_execution.test_execution.run_test_id}/execute/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_test_executor.assert_not_called()

    @patch("simulate.views.run_test.run_eval_summary_task.apply_async")
    def test_eval_summary_refresh_rejects_other_workspace_test_execution(
        self, mock_apply_async, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{other_workspace_text_call_execution.test_execution_id}/eval-explanation-summary/refresh/",
            {},
            format="json",
        )

        other_workspace_text_call_execution.test_execution.refresh_from_db()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert (
            other_workspace_text_call_execution.test_execution.eval_explanation_summary_status
            == EvalExplanationSummaryStatus.PENDING
        )
        mock_apply_async.assert_not_called()

    @patch("simulate.views.run_test.create_optimiser_run_for_test_execution")
    @patch("simulate.views.run_test.get_or_create_optimiser_for_test_execution")
    def test_optimiser_refresh_rejects_other_workspace_test_execution(
        self,
        mock_get_or_create_optimiser,
        mock_create_optimiser_run,
        auth_client,
        other_workspace_text_call_execution,
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{other_workspace_text_call_execution.test_execution_id}/optimiser-analysis/refresh/",
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_get_or_create_optimiser.assert_not_called()
        mock_create_optimiser_run.assert_not_called()

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_call_rerun_rejects_other_workspace_test_execution(
        self, mock_rerun, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/test-executions/{other_workspace_text_call_execution.test_execution_id}/rerun-calls/",
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_rerun.assert_not_called()

    @patch("simulate.temporal.client.rerun_call_executions")
    def test_test_execution_rerun_rejects_other_workspace_run_test(
        self, mock_rerun, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{other_workspace_text_call_execution.test_execution.run_test_id}/rerun-test-executions/",
            {"rerun_type": "call_and_eval", "select_all": True},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_rerun.assert_not_called()

    @patch("simulate.views.run_test.run_new_evals_on_call_executions_task.apply_async")
    def test_run_new_evals_rejects_other_workspace_run_test(
        self, mock_apply_async, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.post(
            f"/simulate/run-tests/{other_workspace_text_call_execution.test_execution.run_test_id}/run-new-evals/",
            {
                "select_all": True,
                "eval_config_ids": [str(uuid.uuid4())],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_apply_async.assert_not_called()
