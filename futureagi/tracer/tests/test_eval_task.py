"""
EvalTask API Tests

Tests for /tracer/eval-task/ endpoints.
"""

import uuid

import pytest
from rest_framework import status

from tracer.models.eval_task import EvalTask, EvalTaskStatus


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskCreateAPI:
    """Tests for POST /tracer/eval-task/ endpoint."""

    def test_create_eval_task_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "New Eval Task",
                "run_type": "continuous",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_eval_task_success(self, auth_client, project, custom_eval_config):
        """Create a new eval task."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "New Eval Task",
                "run_type": "continuous",
                "sampling_rate": 1.0,
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "id" in data

    def test_create_eval_task_missing_project(self, auth_client):
        """Create eval task fails without project."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "name": "No Project Task",
                "run_type": "continuous",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskListAPI:
    """Tests for GET /tracer/eval-task/list_eval_tasks/ endpoint."""

    def test_list_eval_tasks_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/eval-task/list_eval_tasks/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_eval_tasks_success(self, auth_client, project, eval_task):
        """List eval tasks for a project."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "metadata" in data or "table" in data

    def test_list_eval_tasks_empty(self, auth_client, project):
        """List returns empty when no eval tasks exist."""
        # Delete any existing eval tasks
        EvalTask.objects.filter(project=project).delete()

        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskListWithProjectNameAPI:
    """Tests for GET /tracer/eval-task/list_eval_tasks_with_project_name/ endpoint."""

    def test_list_with_project_name_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_with_project_name_success(self, auth_client, project, eval_task):
        """List eval tasks with project names."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "metadata" in data or "table" in data


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskGetLogsAPI:
    """Tests for GET /tracer/eval-task/get_eval_task_logs/ endpoint."""

    def test_get_logs_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(eval_task.id)},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_logs_success(self, auth_client, eval_task):
        """Get logs for an eval task."""
        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(eval_task.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "errors_count" in data or "success_count" in data


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskPauseAPI:
    """Tests for POST /tracer/eval-task/pause_eval_task/ endpoint."""

    def test_pause_eval_task_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        # API expects eval_task_id as query param
        response = api_client.post(
            f"/tracer/eval-task/pause_eval_task/?eval_task_id={eval_task.id}",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_pause_eval_task_success(self, auth_client, eval_task):
        """Pause an eval task."""
        eval_task.status = EvalTaskStatus.RUNNING
        eval_task.save(update_fields=["status"])

        # API expects eval_task_id as query param, NOT body
        response = auth_client.post(
            f"/tracer/eval-task/pause_eval_task/?eval_task_id={eval_task.id}",
        )
        assert response.status_code == status.HTTP_200_OK

        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.PAUSED

    def test_pause_eval_task_not_found(self, auth_client):
        """Pause non-existent eval task fails."""
        fake_id = uuid.uuid4()
        response = auth_client.post(
            f"/tracer/eval-task/pause_eval_task/?eval_task_id={fake_id}",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskUnpauseAPI:
    """Tests for POST /tracer/eval-task/unpause_eval_task/ endpoint."""

    def test_unpause_eval_task_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        # API expects eval_task_id as query param
        response = api_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={eval_task.id}",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unpause_eval_task_success(self, auth_client, eval_task):
        """Unpause a paused eval task."""
        # First pause the task
        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save()

        # API expects eval_task_id as query param, NOT body
        response = auth_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={eval_task.id}",
        )
        assert response.status_code == status.HTTP_200_OK

        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.PENDING


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskDeleteAPI:
    """Tests for POST /tracer/eval-task/mark_eval_tasks_deleted/ endpoint."""

    def test_delete_eval_tasks_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(eval_task.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_eval_tasks_success(self, auth_client, eval_task):
        """Delete eval tasks."""
        # Body parameter
        response = auth_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(eval_task.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.DELETED

    def test_delete_multiple_eval_tasks(self, auth_client, project, custom_eval_config):
        """Delete multiple eval tasks."""
        # Create multiple eval tasks
        task1 = EvalTask.objects.create(
            project=project,
            name="Task 1",
            status=EvalTaskStatus.PENDING,
        )
        task2 = EvalTask.objects.create(
            project=project,
            name="Task 2",
            status=EvalTaskStatus.PENDING,
        )

        response = auth_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(task1.id), str(task2.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        task1.refresh_from_db()
        task2.refresh_from_db()
        assert task1.status == EvalTaskStatus.DELETED
        assert task2.status == EvalTaskStatus.DELETED


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskUpdateAPI:
    """Tests for PATCH /tracer/eval-task/update_eval_task/ endpoint."""

    def test_update_eval_task_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        response = api_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "name": "Updated Name",
                "edit_type": "fresh_run",  # Required field
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_eval_task_success(self, auth_client, eval_task):
        """Update an eval task."""
        # Body parameter with required edit_type
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "name": "Updated Eval Task",
                "edit_type": "fresh_run",  # Required field
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_update_eval_task_not_found(self, auth_client):
        """Update non-existent eval task fails."""
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(uuid.uuid4()),
                "name": "Test",
                "edit_type": "fresh_run",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskGetDetailsAPI:
    """Tests for GET /tracer/eval-task/get_eval_details/ endpoint."""

    def test_get_details_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        # NOTE: API uses 'eval_id', not 'eval_task_id'
        response = api_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(eval_task.id)},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_details_success(self, auth_client, eval_task, custom_eval_config):
        """Get details for an eval task."""
        # NOTE: API uses 'eval_id', not 'eval_task_id'
        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(eval_task.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["name"] == eval_task.name
