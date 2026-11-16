"""
EvalTask API Tests

Tests for /tracer/eval-task/ endpoints.
"""

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.temporal.eval_tasks import client as workflow_client
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskLogger, EvalTaskStatus, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.selectors.eval_tasks.stranded import (
    automatic_recovery_eligibility,
    find_stranded_tasks,
)
from tracer.services.eval_tasks.recovery import recover_task
from tracer.views import eval_task as eval_task_views
from tracer.views.eval_task import EvalTaskView

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def make_other_workspace_eval_task(project, user, custom_eval_config):
    other_workspace = Workspace.objects.create(
        name="Other Eval Task Workspace",
        organization=project.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name="Other Eval Task Project",
        organization=project.organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    task = EvalTask.objects.create(
        project=other_project,
        name="Other Workspace Eval Task",
        run_type="continuous",
        sampling_rate=100,
        status=EvalTaskStatus.COMPLETED,
    )
    task.evals.add(custom_eval_config)
    return task


def make_custom_eval_config_for_project(project, custom_eval_config, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=custom_eval_config.eval_template,
        config=custom_eval_config.config or {},
        mapping=custom_eval_config.mapping or {},
        filters={},
    )


_PRIVATE_DB_ERROR = "Code: 159. DB::Exception: private stack and query text"


def _raise_private_db_error(*_args, **_kwargs):
    raise RuntimeError(_PRIVATE_DB_ERROR)


def _assert_sanitized(response, expected_message: str, status_code: int = 400):
    rendered = response.content.decode()
    assert response.status_code == status_code
    assert expected_message in rendered
    assert _PRIVATE_DB_ERROR not in rendered


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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

    def test_create_internal_error_is_sanitized(
        self, auth_client, project, custom_eval_config, monkeypatch
    ):
        monkeypatch.setattr(
            EvalTaskView,
            "_invalid_eval_ids_for_project",
            _raise_private_db_error,
        )

        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Sanitized create",
                "run_type": "continuous",
                "sampling_rate": 100,
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )

        _assert_sanitized(
            response,
            "Evaluation task could not be created",
            status.HTTP_400_BAD_REQUEST,
        )

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

    def test_create_eval_task_rejects_other_workspace_project(
        self, auth_client, project, user, custom_eval_config
    ):
        """Task creation should not accept a project outside the active workspace."""
        other_task = make_other_workspace_eval_task(project, user, custom_eval_config)

        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(other_task.project_id),
                "name": "Cross Workspace Eval Task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_eval_task_rejects_other_project_eval_config(
        self, auth_client, project, custom_eval_config
    ):
        """Task eval configs must belong to the selected task project."""
        other_project = Project.objects.create(
            name="Other Visible Eval Config Project",
            organization=project.organization,
            workspace=project.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_config = make_custom_eval_config_for_project(
            other_project, custom_eval_config, "Other Project Eval Config"
        )

        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Cross Project Eval Config Task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "evals": [str(other_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_eval_task_accepts_linked_source_id_filters(
        self, auth_client, populated_observe_project, eval_template
    ):
        """Trace drawer/Add Evals flows save direct source ids as task filters."""
        project = populated_observe_project["project"]
        trace = populated_observe_project["traces"][0]
        span = populated_observe_project["spans"][0]
        session = populated_observe_project["sessions"][0]
        config = CustomEvalConfig.objects.create(
            name="Linked source eval",
            project=project,
            eval_template=eval_template,
            config={"threshold": 0.8},
            mapping={"input": "input", "output": "output"},
            filters={},
        )

        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Linked trace task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "row_type": "traces",
                "filters": {
                    "trace_id": [str(trace.id)],
                    "span_id": [span.id],
                    "session_id": [str(session.id)],
                },
                "evals": [str(config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        task = EvalTask.objects.get(id=get_result(response)["id"])
        assert task.filters["project_id"] == str(project.id)
        assert task.filters["trace_id"] == [str(trace.id)]
        assert task.filters["span_id"] == [span.id]
        assert task.filters["session_id"] == [str(session.id)]

    def test_create_eval_task_rejects_invalid_linked_source_filter_shape(
        self, auth_client, project, custom_eval_config
    ):
        """Direct source id filters must be scalar strings or string lists."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Invalid linked trace task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "filters": {"trace_id": {"id": str(uuid.uuid4())}},
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_eval_task_accepts_and_canonicalizes_json_object_filter(
        self, auth_client, project, custom_eval_config
    ):
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Structured attribute task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "filters": {
                    "span_attributes_filters": [
                        {
                            "column_id": "customer.context",
                            "filter_config": {
                                "col_type": "SPAN_ATTRIBUTE",
                                "filter_type": "json",
                                "filter_op": "contains",
                                "filter_value": {"tier": "vip", "attempt": 2},
                            },
                        }
                    ]
                },
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        task = EvalTask.objects.get(id=get_result(response)["id"])
        saved_items = [
            *task.filters.get("filters", []),
            *task.filters.get("span_attributes_filters", []),
        ]
        saved = next(
            item for item in saved_items if item["column_id"] == "customer.context"
        )
        assert saved["filter_config"]["filter_type"] == "map"
        assert saved["filter_config"]["filter_value"] == {
            "attempt": 2,
            "tier": "vip",
        }

    @pytest.mark.parametrize(
        "filter_value",
        [
            {"nested": {"tier": "vip"}},
            {"nested": ["vip"]},
            {"nullable": None},
            {f"key-{index}": index for index in range(33)},
        ],
    )
    def test_create_eval_task_rejects_invalid_map_before_clickhouse(
        self,
        auth_client,
        project,
        custom_eval_config,
        filter_value,
    ):
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Invalid structured attribute task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "filters": {
                    "span_attributes_filters": [
                        {
                            "column_id": "customer.context",
                            "filter_config": {
                                "col_type": "SPAN_ATTRIBUTE",
                                "filter_type": "map",
                                "filter_op": "contains",
                                "filter_value": filter_value,
                            },
                        }
                    ]
                },
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        rendered = response.content.decode()
        assert "DB::Exception" not in rendered
        assert "ClickHouse" not in rendered


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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

    def test_list_eval_tasks_rejects_legacy_query_aliases(self, auth_client, project):
        """List endpoint should expose only canonical query params."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks/",
            {
                "projectId": str(project.id),
                "sortParams": json.dumps(
                    [{"column_id": "created_at", "direction": "desc"}]
                ),
                "pageNumber": "1",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskListWithProjectNameAPI:
    """Tests for GET /tracer/eval-task/list_eval_tasks_with_project_name/ endpoint."""

    def test_list_with_project_name_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/"
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_with_project_name_success(self, auth_client, project, eval_task):
        """List eval tasks with project names."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {
                "page_number": "0",
                "page_size": "25",
                "sort_params": json.dumps(
                    [{"column_id": "created_at", "direction": "desc"}]
                ),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "metadata" in data or "table" in data

    def test_list_internal_error_is_sanitized(self, auth_client, project, monkeypatch):
        monkeypatch.setattr(EvalTaskView, "get_queryset", _raise_private_db_error)

        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {"page_number": "0", "page_size": "25"},
        )

        _assert_sanitized(
            response,
            "Evaluation tasks could not be loaded",
            status.HTTP_400_BAD_REQUEST,
        )

    def test_list_with_project_name_rejects_grid_param_drift(
        self, auth_client, project
    ):
        """The frontend must send the backend serializer's canonical query shape."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {
                "page": "1",
                "sort_by": "created_at",
                "sort_order": "desc",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["details"] == {
            "page": ["Unknown field."],
            "sort_by": ["Unknown field."],
            "sort_order": ["Unknown field."],
        }

    def test_list_with_project_name_rejects_legacy_filter_shape(
        self, auth_client, project
    ):
        """Filter query payload must use the canonical filter object."""
        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {
                "project_id": str(project.id),
                "filters": json.dumps(
                    [
                        {
                            "column_id": "name",
                            "filterConfig": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "Task",
                            },
                        }
                    ]
                ),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_with_project_name_excludes_other_workspace_tasks(
        self, auth_client, project, user, eval_task, custom_eval_config
    ):
        """The org-level tasks route should still respect project workspace scope."""
        other_task = make_other_workspace_eval_task(project, user, custom_eval_config)

        response = auth_client.get(
            "/tracer/eval-task/list_eval_tasks_with_project_name/",
            {"page_number": "0", "page_size": "100"},
        )

        assert response.status_code == status.HTTP_200_OK
        rows = get_result(response)["table"]
        ids = {row["id"] for row in rows}
        assert str(eval_task.id) in ids
        assert str(other_task.id) not in ids


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_logs_success(self, auth_client, eval_task):
        """Get logs for an eval task."""
        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(eval_task.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "errors_count" in data or "success_count" in data

    def test_get_logs_counts_targets_apart_from_eval_runs(
        self, auth_client, eval_task, observation_span, custom_eval_config
    ):
        """One span run through two evals is two eval runs over one span."""
        second_config = make_custom_eval_config_for_project(
            eval_task.project, custom_eval_config, "Second Eval"
        )
        for config in (custom_eval_config, second_config):
            EvalLogger.objects.create(
                eval_task_id=str(eval_task.id),
                observation_span=observation_span,
                trace=observation_span.trace,
                custom_eval_config=config,
            )

        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(eval_task.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["total_count"] == 2
        assert data["success_count"] == 2
        assert data["target_count"] == 1

    def test_get_logs_rejects_other_workspace_task(
        self, auth_client, project, user, custom_eval_config
    ):
        """Task logs should not resolve same-org tasks from another workspace."""
        other_task = make_other_workspace_eval_task(project, user, custom_eval_config)

        response = auth_client.get(
            "/tracer/eval-task/get_eval_task_logs/",
            {"eval_task_id": str(other_task.id)},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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

    def test_unpause_rejects_other_workspace_task(
        self, auth_client, project, user, custom_eval_config
    ):
        """The endpoint's scope, now shared with the AI tool through
        ``tracer.selectors.eval_tasks.scope``, still refuses a same-org task
        in another workspace."""
        other_task = make_other_workspace_eval_task(project, user, custom_eval_config)
        EvalTask.objects.filter(id=other_task.id).update(status=EvalTaskStatus.PAUSED)

        response = auth_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={other_task.id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_task.refresh_from_db()
        assert other_task.status == EvalTaskStatus.PAUSED

    def test_unpause_rejects_task_in_deleted_project(
        self, auth_client, project, eval_task
    ):
        eval_task.status = EvalTaskStatus.PAUSED
        eval_task.save()
        Project.all_objects.filter(id=project.id).update(deleted=True)

        response = auth_client.post(
            f"/tracer/eval-task/unpause_eval_task/?eval_task_id={eval_task.id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.PAUSED


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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

    def test_bulk_delete_cascades_soft_delete(
        self, auth_client, eval_task, trace, observation_span
    ):
        """Bulk delete soft-deletes each task's loggers and eval results."""
        task_logger = EvalTaskLogger.objects.create(
            eval_task=eval_task,
            status=EvalTaskStatus.PENDING,
        )
        eval_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            eval_task_id=str(eval_task.id),
        )

        response = auth_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(eval_task.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        eval_task.refresh_from_db()
        assert eval_task.status == EvalTaskStatus.DELETED
        assert eval_task.deleted is True

        task_logger = EvalTaskLogger.all_objects.get(id=task_logger.id)
        assert task_logger.deleted is True
        assert task_logger.deleted_at is not None

        eval_logger = EvalLogger.all_objects.get(id=eval_logger.id)
        assert eval_logger.deleted is True
        assert eval_logger.deleted_at is not None

    def test_bulk_delete_leaves_other_tasks_results(
        self, auth_client, project, eval_task, trace, observation_span
    ):
        """Bulk-deleting one task must not touch another task's eval results."""
        other_task = EvalTask.objects.create(
            project=project,
            name="Other Task",
            status=EvalTaskStatus.PENDING,
        )
        other_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            eval_task_id=str(other_task.id),
        )

        response = auth_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(eval_task.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        other_logger.refresh_from_db()
        assert other_logger.deleted is False

    def test_bulk_delete_rejects_running_tasks(self, auth_client, project):
        """Running tasks cannot be bulk-deleted; they must be paused first."""
        running_task = EvalTask.objects.create(
            project=project,
            name="Running Task",
            status=EvalTaskStatus.RUNNING,
        )

        response = auth_client.post(
            "/tracer/eval-task/mark_eval_tasks_deleted/",
            {"eval_task_ids": [str(running_task.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        running_task.refresh_from_db()
        assert running_task.status == EvalTaskStatus.RUNNING
        assert running_task.deleted is False


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskDestroyAPI:
    """Tests for DELETE /tracer/eval-task/{id}/ (single REST delete)."""

    def test_destroy_eval_task_unauthenticated(self, api_client, eval_task):
        """Unauthenticated requests should be rejected."""
        response = api_client.delete(f"/tracer/eval-task/{eval_task.id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_destroy_eval_task_cascades_soft_delete(
        self, auth_client, eval_task, trace, observation_span
    ):
        """DELETE on a single eval task soft-deletes its loggers and results."""
        task_logger = EvalTaskLogger.objects.create(
            eval_task=eval_task,
            status=EvalTaskStatus.PENDING,
        )
        eval_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            eval_task_id=str(eval_task.id),
        )

        response = auth_client.delete(f"/tracer/eval-task/{eval_task.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # The task itself is soft-deleted (filtered out of the default manager).
        assert not EvalTask.objects.filter(id=eval_task.id).exists()
        eval_task.refresh_from_db()
        assert eval_task.deleted is True
        assert eval_task.deleted_at is not None

        # Loggers and eval results cascade to soft-deleted (use all_objects
        # since the default manager hides deleted rows).
        task_logger = EvalTaskLogger.all_objects.get(id=task_logger.id)
        assert task_logger.deleted is True
        assert task_logger.deleted_at is not None

        eval_logger = EvalLogger.all_objects.get(id=eval_logger.id)
        assert eval_logger.deleted is True
        assert eval_logger.deleted_at is not None

    def test_destroy_eval_task_leaves_other_tasks_results(
        self, auth_client, project, eval_task, trace, observation_span
    ):
        """Deleting one task must not touch another task's eval results."""
        other_task = EvalTask.objects.create(
            project=project,
            name="Other Task",
            status=EvalTaskStatus.PENDING,
        )
        other_logger = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            eval_task_id=str(other_task.id),
        )

        response = auth_client.delete(f"/tracer/eval-task/{eval_task.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT

        other_logger.refresh_from_db()
        assert other_logger.deleted is False


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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

    def test_update_internal_error_is_sanitized(
        self, auth_client, eval_task, monkeypatch
    ):
        monkeypatch.setattr(
            EvalTaskView,
            "_scope_eval_task_queryset",
            _raise_private_db_error,
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "name": "Sanitized update",
                "edit_type": "fresh_run",
            },
            format="json",
        )

        _assert_sanitized(
            response,
            "Evaluation task could not be updated",
            status.HTTP_400_BAD_REQUEST,
        )

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

    def test_update_eval_task_rejects_other_project_eval_config(
        self, auth_client, project, eval_task, custom_eval_config
    ):
        """Task updates should not attach eval configs from another project."""
        other_project = Project.objects.create(
            name="Other Visible Eval Config Project For Update",
            organization=project.organization,
            workspace=project.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_config = make_custom_eval_config_for_project(
            other_project, custom_eval_config, "Other Project Eval Config Update"
        )

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "evals": [str(other_config.id)],
                "edit_type": "edit_rerun",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not eval_task.evals.filter(id=other_config.id).exists()

    def test_update_eval_task_rejects_empty_eval_list(self, auth_client, eval_task):
        """A task cannot be updated to have no eval configs."""
        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "evals": [],
                "edit_type": "edit_rerun",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_partial_update_rename_does_not_require_edit_type(
        self, auth_client, eval_task
    ):
        """Inline rename uses the detail PATCH route and should not rerun the task."""
        eval_task.status = EvalTaskStatus.COMPLETED
        eval_task.save(update_fields=["status"])

        response = auth_client.patch(
            f"/tracer/eval-task/{eval_task.id}/",
            {"name": "Renamed Inline Task"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        eval_task.refresh_from_db()
        assert eval_task.name == "Renamed Inline Task"
        assert eval_task.status == EvalTaskStatus.COMPLETED


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalTaskDetailPatchRecoveryEligibility:
    """Generic PATCH preserves NULL activation; writable status is not acceptance."""

    @pytest.fixture
    def workflow_calls(self, monkeypatch):
        calls = {"described": [], "started": []}

        def describe(task_id):
            calls["described"].append(task_id)
            return workflow_client.WF_ABSENT

        def start(task, **kwargs):
            calls["started"].append((task.pk, kwargs))

        monkeypatch.setattr(
            workflow_client, "describe_eval_task_workflow_sync", describe
        )
        monkeypatch.setattr(workflow_client, "start_eval_task_workflow_sync", start)
        monkeypatch.setattr(eval_task_views, "start_eval_task_workflow_sync", start)
        return calls

    def _add_stale_backlog(self, task, custom_eval_config, trace):
        # PG-only, fixture-owned data: no CH seeding or global cleanup needed.
        span = ObservationSpan.objects.create(
            id=f"detail-patch-{uuid.uuid4().hex}",
            project=task.project,
            trace=trace,
            name="synthetic PATCH backlog",
            observation_type="llm",
        )
        entry = EvalLogger.objects.create(
            observation_span=span,
            trace=trace,
            custom_eval_config=custom_eval_config,
            eval_task_id=str(task.pk),
            target_type=EvalTargetType.SPAN,
            status=EvalEntryStatus.RUNNING,
        )
        EvalLogger.all_objects.filter(pk=entry.pk).update(
            updated_at=task.created_at - timedelta(days=2)
        )

    def _snapshot(self, task):
        return (
            EvalTask.all_objects.using("default").filter(pk=task.pk).values().get(),
            list(
                EvalLogger.all_objects.using("default")
                .filter(eval_task_id=str(task.pk))
                .order_by("pk")
                .values()
            ),
            list(task.evals.using("default").order_by("pk").values_list("pk", flat=True)),
        )

    def _assert_unstamped_and_unrecovered(self, task, expected, workflow_calls):
        assert self._snapshot(task) == expected
        task.refresh_from_db()
        assert task.start_time is None
        # Check the exact row as well as the real capped backlog/idle selector;
        # other tests' rows must not be deleted or hidden to make this pass.
        assert not (
            EvalTask.no_workspace_objects.using("default")
            .filter(automatic_recovery_eligibility(), pk=task.pk)
            .exists()
        )
        assert task.pk not in {
            candidate.pk for candidate in find_stranded_tasks(limit=1000)
        }
        outcome = recover_task(task, stale_running_seconds=7200)
        assert outcome["skipped"] and not outcome["restarted"], outcome
        assert (outcome["requeued"], outcome["failed"]) == (0, 0)
        assert workflow_calls == {"described": [], "started": []}
        assert self._snapshot(task) == expected

    @pytest.mark.parametrize("stored_last_run", ["before_creation", "at_creation"])
    @pytest.mark.parametrize("incoming_start_time", ["past", "future"])
    @pytest.mark.parametrize(
        "requested_status",
        [EvalTaskStatus.RUNNING, EvalTaskStatus.PAUSED, EvalTaskStatus.PENDING],
    )
    @pytest.mark.parametrize("incoming_last_run", ["past", "future"])
    @pytest.mark.parametrize("with_backlog", [False, True], ids=["idle", "backlog"])
    def test_detail_patch_cannot_activate_a_new_unstamped_continuous_task(
        self,
        auth_client,
        eval_task,
        custom_eval_config,
        trace,
        monkeypatch,
        workflow_calls,
        stored_last_run,
        incoming_start_time,
        requested_status,
        incoming_last_run,
        with_backlog,
    ):
        created = eval_task.created_at
        initial_last_run = (
            created - timedelta(seconds=1)
            if stored_last_run == "before_creation"
            else created
        )
        EvalTask.all_objects.filter(pk=eval_task.pk).update(last_run=initial_last_run)
        if with_backlog:
            self._add_stale_backlog(eval_task, custom_eval_config, trace)
        expected = self._snapshot(eval_task)
        assert (
            expected[0]["run_type"],
            expected[0]["status"],
            expected[0]["start_time"],
            expected[0]["continuous_cursor"],
        ) == (RunType.CONTINUOUS, EvalTaskStatus.PENDING, None, None)
        assert expected[0]["last_run"] <= created
        now = created + timedelta(seconds=10)
        monkeypatch.setattr(timezone, "now", lambda: now)
        supplied_start = created + timedelta(
            days=-1 if incoming_start_time == "past" else 1
        )
        supplied_last_run = created + timedelta(
            days=-1 if incoming_last_run == "past" else 1
        )

        response = auth_client.patch(
            f"/tracer/eval-task/{eval_task.pk}/",
            {
                "start_time": supplied_start.isoformat(),
                "status": requested_status,
                "last_run": supplied_last_run.isoformat(),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        # Pin the landed writable fields, not new API ownership rules: a past
        # last_run persists; a future one is clamped to the locked stored value.
        # Neither it nor a client-written RUNNING status proves activation.
        expected[0].update(
            status=requested_status,
            last_run=(
                supplied_last_run if incoming_last_run == "past" else initial_last_run
            ),
            updated_at=now,
        )
        self._assert_unstamped_and_unrecovered(eval_task, expected, workflow_calls)

    @pytest.mark.parametrize("with_backlog", [False, True], ids=["idle", "backlog"])
    def test_detail_metadata_patch_leaves_legacy_running_task_unstamped(
        self,
        auth_client,
        eval_task,
        custom_eval_config,
        trace,
        monkeypatch,
        workflow_calls,
        with_backlog,
    ):
        now = timezone.now()
        old = now - timedelta(days=30)
        EvalTask.all_objects.filter(pk=eval_task.pk).update(
            status=EvalTaskStatus.RUNNING,
            created_at=old,
            last_run=old,
            start_time=None,
            continuous_cursor=None,
        )
        if with_backlog:
            self._add_stale_backlog(eval_task, custom_eval_config, trace)
        expected = self._snapshot(eval_task)
        monkeypatch.setattr(timezone, "now", lambda: now)

        response = auth_client.patch(
            f"/tracer/eval-task/{eval_task.pk}/",
            {"name": "renamed unstamped legacy task"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        expected[0].update(name="renamed unstamped legacy task", updated_at=now)
        self._assert_unstamped_and_unrecovered(eval_task, expected, workflow_calls)


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
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_details_ignores_extra_query_parameters(
        self, auth_client, eval_task, custom_eval_config
    ):
        """Cache-busting parameters must not turn a valid read into a 400."""
        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(eval_task.id), "_": "1731489000000", "v": "2"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert get_result(response)["name"] == eval_task.name

    def test_get_details_still_rejects_a_non_uuid_eval_id(self, auth_client):
        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/", {"eval_id": "not-a-uuid"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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

        from tracer.serializers.eval_task import EvalTaskDetailsResponseSerializer

        contract = EvalTaskDetailsResponseSerializer(data=response.json())
        assert contract.is_valid(), contract.errors
        assert "sampling_policy" in data
        assert "count" not in response.json() and "results" not in response.json()

    def test_get_details_internal_error_is_sanitized(
        self, auth_client, eval_task, monkeypatch
    ):
        monkeypatch.setattr(
            EvalTaskView,
            "_scope_eval_task_queryset",
            _raise_private_db_error,
        )

        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(eval_task.id)},
        )

        _assert_sanitized(
            response,
            "Evaluation task details could not be loaded",
            status.HTTP_400_BAD_REQUEST,
        )

    def test_usage_internal_error_is_sanitized(
        self, auth_client, eval_task, monkeypatch
    ):
        monkeypatch.setattr(
            EvalTaskView,
            "_scope_eval_task_queryset",
            _raise_private_db_error,
        )

        response = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {
                "eval_task_id": str(eval_task.id),
                "page": 1,
                "page_size": 25,
                "period": "30d",
            },
        )

        _assert_sanitized(
            response,
            "Evaluation task usage could not be loaded",
            status.HTTP_400_BAD_REQUEST,
        )

    def test_usage_internal_value_error_is_sanitized(
        self, auth_client, eval_task, monkeypatch
    ):
        def raise_internal_value_error(*_args, **_kwargs):
            raise ValueError(_PRIVATE_DB_ERROR)

        monkeypatch.setattr(
            eval_task_views,
            "_compute_eval_aggregation",
            raise_internal_value_error,
        )

        response = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {
                "eval_task_id": str(eval_task.id),
                "page": 1,
                "page_size": 25,
                "eval_aggregation": "true",
            },
        )

        _assert_sanitized(
            response,
            "Evaluation task usage could not be loaded",
            status.HTTP_400_BAD_REQUEST,
        )

    def test_usage_invalid_aggregation_date_is_bad_request(
        self, auth_client, eval_task
    ):
        response = auth_client.get(
            "/tracer/eval-task/get_usage/",
            {
                "eval_task_id": str(eval_task.id),
                "page": 1,
                "page_size": 25,
                "eval_aggregation": "true",
                "start_date": "not-an-iso-date",
            },
        )

        _assert_sanitized(
            response,
            "start_date",
            status.HTTP_400_BAD_REQUEST,
        )

    def test_get_details_missing_task_returns_not_found(self, auth_client):
        """A missing eval task is a 404, not a retriable bad request."""
        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(uuid.uuid4())},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert get_result(response) == "Eval task not found"

    def test_get_details_rejects_other_workspace_task(
        self, auth_client, project, user, custom_eval_config
    ):
        """Task detail should not resolve same-org tasks from another workspace."""
        other_task = make_other_workspace_eval_task(project, user, custom_eval_config)

        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(other_task.id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
class TestEvalTaskRowTypePersistence:
    """`row_type` round-trips through create / get / update (PR2).

    The FE sends one of `spans` / `traces` / `sessions` / `voiceCalls`; the
    backend persists it on EvalTask and surfaces it on every read so the
    UI's row-type tab survives an edit. Runtime semantics still spans-only
    until PR4.
    """

    @pytest.mark.parametrize("row_type", ["spans", "traces", "sessions", "voiceCalls"])
    def test_create_task_persists_row_type(
        self, auth_client, project, custom_eval_config, row_type
    ):
        """row_type round-trips through POST -> DB -> GET."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": f"Test {row_type} task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "row_type": row_type,
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        task_id = get_result(response)["id"]

        from tracer.models.eval_task import EvalTask

        task = EvalTask.objects.get(id=task_id)
        assert task.row_type == row_type

    def test_create_task_default_row_type_is_spans(
        self, auth_client, project, custom_eval_config
    ):
        """Omitting row_type defaults to 'spans' for back-compat."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": "Default row_type task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "evals": [str(custom_eval_config.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        task_id = get_result(response)["id"]

        from tracer.models.eval_task import EvalTask

        task = EvalTask.objects.get(id=task_id)
        assert task.row_type == "spans"

    def test_get_eval_details_returns_row_type(
        self, auth_client, project, custom_eval_config
    ):
        """get_eval_details surfaces row_type so edit-mode hydration finds it."""
        from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType

        task = EvalTask.objects.create(
            project=project,
            name="Trace task",
            filters={},
            sampling_rate=100,
            run_type=RunType.CONTINUOUS,
            status=EvalTaskStatus.PENDING,
            spans_limit=100,
            row_type="traces",
        )
        task.evals.add(custom_eval_config)

        response = auth_client.get(
            "/tracer/eval-task/get_eval_details/",
            {"eval_id": str(task.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["row_type"] == "traces"

    def test_update_eval_task_rejects_row_type_change(self, auth_client, eval_task):
        """row_type is immutable after task creation.

        Pins the API contract: clients can't change row_type on an
        existing task. The dispatcher / target_type wiring / dedup
        index all depend on row_type being stable for the task's
        lifetime, so the endpoint rejects any explicit row_type in
        an update request (matching or not).
        """
        original_row_type = eval_task.row_type
        assert original_row_type == "spans"

        response = auth_client.patch(
            "/tracer/eval-task/update_eval_task/",
            {
                "eval_task_id": str(eval_task.id),
                "row_type": "sessions",
                "edit_type": "fresh_run",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        eval_task.refresh_from_db()
        assert eval_task.row_type == original_row_type


@pytest.mark.integration
@pytest.mark.api
class TestCompositeEvalAcrossRowTypes:
    """Composite eval templates are now valid on every row_type (TH-5158).

    Earlier the runtime raised ``NotImplementedError`` for composite + non-span
    row_type; the API layer never blocked it, so these tests pin the new
    behaviour: composite + traces / sessions creates a task cleanly.
    """

    @pytest.fixture
    def composite_custom_eval_config(self, db, project, organization, workspace):
        from model_hub.models.evals_metric import (
            CompositeEvalChild,
            EvalTemplate,
        )
        from tracer.models.custom_eval_config import CustomEvalConfig

        parent = EvalTemplate.objects.create(
            name="Composite (api test)",
            description="composite parent",
            organization=organization,
            workspace=workspace,
            template_type="composite",
            aggregation_enabled=True,
            aggregation_function="weighted_avg",
            pass_threshold=0.5,
            config={"type": "composite"},
        )
        child = EvalTemplate.objects.create(
            name="Child (api test)",
            description="composite child",
            organization=organization,
            workspace=workspace,
            template_type="single",
            config={"type": "pass_fail", "criteria": "ok"},
            pass_threshold=0.5,
        )
        CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, weight=1.0
        )
        return CustomEvalConfig.objects.create(
            name="Composite custom config",
            project=project,
            eval_template=parent,
            config={"threshold": 0.5},
            mapping={"input": "input", "output": "output"},
            filters={},
        )

    @pytest.mark.parametrize("row_type", ["traces", "sessions"])
    def test_composite_template_now_allowed_for_row_type(
        self, auth_client, project, composite_custom_eval_config, row_type
    ):
        """Creating a composite-eval task with row_type=traces|sessions succeeds."""
        response = auth_client.post(
            "/tracer/eval-task/",
            {
                "project": str(project.id),
                "name": f"composite {row_type} task",
                "run_type": "continuous",
                "sampling_rate": 100,
                "row_type": row_type,
                "evals": [str(composite_custom_eval_config.id)],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        task_id = get_result(response)["id"]
        task = EvalTask.objects.get(id=task_id)
        assert task.row_type == row_type
        assert task.evals.filter(id=composite_custom_eval_config.id).exists()
