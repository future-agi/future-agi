"""Two tenants and one Console agent id (learning-service P1-08, the lane's cross-tenant red
test): another workspace of the same org, and another org.

Tenant A links its agent. Tenant B, naming the same id, can neither link it, end it, learn
A's project, nor change anything of A's; B's own agent of the same name gets its own project.
"""

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig
from tracer.tests.omega_agents.conftest import ensure, new_agent_id, unlink

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TENANTS = ["same_org", "other_org"]


def _tenant_b(request, tenant):
    if tenant == "same_org":
        return (
            request.getfixturevalue("second_workspace_client"),
            request.getfixturevalue("second_workspace"),
        )
    return (
        request.getfixturevalue("other_org_client"),
        request.getfixturevalue("other_org")[2],
    )


def _snapshot(workspace):
    """Everything the agent route can write in ``workspace``."""
    return {
        "links": sorted(
            (str(row.pk), row.ee_agent_id, row.agent_name, row.deleted)
            for row in OmegaDeploymentObserveLink.all_objects.filter(
                workspace=workspace
            )
        ),
        "projects": sorted(
            str(pk)
            for pk in Project.all_objects.filter(workspace=workspace).values_list(
                "id", flat=True
            )
        ),
        "monitors": sorted(
            str(pk)
            for pk in UserAlertMonitor.all_objects.filter(
                workspace=workspace
            ).values_list("id", flat=True)
        ),
        "scan_configs": sorted(
            (str(row.project_id), row.sampling_rate, row.enabled)
            for row in TraceScanConfig.all_objects.filter(project__workspace=workspace)
        ),
    }


@pytest.mark.parametrize("tenant", TENANTS)
def test_another_tenant_cannot_link_an_agent_linked_here(
    request, tenant, auth_client, workspace
):
    other_client, other_workspace = _tenant_b(request, tenant)
    agent_id = new_agent_id()
    owner = ensure(auth_client, agent_id).data["result"]
    mine, theirs = _snapshot(workspace), _snapshot(other_workspace)

    refused = ensure(other_client, agent_id, name="Taken Over")

    assert refused.status_code == 404
    assert refused.data["status"] is False
    assert refused.data["message"] == get_error_message("OMEGA_AGENT_OBSERVE_NOT_FOUND")
    for secret in (owner["projectId"], owner["projectName"], owner["agentName"]):
        assert secret not in str(refused.data)
    assert _snapshot(workspace) == mine
    assert _snapshot(other_workspace) == theirs
    assert ensure(auth_client, agent_id).data["result"] == owner


@pytest.mark.parametrize("tenant", TENANTS)
def test_another_tenant_cannot_end_an_agent_linked_here(
    request, tenant, auth_client, workspace
):
    other_client, other_workspace = _tenant_b(request, tenant)
    agent_id = new_agent_id()
    owner = ensure(auth_client, agent_id).data["result"]
    mine, theirs = _snapshot(workspace), _snapshot(other_workspace)

    answer = unlink(other_client, agent_id)

    # The same answer as for an id never linked anywhere: nothing is revealed.
    assert answer.status_code == 200, answer.data
    assert answer.data["result"] == {"unlinked": False}
    never = unlink(other_client, new_agent_id())
    assert never.data == answer.data
    assert _snapshot(workspace) == mine
    assert _snapshot(other_workspace) == theirs
    assert ensure(auth_client, agent_id).data["result"] == owner


@pytest.mark.parametrize("tenant", TENANTS)
def test_another_tenants_agent_of_the_same_name_gets_its_own_project(
    request, tenant, auth_client, workspace
):
    other_client, other_workspace = _tenant_b(request, tenant)
    mine = ensure(auth_client, new_agent_id()).data["result"]

    theirs = ensure(other_client, new_agent_id())

    assert theirs.status_code == 200, theirs.data
    result = theirs.data["result"]
    assert result["projectId"] != mine["projectId"]
    assert Project.no_workspace_objects.get(id=result["projectId"]).workspace_id == (
        other_workspace.id
    )
    assert Project.no_workspace_objects.get(id=mine["projectId"]).workspace_id == (
        workspace.id
    )
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in mine["alerts"]}
    )
    assert (
        UserAlertMonitor.no_workspace_objects.filter(
            project_id=mine["projectId"]
        ).count()
        == 2
    )


@pytest.mark.parametrize("tenant", TENANTS)
def test_an_id_ended_here_and_linked_by_another_tenant_never_gets_this_project(
    request, tenant, auth_client, workspace
):
    """Linked again, an agent goes back to its own ended link's project, and only ever to
    one ended in the same workspace. Another tenant linking the id A ended gets a project
    of its own; once it ends, A gets its own project and monitors back, not B's."""
    other_client, other_workspace = _tenant_b(request, tenant)
    agent_id = new_agent_id()
    mine = ensure(auth_client, agent_id).data["result"]
    assert unlink(auth_client, agent_id).data["result"] == {"unlinked": True}
    ended_here = _snapshot(workspace)

    theirs = ensure(other_client, agent_id)

    assert theirs.status_code == 200, theirs.data
    result = theirs.data["result"]
    assert result["projectId"] != mine["projectId"]
    assert mine["projectId"] not in str(theirs.data)
    assert Project.no_workspace_objects.get(id=result["projectId"]).workspace_id == (
        other_workspace.id
    )
    assert {a["id"] for a in result["alerts"]}.isdisjoint(
        {a["id"] for a in mine["alerts"]}
    )
    assert _snapshot(workspace) == ended_here
    (ended,) = OmegaDeploymentObserveLink.all_objects.filter(
        workspace=workspace, ee_agent_id=agent_id
    )
    assert (ended.deleted, str(ended.project_id)) == (True, mine["projectId"])

    assert unlink(other_client, agent_id).data["result"] == {"unlinked": True}
    back = ensure(auth_client, agent_id)

    assert back.status_code == 200, back.data
    assert back.data["result"]["projectId"] == mine["projectId"]
    assert [a["id"] for a in back.data["result"]["alerts"]] == [
        a["id"] for a in mine["alerts"]
    ]
    assert result["projectId"] not in str(back.data)


def test_an_org_not_admitted_to_the_console_cannot_reach_the_route_for_any_id(
    auth_client, other_org, other_org_client
):
    from omega_rollout.models import ProductAccess

    agent_id = new_agent_id()
    owner = ensure(auth_client, agent_id).data["result"]
    ProductAccess.objects.filter(organization=other_org[0]).delete()

    for response in (
        ensure(other_org_client, agent_id),
        unlink(other_org_client, agent_id),
    ):
        assert response.status_code == 404
        assert response.data["code"] == "product_not_admitted"
    assert ensure(auth_client, agent_id).data["result"] == owner
