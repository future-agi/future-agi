"""
Tests for the async task listing endpoint (notification center data layer).

Run with: pytest model_hub/tests/test_async_tasks_api.py -v
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evaluation import Evaluation, StatusChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import RunPrompter

ASYNC_TASKS_URL = "/model-hub/async-tasks/"


@pytest.fixture
def eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="hallucination_check",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
        eval_tags=["llm"],
        visible_ui=True,
    )


@pytest.fixture
def dataset(organization, workspace):
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


def _create_evaluation(
    user, organization, workspace, eval_template, status_value=StatusChoices.PENDING
):
    return Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=eval_template,
        status=status_value,
    )


def _create_run_prompt(
    organization, workspace, dataset, status_value=StatusType.RUNNING.value
):
    return RunPrompter.objects.create(
        name="Test Run Prompt",
        model="gpt-4o",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        status=status_value,
    )


def _create_experiment(user, organization, dataset, status_value=StatusType.COMPLETED.value):
    return ExperimentsTable.objects.create(
        name="Test Experiment",
        dataset=dataset,
        user=user,
        status=status_value,
    )


class TestAsyncTaskListView:
    def test_lists_tasks_with_normalized_status(
        self, auth_client, user, organization, workspace, eval_template, dataset
    ):
        _create_evaluation(user, organization, workspace, eval_template, StatusChoices.PENDING)
        _create_run_prompt(organization, workspace, dataset, StatusType.RUNNING.value)
        _create_experiment(user, organization, dataset, StatusType.COMPLETED.value)

        response = auth_client.get(ASYNC_TASKS_URL)

        assert response.status_code == status.HTTP_200_OK
        tasks = {task["type"]: task for task in response.data["tasks"]}
        assert set(tasks) == {"evaluation", "run_prompt", "experiment"}
        assert tasks["evaluation"]["status"] == "queued"
        assert tasks["run_prompt"]["status"] == "running"
        assert tasks["experiment"]["status"] == "completed"

    def test_new_task_appears_and_newest_first(
        self, auth_client, user, organization, workspace, eval_template
    ):
        _create_evaluation(user, organization, workspace, eval_template)
        assert len(auth_client.get(ASYNC_TASKS_URL).data["tasks"]) == 1

        new_eval = _create_evaluation(user, organization, workspace, eval_template)
        response = auth_client.get(ASYNC_TASKS_URL)

        tasks = response.data["tasks"]
        assert len(tasks) == 2
        assert tasks[0]["id"] == str(new_eval.id)
        assert tasks[0]["type"] == "evaluation"

    def test_completed_evaluation_reported_as_completed(
        self, auth_client, user, organization, workspace, eval_template
    ):
        evaluation = _create_evaluation(user, organization, workspace, eval_template)
        evaluation.mark_as_processing()
        evaluation.update_with_result({"value": "Passed", "output": "bool"})

        response = auth_client.get(ASYNC_TASKS_URL)

        assert response.status_code == status.HTTP_200_OK
        task = response.data["tasks"][0]
        assert task["id"] == str(evaluation.id)
        assert task["status"] == "completed"

    def test_failed_evaluation_reported_as_failed(
        self, auth_client, user, organization, workspace, eval_template
    ):
        evaluation = _create_evaluation(user, organization, workspace, eval_template)
        evaluation.mark_as_failed("model timed out")

        response = auth_client.get(ASYNC_TASKS_URL)

        task = response.data["tasks"][0]
        assert task["status"] == "failed"
        assert task["error_message"] == "model timed out"

    def test_tasks_are_isolated_per_organization(
        self, auth_client, user, organization, workspace, eval_template
    ):
        other_org = Organization.objects.create(name="Other Organization")
        other_user = User.objects.create_user(
            email="other@futureagi.com",
            password="testpassword123",
            name="Other User",
            organization=other_org,
        )
        other_template = EvalTemplate.no_workspace_objects.create(
            name="other_eval",
            organization=other_org,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail"},
            eval_tags=["llm"],
            visible_ui=True,
        )
        _create_evaluation(other_user, other_org, None, other_template)

        response = auth_client.get(ASYNC_TASKS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["tasks"] == []

    def test_limit_parameter_is_respected(
        self, auth_client, user, organization, workspace, eval_template
    ):
        for _ in range(3):
            _create_evaluation(user, organization, workspace, eval_template)

        response = auth_client.get(ASYNC_TASKS_URL, {"limit": 2})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["tasks"]) == 2

    def test_unauthenticated_request_is_rejected(self, organization):
        response = APIClient().get(ASYNC_TASKS_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
