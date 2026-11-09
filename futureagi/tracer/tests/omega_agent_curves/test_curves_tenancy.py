"""Two tenants and one agent's curves (learning-service P1-09, the lane's cross-tenant red
test): another workspace of the same org, and another org.

Tenant A's agent runs are traced to A's Observe project. Tenant B, naming A's project id (and
A's agent id), gets the same 404 as for an id that does not exist. The answer holds nothing
of A's, and nothing is read. B's own project is read for B, under B's id only.
"""

import uuid

import pytest

from tfc.utils.error_codes import get_error_message
from tracer.models.project import Project
from tracer.tests.omega_agent_curves.conftest import (
    agent_project,
    new_agent_id,
    read_curves,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TENANTS = ["same_org", "other_org"]


def _tenant_b(request, tenant):
    """B's client, organization and workspace."""
    if tenant == "same_org":
        workspace = request.getfixturevalue("second_workspace")
        return (
            request.getfixturevalue("second_workspace_client"),
            request.getfixturevalue("organization"),
            workspace,
        )
    organization, _, workspace = request.getfixturevalue("other_org")
    return request.getfixturevalue("other_org_client"), organization, workspace


@pytest.mark.parametrize("tenant", TENANTS)
def test_another_tenant_naming_this_project_gets_404_and_nothing_is_read(
    request, tenant, project_a, recording_read
):
    other_client, _, _ = _tenant_b(request, tenant)
    agent_id = new_agent_id()

    refused = read_curves(other_client, agent_id, project_a.id)
    unknown = read_curves(other_client, agent_id, uuid.uuid4())

    assert refused.status_code == unknown.status_code == 404
    assert refused.data == unknown.data
    assert refused.data["result"] == get_error_message(
        "OMEGA_AGENT_CURVES_PROJECT_NOT_FOUND"
    )
    for secret in (str(project_a.id), project_a.name):
        assert secret not in str(refused.data)
    assert recording_read.calls == []


@pytest.mark.parametrize("tenant", TENANTS)
def test_each_tenant_reads_only_its_own_project(
    request, tenant, auth_client, project_a, recording_read
):
    other_client, organization, workspace = _tenant_b(request, tenant)
    project_b = agent_project(organization, workspace)
    agent_id = new_agent_id()

    mine = read_curves(auth_client, agent_id, project_a.id)
    theirs = read_curves(other_client, agent_id, project_b.id)
    crossed = read_curves(auth_client, agent_id, project_b.id)

    assert mine.status_code == theirs.status_code == 200
    assert crossed.status_code == 404
    assert [call["project_id"] for call in recording_read.calls] == [
        str(project_a.id),
        str(project_b.id),
    ]
    assert mine.data["result"]["projectId"] == str(project_a.id)
    assert theirs.data["result"]["projectId"] == str(project_b.id)


def test_a_project_with_no_workspace_is_not_read(
    auth_client, organization, recording_read
):
    """For a default workspace, the workspace manager also returns the org's rows that have no
    workspace. The curves read only a project of the caller's exact workspace."""
    legacy = agent_project(organization, None)
    # save() would take the thread's workspace for a project created without one.
    Project.no_workspace_objects.filter(id=legacy.id).update(workspace=None)
    assert Project.no_workspace_objects.get(id=legacy.id).workspace_id is None

    response = read_curves(auth_client, new_agent_id(), legacy.id)

    assert response.status_code == 404
    assert recording_read.calls == []
