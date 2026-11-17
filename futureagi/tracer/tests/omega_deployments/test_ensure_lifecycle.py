"""The Observe link over a deployment's life: reused names, deleted monitors and projects,
refused input, and a failure part way through.
"""

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.services.omega_deployments.ensure import monitors as monitors_module
from tracer.services.project_deletion import soft_delete_projects
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NAME = "billing-bot"
PROJECT = "deploy/billing-bot"


def _ensure(client, deployment_id, name=NAME):
    return client.post(ensure_url(deployment_id), {"name": name}, format="json")


def _live_monitors(project_id):
    return UserAlertMonitor.no_workspace_objects.filter(project_id=project_id)


def test_a_deployment_recreated_under_its_name_shares_the_project_and_monitors(
    auth_client,
):
    first = _ensure(auth_client, new_deployment_id()).data["result"]

    second = _ensure(auth_client, new_deployment_id())

    assert second.status_code == 200, second.data
    assert second.data["result"]["projectId"] == first["projectId"]
    assert [a["id"] for a in second.data["result"]["alerts"]] == [
        a["id"] for a in first["alerts"]
    ]
    assert _live_monitors(first["projectId"]).count() == 2
    assert OmegaDeploymentObserveLink.no_workspace_objects.count() == 2


def test_a_monitor_the_user_deleted_or_muted_stays_that_way(auth_client):
    deployment_id = new_deployment_id()
    first = _ensure(auth_client, deployment_id).data["result"]
    errors_id, tokens_id = (alert["id"] for alert in first["alerts"])
    # What Observe -> Alerts does: delete is a soft delete, mute a flag.
    UserAlertMonitor.objects.filter(id=errors_id).update(deleted=True)
    UserAlertMonitor.objects.filter(id=tokens_id).update(is_mute=True)

    again = _ensure(auth_client, deployment_id)

    assert again.status_code == 200
    alerts = again.data["result"]["alerts"]
    assert [(a["id"], a["kind"], a["isMuted"]) for a in alerts] == [
        (tokens_id, "daily_tokens", True)
    ]
    assert _live_monitors(first["projectId"]).count() == 1
    assert (
        UserAlertMonitor.all_objects.filter(project_id=first["projectId"]).count() == 2
    )


def test_a_hard_deleted_monitor_is_not_recreated_either(auth_client):
    deployment_id = new_deployment_id()
    first = _ensure(auth_client, deployment_id).data["result"]
    # A queryset delete removes the row (only BaseModel.delete() on an instance
    # is soft), the way an admin or a cascade would.
    UserAlertMonitor.all_objects.filter(id=first["alerts"][0]["id"]).delete()

    again = _ensure(auth_client, deployment_id).data["result"]

    assert [a["kind"] for a in again["alerts"]] == ["daily_tokens"]
    link = OmegaDeploymentObserveLink.no_workspace_objects.get()
    assert link.errors_monitor_id is None
    assert (
        UserAlertMonitor.all_objects.filter(project_id=first["projectId"]).count() == 1
    )


def test_a_deleted_project_is_made_again_with_fresh_monitors(auth_client, workspace):
    deployment_id = new_deployment_id()
    first = _ensure(auth_client, deployment_id).data["result"]
    soft_delete_projects(
        Project.no_workspace_objects.filter(id=first["projectId"]), "observe"
    )

    again = _ensure(auth_client, deployment_id)

    assert again.status_code == 200, again.data
    result = again.data["result"]
    assert result["projectId"] != first["projectId"]
    assert result["projectName"] == PROJECT
    assert len(result["alerts"]) == 2
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in first["alerts"]}
    )
    link = OmegaDeploymentObserveLink.no_workspace_objects.get()
    assert str(link.project_id) == result["projectId"]
    assert _live_monitors(result["projectId"]).count() == 2


