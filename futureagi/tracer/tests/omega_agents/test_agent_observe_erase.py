"""Erase for the agent link rows (learning-service P1-08; the lane's erase test for its new
store): what an agent link holds is its EE id, the agent's name and which project it used.

- DELETE ends the link: no live row is left and the agent's name is cleared; only the id and
  the project reference stay, as the record of which project the agent used. A second
  DELETE answers ``unlinked: false`` and changes nothing.
- Deleting the workspace, the organization or the project removes the rows outright.
"""

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.tests.omega_agents.conftest import ensure, new_agent_id, unlink

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NAME = "Priya's billing helper"


def _rows(agent_id):
    return OmegaDeploymentObserveLink.all_objects.filter(ee_agent_id=agent_id)


def test_ending_a_link_leaves_no_live_row_and_no_agent_name(auth_client, workspace):
    agent_id = new_agent_id()
    linked = ensure(auth_client, agent_id, name=NAME).data["result"]

    ended = unlink(auth_client, agent_id)

    assert ended.status_code == 200, ended.data
    assert ended.data["result"] == {"unlinked": True}
    assert not OmegaDeploymentObserveLink.no_workspace_objects.filter(
        ee_agent_id=agent_id
    ).exists()
    (row,) = _rows(agent_id)
    assert row.deleted is True and row.deleted_at is not None
    assert row.agent_name == ""
    assert str(row.project_id) == linked["projectId"]
    assert not OmegaDeploymentObserveLink.all_objects.filter(
        workspace=workspace, agent_name__contains="Priya"
    ).exists()

    again = unlink(auth_client, agent_id)

    assert again.status_code == 200
    assert again.data["result"] == {"unlinked": False}
    assert list(_rows(agent_id)) == [row]


@pytest.mark.parametrize("owner", ["workspace", "organization", "project"])
def test_deleting_the_workspace_org_or_project_removes_the_rows(
    auth_client, workspace, organization, owner
):
    live_id, ended_id = new_agent_id(), new_agent_id()
    live = ensure(auth_client, live_id, name=NAME).data["result"]
    ensure(auth_client, ended_id, name=NAME)
    unlink(auth_client, ended_id)
    assert (
        OmegaDeploymentObserveLink.all_objects.filter(
            ee_agent_id__in=[live_id, ended_id]
        ).count()
        == 2
    )

    if owner == "workspace":
        Workspace.all_objects.filter(pk=workspace.pk).delete()
    elif owner == "organization":
        Organization.objects.filter(pk=organization.pk).delete()
    else:
        Project.all_objects.filter(id=live["projectId"]).delete()

    remaining = OmegaDeploymentObserveLink.all_objects.filter(
        ee_agent_id__in=[live_id, ended_id]
    )
    if owner == "project":
        # Only the project's own link goes; the ended link used another project.
        assert [row.ee_agent_id for row in remaining] == [ended_id]
    else:
        assert not remaining.exists()
