import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
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

        assert response.status_code == status.HTTP_404_NOT_FOUND
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

    def test_call_execution_delete_soft_deletes_and_stamps_deleted_at(
        self, auth_client, text_call_execution
    ):
        response = auth_client.delete(
            f"/simulate/call-executions/{text_call_execution.id}/delete/"
        )

        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        ), response.content
        text_call_execution.refresh_from_db()
        assert text_call_execution.deleted is True
        assert text_call_execution.deleted_at is not None

    def test_call_execution_delete_not_found_returns_404(self, auth_client):
        response = auth_client.delete(
            f"/simulate/call-executions/{uuid.uuid4()}/delete/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in str(response.content).lower()

    def test_call_execution_delete_unauthenticated_returns_401(
        self, api_client, text_call_execution
    ):
        response = api_client.delete(
            f"/simulate/call-executions/{text_call_execution.id}/delete/"
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        text_call_execution.refresh_from_db()
        assert text_call_execution.deleted is False

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


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionErrorLocalizerTasks:
    def test_error_localizer_tasks_returns_serialized_task_for_own_call_execution(
        self, auth_client, organization, workspace, text_call_execution
    ):
        eval_config_id = str(uuid.uuid4())
        task = ErrorLocalizerTask.objects.create(
            source=ErrorLocalizerSource.SIMULATE,
            status=ErrorLocalizerStatus.COMPLETED,
            input_data={"question": "hi"},
            input_keys=["question"],
            input_types={"question": "text"},
            eval_result={"score": 0.9},
            eval_explanation={"reason": "ok"},
            rule_prompt="be helpful",
            error_analysis={"root_cause": "prompt too vague"},
            selected_input_key="question",
            organization=organization,
            workspace=workspace,
            metadata={
                "call_execution_id": str(text_call_execution.id),
                "eval_config_id": eval_config_id,
            },
        )

        response = auth_client.get(
            f"/simulate/call-executions/{text_call_execution.id}/error-localizer-tasks/"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["call_execution_id"] == str(text_call_execution.id)
        assert response.data["total_tasks"] == 1
        tasks = response.data["error_localizer_tasks"]
        assert len(tasks) == 1
        payload = tasks[0]
        assert payload["task_id"] == str(task.id)
        assert payload["eval_config_id"] == eval_config_id
        assert payload["status"] == "completed"
        assert payload["selected_input_key"] == "question"
        assert payload["input_keys"] == ["question"]
        assert payload["error_analysis"] == {"root_cause": "prompt too vague"}

    def test_error_localizer_tasks_requires_authentication(
        self, api_client, text_call_execution
    ):
        response = api_client.get(
            f"/simulate/call-executions/{text_call_execution.id}/error-localizer-tasks/"
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert "call_execution_id" not in getattr(response, "data", {}) or (
            response.data.get("call_execution_id") is None
        )

    def test_error_localizer_tasks_returns_404_for_unknown_call_execution(
        self, auth_client
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{uuid.uuid4()}/error-localizer-tasks/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Call execution not found" in str(response.content)

    def test_error_localizer_tasks_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/error-localizer-tasks/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Call execution not found" in str(response.content)


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionCrossWorkspaceReadWrite:
    def test_patch_rejects_other_workspace_call_execution_and_leaves_status_unchanged(
        self, auth_client, other_workspace_text_call_execution
    ):
        original_status = other_workspace_text_call_execution.status
        original_ended_reason = other_workspace_text_call_execution.ended_reason

        response = auth_client.patch(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/",
            {"status": CallExecution.CallStatus.FAILED.value},
            format="json",
        )

        other_workspace_text_call_execution.refresh_from_db()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert other_workspace_text_call_execution.status == original_status
        assert (
            other_workspace_text_call_execution.ended_reason == original_ended_reason
        )

    def test_detail_get_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Call execution not found" in str(response.content)

    def test_transcripts_get_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/transcripts/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Call execution not found" in str(response.content)

    def test_logs_get_rejects_other_workspace_call_execution(
        self, auth_client, other_workspace_text_call_execution
    ):
        response = auth_client.get(
            f"/simulate/call-executions/{other_workspace_text_call_execution.id}/logs/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Call execution not found" in str(response.content)


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionPatchStatus:
    def test_patch_status_to_failed_persists_ended_at_and_generic_reason(
        self, auth_client, text_call_execution
    ):
        text_call_execution.status = CallExecution.CallStatus.ONGOING
        text_call_execution.ended_at = None
        text_call_execution.ended_reason = None
        text_call_execution.save(update_fields=["status", "ended_at", "ended_reason"])

        response = auth_client.patch(
            f"/simulate/call-executions/{text_call_execution.id}/",
            {"status": CallExecution.CallStatus.FAILED.value},
            format="json",
        )

        text_call_execution.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.content
        assert text_call_execution.status == CallExecution.CallStatus.FAILED
        assert text_call_execution.ended_at is not None
        assert text_call_execution.ended_reason == "Error processing simulation"

    def test_patch_status_to_cancelled_persists_ended_reason_from_body(
        self, auth_client, text_call_execution
    ):
        text_call_execution.status = CallExecution.CallStatus.ONGOING
        text_call_execution.ended_at = None
        text_call_execution.ended_reason = None
        text_call_execution.save(update_fields=["status", "ended_at", "ended_reason"])

        response = auth_client.patch(
            f"/simulate/call-executions/{text_call_execution.id}/",
            {
                "status": CallExecution.CallStatus.CANCELLED.value,
                "ended_reason": "user cancelled",
            },
            format="json",
        )

        text_call_execution.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.content
        assert text_call_execution.status == CallExecution.CallStatus.CANCELLED
        assert text_call_execution.ended_reason == "user cancelled"

    def test_patch_status_to_completed_preserves_optional_error_reason(
        self, auth_client, text_call_execution
    ):
        text_call_execution.status = CallExecution.CallStatus.ONGOING
        text_call_execution.ended_at = None
        text_call_execution.ended_reason = None
        text_call_execution.save(update_fields=["status", "ended_at", "ended_reason"])

        response = auth_client.patch(
            f"/simulate/call-executions/{text_call_execution.id}/",
            {
                "status": CallExecution.CallStatus.COMPLETED.value,
                "ended_reason": "partial failure",
            },
            format="json",
        )

        text_call_execution.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.content
        assert text_call_execution.status == CallExecution.CallStatus.COMPLETED
        assert text_call_execution.ended_reason == "partial failure"


@pytest.mark.integration
@pytest.mark.api
class TestTestExecutionColumnOrderPatch:
    def test_column_order_patch_persists_new_order(
        self, auth_client, text_call_execution
    ):
        original_order = [
            {"id": "status", "column_name": "Status", "visible": True},
            {"id": "latency", "column_name": "Latency", "visible": True},
        ]
        test_execution = text_call_execution.test_execution
        test_execution.execution_metadata = {"column_order": original_order}
        test_execution.save(update_fields=["execution_metadata"])

        new_order = [
            {"id": "latency", "column_name": "Latency", "visible": True},
            {"id": "status", "column_name": "Status", "visible": False},
        ]
        response = auth_client.put(
            f"/simulate/test-executions/{test_execution.id}/column-order/",
            {"column_order": new_order},
            format="json",
        )

        test_execution.refresh_from_db()

        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.data["column_order"] == new_order
        assert test_execution.execution_metadata["column_order"] == new_order

    def test_column_order_patch_other_workspace_returns_404(
        self, auth_client, other_workspace_text_call_execution
    ):
        original_order = [
            {"id": "status", "column_name": "Status", "visible": True},
        ]
        test_execution = other_workspace_text_call_execution.test_execution
        test_execution.execution_metadata = {"column_order": original_order}
        test_execution.save(update_fields=["execution_metadata"])

        new_order = [
            {"id": "latency", "column_name": "Latency", "visible": False},
        ]
        response = auth_client.put(
            f"/simulate/test-executions/{test_execution.id}/column-order/",
            {"column_order": new_order},
            format="json",
        )

        test_execution.refresh_from_db()

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert test_execution.execution_metadata["column_order"] == original_order


@pytest.mark.integration
@pytest.mark.api
class TestCallExecutionDetailBody:
    def test_get_call_execution_detail_returns_expected_body_keys(
        self, auth_client, text_call_execution
    ):
        text_call_execution.status = CallExecution.CallStatus.COMPLETED
        text_call_execution.phone_number = "+15555550101"
        text_call_execution.customer_number = "+15555550101"
        text_call_execution.provider_call_data = {
            "vapi": {"call_id": "vapi-123", "recording": {"combined": "https://example.com/rec.wav"}}
        }
        text_call_execution.call_metadata = {"channel": "text"}
        text_call_execution.eval_outputs = {str(uuid.uuid4()): {"score": 0.9}}
        text_call_execution.save(
            update_fields=[
                "status",
                "phone_number",
                "customer_number",
                "provider_call_data",
                "call_metadata",
                "eval_outputs",
            ]
        )

        response = auth_client.get(
            f"/simulate/call-executions/{text_call_execution.id}/"
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.data
        for key in (
            "id",
            "status",
            "scenario_id",
            "phone_number",
            "recordings",
            "transcript",
            "eval_outputs",
        ):
            assert key in body, f"missing key {key} in response body"
        assert body["id"] == str(text_call_execution.id)
        assert body["status"] == CallExecution.CallStatus.COMPLETED
        assert body["phone_number"] == "+15555550101"
        # TEXT simulation call type: recordings intentionally empty (chat has no audio).
        assert body["recordings"] == {}
        assert isinstance(body["transcript"], list)