def test_a_new_name_for_the_same_deployment_moves_the_link_with_fresh_monitors(
    auth_client, workspace
):
    deployment_id = new_deployment_id()
    before = _ensure(auth_client, deployment_id).data["result"]

    after = _ensure(auth_client, deployment_id, name="billing-bot-two")

    assert after.status_code == 200, after.data
    result = after.data["result"]
    assert result["projectName"] == "deploy/billing-bot-two"
    assert result["projectId"] != before["projectId"]
    assert len(result["alerts"]) == 2
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in before["alerts"]}
    )
    link = OmegaDeploymentObserveLink.no_workspace_objects.get()
    assert (str(link.project_id), link.deployment_name) == (
        result["projectId"],
        "billing-bot-two",
    )
    # The old project and its pair stay as they were: traces already there keep their alerts.
    assert Project.no_workspace_objects.filter(id=before["projectId"]).exists()
    assert sorted(
        str(pk)
        for pk in _live_monitors(before["projectId"]).values_list("id", flat=True)
    ) == sorted(a["id"] for a in before["alerts"])
    assert _live_monitors(result["projectId"]).count() == 2


def test_a_user_monitor_with_a_default_name_is_left_alone(
    auth_client, user, workspace, organization
):
    project = get_or_create_project(
        project_name=PROJECT,
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    own = UserAlertMonitor.no_workspace_objects.create(
        name="Errors spike (production)",
        metric_type="count_of_errors",
        organization=organization,
        workspace=workspace,
        project=project,
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=3,
    )
    deployment_id = new_deployment_id()

    response = _ensure(auth_client, deployment_id)

    assert response.status_code == 200, response.data
    errors = response.data["result"]["alerts"][0]
    assert errors["id"] != str(own.id)
    assert errors["name"] == f"Errors spike (production) [{deployment_id}]"
    own.refresh_from_db()
    assert (own.critical_threshold_value, own.filters) == (3, {})
    assert _live_monitors(project.id).count() == 3


def test_a_failure_part_way_returns_500_and_leaves_nothing_behind(
    auth_client, workspace, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("monitor store down")

    monkeypatch.setattr(monitors_module, "_create_monitor", fail)

    response = _ensure(auth_client, new_deployment_id())

    assert response.status_code == 500
    assert response.data["message"] == get_error_message(
        "OMEGA_DEPLOYMENT_OBSERVE_FAILED"
    )
    assert "monitor store down" not in str(response.data)
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
    assert not Project.no_workspace_objects.filter(
        workspace=workspace, name=PROJECT
    ).exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": None},
        {"name": "Billing-Bot"},
        {"name": "1billing"},
        {"name": "billing/bot"},
        {"name": "billing bot"},
        {"name": "b" * 41},
    ],
)
def test_a_name_ee_would_refuse_is_400(auth_client, body):
    response = auth_client.post(ensure_url(new_deployment_id()), body, format="json")

    assert response.status_code == 400
    assert get_error_message("OMEGA_DEPLOYMENT_OBSERVE_INVALID_NAME") in str(
        response.data
    )
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()


def test_the_longest_name_ee_allows_is_linked(auth_client):
    name = "b" * 40

    response = _ensure(auth_client, new_deployment_id(), name=name)

    assert response.status_code == 200, response.data
    assert response.data["result"]["projectName"] == f"deploy/{name}"


def test_an_unknown_body_field_is_400(auth_client):
    response = auth_client.post(
        ensure_url(new_deployment_id()),
        {"name": NAME, "project_id": "anything"},
        format="json",
    )

    assert response.status_code == 400
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()


@pytest.mark.parametrize(
    "deployment_id", ["-dep", ".dep", "dep%20one", "dep%3Cscript%3E", "d" * 256]
)
def test_a_malformed_deployment_id_is_400(auth_client, deployment_id):
    response = _ensure(auth_client, deployment_id)

    assert response.status_code == 400
    assert response.data["message"] == get_error_message(
        "OMEGA_DEPLOYMENT_OBSERVE_INVALID_ID"
    )
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
