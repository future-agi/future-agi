"""Config-list consumers must agree on request scope and canonical query fields.

These exercise the real picker/task/config API queries, not the earlier caller
inventory's assumption that every caller already uses the canonical contract.
Authentication is supplied by the existing workspace-aware test client.
"""

import uuid
from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask
from tracer.models.project import Project
from tracer.views.custom_eval_config import CustomEvalConfigView

pytestmark = pytest.mark.django_db
LIST_URL = "/tracer/custom-eval-config/list_custom_eval_configs/"


@pytest.fixture
def caller_rows(observe_project, user, eval_template):
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="Config caller sibling",
        organization=observe_project.organization,
        created_by=user,
        is_active=True,
    )
    foreign_org = Organization.objects.create(name="Config caller foreign org")
    projects = {"default": observe_project}
    for label, workspace, organization in (
        ("sibling", sibling_workspace, observe_project.organization),
        ("legacy", None, observe_project.organization),
        ("foreign", None, foreign_org),
    ):
        projects[label] = Project.no_workspace_objects.create(
            name=f"Config caller {label}",
            workspace=workspace,
            organization=organization,
            model_type=observe_project.model_type,
            trace_type="observe",
        )
    rows = {}
    for label, project in projects.items():
        config = CustomEvalConfig.objects.create(
            name=f"caller-{label}-{uuid.uuid4().hex}",
            project=project,
            eval_template=eval_template,
            config={},
            mapping={},
        )
        task = EvalTask.no_workspace_objects.create(
            project=project,
            name=f"Config caller {label} task",
            filters={},
            status="completed",
            run_type="historical",
        )
        task.evals.add(config)
        rows[label] = SimpleNamespace(project=project, config=config, task=task)
    return rows


def _result(client, url, params=None):
    response = client.get(url, params or {})
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _config_ids(client, **params):
    return {row["id"] for row in _result(client, LIST_URL, params)}


@pytest.mark.parametrize("workspace_label", ["default", "sibling"])
@pytest.mark.parametrize("fresh", [False, True])
def test_picker_and_both_eval_pages_supply_config_ids_in_request_workspace(
    auth_client, caller_rows, workspace_label, fresh
):
    """New uses the project picker; Edit uses project/global task-list rows."""
    auth_client.set_workspace(caller_rows[workspace_label].project.workspace)
    visible = {"default", "legacy"} if workspace_label == "default" else {"sibling"}
    expected = [caller_rows[label] for label in visible]
    picker = _result(
        auth_client,
        "/tracer/project/list_project_ids/",
        {"project_type": "observe"},
    )
    assert {row["id"] for row in picker["projects"]} == {
        str(row.project.pk) for row in expected
    }
    global_tasks = _result(
        auth_client, "/tracer/eval-task/list_eval_tasks_with_project_name/"
    )
    assert {row["id"] for row in global_tasks["table"]} == {
        str(row.task.pk) for row in expected
    }
    freshness = {"fresh": "true"} if fresh else {}
    assert _config_ids(auth_client, **freshness) == {
        str(row.config.pk) for row in expected
    }
    for row in expected:
        project_id, task_id = str(row.project.pk), str(row.task.pk)
        tasks = _result(
            auth_client,
            "/tracer/eval-task/list_eval_tasks/",
            {"project_id": project_id},
        )
        assert {task["id"] for task in tasks["table"]} == {task_id}
        # New/Instant Eval and Edit respectively. Neither sends row filters.
        assert _config_ids(auth_client, project_id=project_id, **freshness) == {
            str(row.config.pk)
        }
        assert _config_ids(
            auth_client, project_id=project_id, task_id=task_id, **freshness
        ) == {str(row.config.pk)}

    # URLs and stale tabs can still supply out-of-scope IDs. The server must
    # enforce scope independently of the picker, including task-only requests.
    for label in caller_rows.keys() - visible:
        row = caller_rows[label]
        for params in (
            {"project_id": str(row.project.pk)},
            {"task_id": str(row.task.pk)},
            {"project_id": str(row.project.pk), "task_id": str(row.task.pk)},
        ):
            assert _config_ids(auth_client, **params, **freshness) == set()


@pytest.mark.parametrize("field", ["filters", "filters[project_id]"])
def test_drawer_legacy_filters_are_not_part_of_the_config_list_contract(
    auth_client, observe_project, field
):
    """Both JSON edit filters and Axios-serialized New filters are rejected."""
    response = auth_client.get(
        LIST_URL, {"project_id": str(observe_project.pk), field: "{}"}
    )
    assert response.status_code == 400, response.content
    assert field in response.json()["details"]


@pytest.mark.parametrize("fresh", [False, True])
def test_non_workspace_caller_keeps_request_organization_scope(
    caller_rows, user, fresh
):
    """Post-auth API-key semantics: no workspace means org scope, not default.

    This deliberately bypasses authentication; it pins the action's handling of
    the resolved request organization, including when it differs from the user.
    """
    query = {"fresh": "true"} if fresh else {}
    request = SimpleNamespace(
        user=user,
        organization=caller_rows["foreign"].project.organization,
        workspace=None,
        query_params=query,
    )
    view = CustomEvalConfigView()
    view.request = request
    view.format_kwarg = None
    response = view.list_custom_eval_configs(request)
    assert response.status_code == 200, response.data
    assert {str(row["id"]) for row in response.data["result"]} == {
        str(caller_rows["foreign"].config.pk)
    }

    request.organization = caller_rows["default"].project.organization
    response = view.list_custom_eval_configs(request)
    assert response.status_code == 200, response.data
    assert {str(row["id"]) for row in response.data["result"]} == {
        str(caller_rows[label].config.pk) for label in ("default", "sibling", "legacy")
    }
