"""A Console agent's Observe link over the agent's life (learning-service P1-08): renamed,
deleted and made again, its project deleted in Observe, a monitor removed, a failure part way.

Acceptance: a rename keeps the link and the history; an agent deleted and made again under
the same name gets a new project; the old project and its traces stay in Observe.
"""

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_scan import TraceScanConfig
from tracer.services.omega_deployments.ensure import monitors as monitors_module
from tracer.services.project_deletion import soft_delete_projects
from tracer.tests.omega_agents.conftest import all_rows, ensure, new_agent_id, unlink

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _link(agent_id):
    return OmegaDeploymentObserveLink.no_workspace_objects.get(ee_agent_id=agent_id)


def _monitors(project_id):
    return UserAlertMonitor.no_workspace_objects.filter(project_id=project_id)


def test_a_rename_keeps_the_link_the_project_and_its_history(auth_client, workspace):
    agent_id = new_agent_id()
    before = ensure(auth_client, agent_id, name="Support Bot").data["result"]
    run = Trace.no_workspace_objects.create(
        project_id=before["projectId"], name="run before the rename"
    )

    after = ensure(auth_client, agent_id, name="Billing Bot")

    assert after.status_code == 200, after.data
    result = after.data["result"]
    assert (result["projectId"], result["projectName"]) == (
        before["projectId"],
        before["projectName"],
    )
    assert result["agentName"] == "Billing Bot"
    assert result["alerts"] == before["alerts"]
    assert result["mistakes"] == before["mistakes"]
    link = _link(agent_id)
    assert (link.agent_name, str(link.project_id)) == (
        "Billing Bot",
        before["projectId"],
    )
    run.refresh_from_db()
    assert str(run.project_id) == before["projectId"]
    assert Project.no_workspace_objects.filter(workspace=workspace).count() == 1
    assert not Project.no_workspace_objects.filter(
        workspace=workspace, name__startswith="agent/billing-bot-"
    ).exists()


def test_an_agent_deleted_and_made_again_under_its_name_gets_a_new_project(
    auth_client, workspace
):
    first_id = new_agent_id()
    first = ensure(auth_client, first_id).data["result"]
    run = Trace.no_workspace_objects.create(
        project_id=first["projectId"], name="the first agent's run"
    )
    ended = unlink(auth_client, first_id)
    assert ended.status_code == 200, ended.data
    assert ended.data["result"] == {"unlinked": True}

    second_id = new_agent_id()
    second = ensure(auth_client, second_id)

    assert second.status_code == 200, second.data
    result = second.data["result"]
    assert result["projectId"] != first["projectId"]
    assert result["projectName"] != first["projectName"]
    assert result["projectName"].startswith("agent/support-bot-")
    assert len(result["alerts"]) == 2
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in first["alerts"]}
    )
    # The first agent's project, its run and its monitors stay in Observe, unlinked.
    assert Project.no_workspace_objects.filter(id=first["projectId"]).exists()
    run.refresh_from_db()
    assert str(run.project_id) == first["projectId"]
    assert _monitors(first["projectId"]).count() == 2
    live = OmegaDeploymentObserveLink.no_workspace_objects.filter(
        kind="agent", workspace=workspace
    )
    assert [row.ee_agent_id for row in live] == [second_id]


def test_the_same_agent_linked_again_finds_its_own_project_and_set_up(auth_client):
    agent_id = new_agent_id()
    first = ensure(auth_client, agent_id).data["result"]
    # The user's own rate, set in the project's settings while the agent was linked.
    TraceScanConfig.no_workspace_objects.filter(project_id=first["projectId"]).update(
        sampling_rate=0.5
    )
    unlink(auth_client, agent_id)

    again = ensure(auth_client, agent_id)

    assert again.status_code == 200, again.data
    assert again.data["result"]["projectId"] == first["projectId"]
    assert again.data["result"]["alerts"] == first["alerts"]
    assert _monitors(first["projectId"]).count() == 2
    assert again.data["result"]["mistakes"]["samplingRate"] == 0.5
    assert (
        TraceScanConfig.no_workspace_objects.get(
            project_id=first["projectId"]
        ).sampling_rate
        == 0.5
    )
    assert (
        OmegaDeploymentObserveLink.all_objects.filter(ee_agent_id=agent_id).count() == 2
    )


def test_the_same_agent_linked_again_under_a_new_name_keeps_its_project(auth_client):
    agent_id = new_agent_id()
    first = ensure(auth_client, agent_id, name="Support Bot").data["result"]
    unlink(auth_client, agent_id)

    again = ensure(auth_client, agent_id, name="Billing Bot").data["result"]

    assert (again["projectId"], again["projectName"]) == (
        first["projectId"],
        first["projectName"],
    )
    assert again["agentName"] == "Billing Bot"
    assert again["alerts"] == first["alerts"]


def test_a_project_deleted_in_observe_is_made_again_with_fresh_defaults(
    auth_client, workspace
):
    agent_id = new_agent_id()
    first = ensure(auth_client, agent_id, name="Support Bot").data["result"]
    soft_delete_projects(
        Project.no_workspace_objects.filter(id=first["projectId"]), "observe"
    )

    again = ensure(auth_client, agent_id, name="Support Bot")

    assert again.status_code == 200, again.data
    result = again.data["result"]
    assert result["projectId"] != first["projectId"]
    assert result["projectName"] == first["projectName"]
    assert len(result["alerts"]) == 2
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in first["alerts"]}
    )
    assert result["mistakes"]["status"] == "on"
    assert TraceScanConfig.no_workspace_objects.filter(
        project_id=result["projectId"], sampling_rate=0.1
    ).exists()
    link = _link(agent_id)
    assert str(link.project_id) == result["projectId"]


def test_a_monitor_the_user_deleted_or_muted_stays_that_way(auth_client):
    agent_id = new_agent_id()
    first = ensure(auth_client, agent_id).data["result"]
    errors_id, tokens_id = (alert["id"] for alert in first["alerts"])
    UserAlertMonitor.objects.filter(id=errors_id).update(deleted=True)
    UserAlertMonitor.objects.filter(id=tokens_id).update(is_mute=True)

    again = ensure(auth_client, agent_id).data["result"]

    assert [(a["id"], a["kind"], a["isMuted"]) for a in again["alerts"]] == [
        (tokens_id, "daily_tokens", True)
    ]
    assert _monitors(first["projectId"]).count() == 1


def test_a_failure_part_way_returns_500_and_leaves_nothing_behind(
    auth_client, workspace, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("monitor store down")

    monkeypatch.setattr(monitors_module, "_create_monitor", fail)
    before = all_rows()

    response = ensure(auth_client, new_agent_id())

    assert response.status_code == 500
    assert response.data["message"] == get_error_message("OMEGA_AGENT_OBSERVE_FAILED")
    assert "monitor store down" not in str(response.data)
    assert all_rows() == before
    assert not Project.no_workspace_objects.filter(workspace=workspace).exists()
