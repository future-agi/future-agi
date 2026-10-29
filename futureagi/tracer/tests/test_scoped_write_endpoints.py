"""Writable relations retain same-workspace API controls and refuse other scopes."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import serializers

from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask
from tracer.models.monitor import UserAlertMonitor
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace_session import TraceSession
from tracer.serializers.trace import TraceSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def scopes(settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    a, b = make_tenant("A"), make_tenant("B")
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        organization=a.organization, name="Second", is_default=False,
        is_active=True, created_by=a.user,
    )
    return a, SimpleNamespace(organization=a.organization, workspace=workspace), b


def resources(scope):
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        organization=scope.organization, workspace=scope.workspace,
        name=f"Project {uuid.uuid4()}", trace_type="observe", model_type="GenerativeLLM",
    )
    template = EvalTemplate.no_workspace_objects.create(
        organization=scope.organization, workspace=scope.workspace,
        name=f"template-{uuid.uuid4()}", owner=OwnerChoices.USER.value,
        config={"required_keys": []},
    )
    config = CustomEvalConfig.no_workspace_objects.create(
        project=project, eval_template=template, name="Config", config={}, mapping={},
    )
    return project, config


def task_payload(project, config):
    return dict(project=str(project.pk), name="Requested task", run_type="continuous",
                sampling_rate=100, evals=[str(config.pk)])


def task_write(client, method, payload, task):
    clear_workspace_context()
    url = "/tracer/eval-task/" if method == "post" else f"/tracer/eval-task/{task.pk}/"
    return getattr(client, method)(url, payload, format="json")


@pytest.mark.parametrize("method", ["post", "put"])
def test_eval_task_same_workspace_control(scopes, method):
    a = scopes[0]
    project, config = resources(a)
    task = EvalTask.no_workspace_objects.create(project=project, name="Original task")
    with patch("tracer.views.eval_task.start_eval_task_workflow_sync") as start:
        response = task_write(a.client, method, task_payload(project, config), task)
    assert response.status_code == 200, response.content
    saved = EvalTask.no_workspace_objects.get(project=project, name="Requested task")
    assert list(saved.evals.values_list("pk", flat=True)) == [config.pk]
    assert start.call_count == (1 if method == "post" else 0)


@pytest.mark.parametrize("method", ["post", "put"])
@pytest.mark.parametrize("other_index", [1, 2], ids=["workspace", "organization"])
@pytest.mark.parametrize("field", ["project", "evals"])
def test_eval_task_refuses_other_scope(scopes, method, other_index, field):
    a = scopes[0]
    project, config = resources(a)
    other_project, other_config = resources(scopes[other_index])
    task = EvalTask.no_workspace_objects.create(project=project, name="Original task")
    task.evals.set([config])
    payload = task_payload(project, config)
    payload[field] = str(other_project.pk) if field == "project" else [str(other_config.pk)]
    before = EvalTask.no_workspace_objects.count()
    with patch("tracer.views.eval_task.start_eval_task_workflow_sync") as start:
        response = task_write(a.client, method, payload, task)
    assert response.status_code in (400, 403, 404), response.content
    start.assert_not_called()
    task.refresh_from_db()
    assert task.project_id == project.pk and task.name == "Original task"
    assert list(task.evals.values_list("pk", flat=True)) == [config.pk]
    assert EvalTask.no_workspace_objects.count() == before


@pytest.mark.parametrize("scope_index", [0, 1, 2], ids=["own", "workspace", "organization"])
def test_metric_alert_resolves_project_in_request_scope(scopes, scope_index):
    a = scopes[0]
    project, _ = resources(scopes[scope_index])
    before = UserAlertMonitor.no_workspace_objects.count()
    clear_workspace_context()
    with patch("tracer.views.alerts.check_ee_can_create"):
        response = a.client.post("/tracer/alerts/", {
            "kind": "metric", "name": "Latency threshold", "project": str(project.pk),
            "metric_type": "span_response_time", "threshold_type": "static",
            "threshold_operator": "greater_than", "critical_threshold_value": 1,
            "notification_emails": ["alerts@example.com"],
        }, format="json")
    if scope_index == 0:
        assert response.status_code == 201, response.content
        saved = UserAlertMonitor.no_workspace_objects.get(pk=response.json()["result"]["id"])
        assert saved.project_id == project.pk
        assert saved.organization_id == a.organization.pk
        assert saved.workspace_id == a.workspace.pk
    else:
        assert response.status_code in (400, 403, 404), response.content
        assert UserAlertMonitor.no_workspace_objects.count() == before


@pytest.mark.parametrize("field,model", [("project_version", ProjectVersion), ("session", TraceSession)])
def test_trace_relations_refuse_deleted_parent(scopes, field, model):
    a = scopes[0]
    project, _ = resources(a)
    related = model.no_workspace_objects.create(project=project, name="Related")
    request = SimpleNamespace(organization=a.organization, workspace=a.workspace, user=a.user)
    serializer = TraceSerializer(context={"request": request})
    assert serializer.fields[field].run_validation(str(related.pk)) == related
    Project.no_workspace_objects.filter(pk=project.pk).update(deleted=True)
    serializer = TraceSerializer(context={"request": request})
    with pytest.raises(serializers.ValidationError):
        serializer.fields[field].run_validation(str(related.pk))
