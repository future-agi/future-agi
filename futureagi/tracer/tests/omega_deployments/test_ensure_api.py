"""POST /tracer/omega/deployments/<id>/observe/: one project, one link, two monitors, two tenants.

W1-FA-OBSERVE-LINK acceptance: two ensure calls give one project and two monitors; another
workspace gets 404; the 409 case; messages come from error_codes.py.
"""

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.services.omega_deployments.ensure.defaults import (
    production_without_checks_filters,
)
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NAME = "support-agent"
PROJECT = "deploy/support-agent"


def _ensure(client, deployment_id, name=NAME):
    return client.post(ensure_url(deployment_id), {"name": name}, format="json")


def _projects(workspace, name=PROJECT, trace_type="observe"):
    return Project.no_workspace_objects.filter(
        workspace=workspace, name=name, trace_type=trace_type
    )


def _monitors(project_id):
    return UserAlertMonitor.no_workspace_objects.filter(project_id=project_id)


def test_first_ensure_creates_project_link_and_both_default_monitors(
    auth_client, user, workspace, organization
):
    deployment_id = new_deployment_id()

    response = _ensure(auth_client, deployment_id)

    assert response.status_code == 200, response.data
    assert response.data["status"] is True
    result = response.data["result"]
    project = _projects(workspace).get()
    assert result["projectId"] == str(project.id)
    assert result["projectName"] == PROJECT
    assert project.organization_id == organization.id

    link = OmegaDeploymentObserveLink.no_workspace_objects.get(
        ee_deployment_id=deployment_id
    )
    assert (link.workspace_id, link.organization_id) == (workspace.id, organization.id)
    assert (link.project_id, link.deployment_name) == (project.id, NAME)
    assert link.created_by_id == user.id
    assert link.alerts_created_at is not None

    assert [alert["kind"] for alert in result["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    errors, tokens = (
        UserAlertMonitor.no_workspace_objects.get(id=alert["id"])
        for alert in result["alerts"]
    )
    assert (link.errors_monitor_id, link.daily_tokens_monitor_id) == (
        errors.id,
        tokens.id,
    )
    assert errors.metric_type == "count_of_errors"
    assert errors.threshold_type == "percentage_change"
    assert errors.threshold_operator == "greater_than"
    assert (errors.critical_threshold_value, errors.warning_threshold_value) == (
        200.0,
        100.0,
    )
    assert errors.alert_frequency == 15
    assert tokens.metric_type == "daily_tokens_spent"
    assert tokens.threshold_type == "static"
    assert tokens.critical_threshold_value == 1_000_000.0
    assert tokens.warning_threshold_value is None
    for monitor in (errors, tokens):
        assert monitor.project_id == project.id
        assert monitor.workspace_id == workspace.id
        assert monitor.organization_id == organization.id
        assert monitor.created_by_id == user.id
        assert monitor.notification_emails == [user.email]
        assert monitor.filters == production_without_checks_filters()
        assert monitor.is_mute is False
    rows = {
        row["column_id"]: row["filter_config"]
        for row in errors.filters["span_attributes_filters"]
    }
    assert rows["omega.deployment.environment"]["filter_op"] == "equals"
    assert rows["omega.deployment.environment"]["filter_value"] == "production"
    assert rows["omega.run.source"]["filter_op"] == "not_equals"
    assert rows["omega.run.source"]["filter_value"] == "check"
    assert result["alerts"][0]["alertFrequencyMinutes"] == 15
    assert result["alerts"][1]["criticalThreshold"] == 1_000_000.0
    assert result["alertsDeferred"] is None


def test_second_ensure_returns_the_same_project_and_monitors(auth_client, workspace):
    deployment_id = new_deployment_id()

    first = _ensure(auth_client, deployment_id)
    second = _ensure(auth_client, deployment_id)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.data["result"] == first.data["result"]
    project = _projects(workspace).get()
    assert _monitors(project.id).count() == 2
    assert OmegaDeploymentObserveLink.no_workspace_objects.count() == 1


def test_a_project_ingest_created_first_is_the_one_linked(
    auth_client, user, workspace, organization
):
    ingested = get_or_create_project(
        project_name=PROJECT,
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )

    response = _ensure(auth_client, new_deployment_id())

    assert response.status_code == 200, response.data
    assert response.data["result"]["projectId"] == str(ingested.id)
    assert _projects(workspace).count() == 1


@pytest.mark.parametrize("tenant", ["same_org", "other_org"])
def test_another_workspace_gets_404_for_a_linked_deployment_and_changes_nothing(
    request, tenant, auth_client, workspace
):
    other_client = request.getfixturevalue(
        "second_workspace_client" if tenant == "same_org" else "other_org_client"
    )
    other_workspace = (
        request.getfixturevalue("second_workspace")
        if tenant == "same_org"
        else request.getfixturevalue("other_org")[2]
    )
    deployment_id = new_deployment_id()
    owner = _ensure(auth_client, deployment_id).data["result"]

    refused = _ensure(other_client, deployment_id)

    assert refused.status_code == 404
    assert refused.data["status"] is False
    assert refused.data["message"] == get_error_message(
        "OMEGA_DEPLOYMENT_OBSERVE_NOT_FOUND"
    )
    assert owner["projectId"] not in str(refused.data)
    assert not _projects(other_workspace).exists()
    link = OmegaDeploymentObserveLink.no_workspace_objects.get()
    assert (link.workspace_id, str(link.project_id)) == (
        workspace.id,
        owner["projectId"],
    )
    assert _monitors(owner["projectId"]).count() == 2
    again = _ensure(auth_client, deployment_id)
    assert again.data["result"] == owner


def test_another_workspace_links_its_own_deployment_of_the_same_name(
    auth_client, second_workspace_client, workspace, second_workspace
):
    mine = _ensure(auth_client, new_deployment_id()).data["result"]

    theirs = _ensure(second_workspace_client, new_deployment_id())

    assert theirs.status_code == 200, theirs.data
    assert theirs.data["result"]["projectId"] != mine["projectId"]
    assert _projects(second_workspace).get().id != _projects(workspace).get().id
    assert {a["id"] for a in theirs.data["result"]["alerts"]}.isdisjoint(
        {a["id"] for a in mine["alerts"]}
    )
    assert _monitors(mine["projectId"]).count() == 2


def test_a_non_observe_project_with_the_name_is_409_and_nothing_is_written(
    auth_client, user, workspace, organization
):
    get_or_create_project(
        project_name=PROJECT,
        organization_id=str(organization.id),
        project_type="experiment",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    deployment_id = new_deployment_id()

    response = _ensure(auth_client, deployment_id)

    assert response.status_code == 409
    assert response.data["message"] == get_error_message(
        "OMEGA_DEPLOYMENT_OBSERVE_NAME_TAKEN"
    ).format(PROJECT)
    assert not _projects(workspace).exists()
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
    assert not UserAlertMonitor.no_workspace_objects.exists()


def test_an_experiment_project_made_later_does_not_break_an_existing_link(
    auth_client, user, workspace, organization
):
    deployment_id = new_deployment_id()
    first = _ensure(auth_client, deployment_id).data["result"]
    get_or_create_project(
        project_name=PROJECT,
        organization_id=str(organization.id),
        project_type="experiment",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )

    again = _ensure(auth_client, deployment_id)

    assert again.status_code == 200
    assert again.data["result"] == first


def test_an_org_not_admitted_to_deployments_gets_404_and_nothing_is_written(
    auth_client, organization
):
    from omega_rollout.models import ProductAccess

    ProductAccess.objects.filter(organization=organization).delete()

    response = _ensure(auth_client, new_deployment_id())

    assert response.status_code == 404
    assert response.data["code"] == "product_not_admitted"
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
    assert not Project.no_workspace_objects.filter(name=PROJECT).exists()


def test_a_paused_deployments_product_is_503_and_nothing_is_written(auth_client):
    from omega_rollout.models import ProductRollout

    ProductRollout.objects.filter(product="deployments").update(paused=True)

    response = _ensure(auth_client, new_deployment_id())

    assert response.status_code == 503
    assert response.data["code"] == "product_paused"
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
    assert not Project.no_workspace_objects.filter(name=PROJECT).exists()


def test_anonymous_is_refused_before_any_work(api_client):
    response = _ensure(api_client, new_deployment_id())

    assert response.status_code in (401, 403)
    assert not OmegaDeploymentObserveLink.no_workspace_objects.exists()
    assert not Project.no_workspace_objects.filter(name=PROJECT).exists()
