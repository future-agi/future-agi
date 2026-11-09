"""The agent kind on the Observe link model (learning-service P1-08): deployment and agent
links side by side, exactly one EE id per kind, one live link per agent id, in the database
itself and not only in the ensure.
"""

import pytest
from django.db import IntegrityError, transaction

from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.tests.omega_agents.conftest import ensure, new_agent_id
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_deployment_and_agent_links_live_side_by_side(
    auth_client, organization, workspace, settings
):
    from omega_rollout.testing import admit_for_tests

    admit_for_tests(organization, products=["deployments"])
    # These tests run as hosted Future AGI, where Deployments needs its own part.
    settings.OMEGA_RELEASED_PARTS = "agent_observe,hosted_deployments"
    deployment_id = new_deployment_id()
    deployment = auth_client.post(
        ensure_url(deployment_id), {"name": "billing-bot"}, format="json"
    )
    agent = ensure(auth_client, new_agent_id())

    assert (deployment.status_code, agent.status_code) == (200, 200)
    assert deployment.data["result"]["projectName"] == "deploy/billing-bot"
    rows = OmegaDeploymentObserveLink.no_workspace_objects.filter(
        workspace=workspace
    ).order_by("kind")
    assert [(row.kind, row.ee_deployment_id is None) for row in rows] == [
        ("agent", True),
        ("deployment", False),
    ]
    # A second deployment ensure is still idempotent beside an agent link.
    again = auth_client.post(
        ensure_url(deployment_id), {"name": "billing-bot"}, format="json"
    )
    assert again.data["result"] == deployment.data["result"]


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "agent"},
        {"kind": "agent", "ee_deployment_id": "dep-0123456789abcdef"},
        {
            "kind": "agent",
            "ee_agent_id": "a-1",
            "ee_deployment_id": "dep-0123456789abcdef",
        },
        {"kind": "deployment", "ee_agent_id": "a-1"},
        {"kind": "deployment"},
    ],
)
def test_the_database_holds_exactly_one_id_per_kind(
    user, workspace, organization, fields
):
    project = get_or_create_project(
        project_name="agent/kind-check",
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        OmegaDeploymentObserveLink.no_workspace_objects.create(
            organization=organization, workspace=workspace, project=project, **fields
        )


def test_the_database_allows_one_live_link_per_agent_id(user, workspace, organization):
    project = get_or_create_project(
        project_name="agent/unique-check",
        organization_id=str(organization.id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )
    row = {
        "organization": organization,
        "workspace": workspace,
        "project": project,
        "kind": "agent",
        # The live id is unique across every workspace: a fresh one, so a row another
        # test left behind can never take it first.
        "ee_agent_id": new_agent_id(),
    }
    first = OmegaDeploymentObserveLink.no_workspace_objects.create(**row)
    with pytest.raises(IntegrityError), transaction.atomic():
        OmegaDeploymentObserveLink.no_workspace_objects.create(**row)
    OmegaDeploymentObserveLink.no_workspace_objects.filter(pk=first.pk).update(
        deleted=True
    )
    OmegaDeploymentObserveLink.no_workspace_objects.create(**row)  # live again: allowed
