"""Voice simulation follows the plan of the organization a request is made in.

A person can belong to two organizations: one is stored on the account and the
other is the one they are working in. The live call listener and the LiveKit
credentials check ask whether the organization the request is made in has voice
simulation on its plan, so a person working in their second organization gets
the answer for that organization. A person with one organization gets the
answer for their only organization.

The plan service is stood in for by organization: it answers yes for the
organizations a test lists as entitled and no for every other one.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

LISTENER_TOKEN = "listener-token"
VALIDATE_CREDENTIALS = "validate-credentials"
VIEWS = [LISTENER_TOKEN, VALIDATE_CREDENTIALS]


@pytest.fixture(autouse=True)
def on_cloud(settings):
    settings.CLOUD_DEPLOYMENT = "US"


def _make_second_org_workspace(user):
    organization = Organization.objects.create(
        name=f"Second Org {uuid.uuid4().hex[:8]}"
    )
    organization_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"Second Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=organization_membership,
    )
    return organization, workspace


def _plan(entitled_organizations):
    entitled = {str(organization.id) for organization in entitled_organizations}

    def check_feature(organization_id, feature):
        return SimpleNamespace(
            allowed=feature == "has_voice_sim" and organization_id in entitled,
            reason="Voice simulation requires a higher plan",
        )

    return check_feature


def _open_view(auth_client, view, entitled_organizations):
    with (
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch("tfc.ee_gates.voice_sim_oss_gate_response", return_value=None),
        patch(
            "ee.usage.services.entitlements.Entitlements.check_feature",
            side_effect=_plan(entitled_organizations),
        ) as check_feature,
    ):
        if view == LISTENER_TOKEN:
            response = auth_client.get(
                f"/simulate/api/livekit/listener-token/{uuid.uuid4()}/"
            )
        else:
            response = auth_client.post(
                "/simulate/api/livekit/validate-credentials/",
                {
                    "livekit_url": "wss://voice.example.test",
                    "api_key": "example-key",
                    "api_secret": "example-value",
                },
                format="json",
            )
    return response, check_feature


def _assert_plan_refusal(response):
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert body["feature"] == "voice_sim"


def _assert_past_plan_check(response, view):
    if view == LISTENER_TOKEN:
        # No call has this id, so the listener answers that it is not found.
        assert response.status_code == 404
        assert "not found" in response.json()["message"].lower()
    else:
        # On Future AGI Cloud the check answers that nothing was verified.
        assert response.status_code == 200
        assert response.json()["result"]["skipped"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("view", VIEWS, ids=VIEWS)
def test_second_organization_without_voice_plan_gets_refusal(
    auth_client, user, organization, view
):
    second, second_workspace = _make_second_org_workspace(user)
    assert user.organization_id == organization.id
    auth_client.set_workspace(second_workspace)

    response, check_feature = _open_view(auth_client, view, [organization])

    check_feature.assert_called_once_with(str(second.id), "has_voice_sim")
    _assert_plan_refusal(response)


@pytest.mark.django_db
@pytest.mark.parametrize("view", VIEWS, ids=VIEWS)
def test_second_organization_with_voice_plan_passes_the_check(
    auth_client, user, organization, view
):
    second, second_workspace = _make_second_org_workspace(user)
    assert user.organization_id == organization.id
    auth_client.set_workspace(second_workspace)

    response, check_feature = _open_view(auth_client, view, [second])

    check_feature.assert_called_once_with(str(second.id), "has_voice_sim")
    _assert_past_plan_check(response, view)


@pytest.mark.django_db
@pytest.mark.parametrize("view", VIEWS, ids=VIEWS)
def test_one_organization_with_voice_plan_passes_the_check(
    auth_client, organization, view
):
    response, check_feature = _open_view(auth_client, view, [organization])

    check_feature.assert_called_once_with(str(organization.id), "has_voice_sim")
    _assert_past_plan_check(response, view)


@pytest.mark.django_db
@pytest.mark.parametrize("view", VIEWS, ids=VIEWS)
def test_one_organization_without_voice_plan_gets_refusal(
    auth_client, organization, view
):
    response, check_feature = _open_view(auth_client, view, [])

    check_feature.assert_called_once_with(str(organization.id), "has_voice_sim")
    _assert_plan_refusal(response)
