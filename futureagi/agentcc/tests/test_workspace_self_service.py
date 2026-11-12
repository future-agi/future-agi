"""Independent F2 regression input; no product overlay or fixture privilege lift."""

import pytest

from accounts.models.workspace import WorkspaceMembership
from agentcc.models import AgentccAPIKey
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_personal_key_self_service import gateway as gateway
from agentcc.tests.test_personal_key_self_service import session_client
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("org_level", [Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("operation", ["create", "revoke", "delete"])
def test_read_only_workspace_keeps_personal_key_rights(
    organization, workspace, gateway, org_level, operation
):
    user = _user_with_level(organization, org_level)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    assert user.can_access_workspace(workspace)
    assert not user.can_write_to_workspace(workspace)
    client = session_client(user, organization, workspace)
    if operation == "create":
        response = client.post(
            "/agentcc/api-keys/", {"name": "own-personal-key"}, format="json"
        )
        assert response.status_code == 201, response.data
        gateway.import_committed_key.assert_called_once()
        key = AgentccAPIKey.no_workspace_objects.get(pk=response.data["result"]["id"])
        assert key.user_id == user.pk
        assert key.workspace_id is None
        assert key.status == AgentccAPIKey.ACTIVE
    else:
        key = AgentccAPIKey.no_workspace_objects.create(
            organization=organization, user=user, name="own", gateway_key_id="own"
        )
        if operation == "revoke":
            response = client.post(f"/agentcc/api-keys/{key.pk}/revoke/")
            assert response.status_code == 200, response.data
            assert response.data["result"]["gateway_revocation_confirmed"] is True
            gateway.revoke_key_confirmed.assert_called_once_with("own")
            gateway.revoke_key.assert_not_called()
        else:
            response = client.delete(f"/agentcc/api-keys/{key.pk}/")
            assert response.status_code == 204, response.data
            gateway.revoke_key.assert_called_once_with("own")
            gateway.revoke_key_confirmed.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "revoke", "delete"])
def test_workspace_member_positive_control(organization, workspace, gateway, operation):
    user = _user_with_level(organization, Level.MEMBER)
    assert user.can_write_to_workspace(workspace)
    client = session_client(user, organization, workspace)
    if operation == "create":
        response = client.post(
            "/agentcc/api-keys/", {"name": "own-control"}, format="json"
        )
        assert response.status_code == 201, response.data
        gateway.import_committed_key.assert_called_once()
        key = AgentccAPIKey.no_workspace_objects.get(pk=response.data["result"]["id"])
        assert key.user_id == user.pk
        assert key.workspace_id is None
        assert key.status == AgentccAPIKey.ACTIVE
    else:
        key = AgentccAPIKey.no_workspace_objects.create(
            organization=organization, user=user, name="own", gateway_key_id="control"
        )
        response = (
            client.post(f"/agentcc/api-keys/{key.pk}/revoke/")
            if operation == "revoke"
            else client.delete(f"/agentcc/api-keys/{key.pk}/")
        )
        assert response.status_code == (200 if operation == "revoke" else 204), (
            response.data
        )
        if operation == "revoke":
            assert response.data["result"]["gateway_revocation_confirmed"] is True
            gateway.revoke_key_confirmed.assert_called_once_with("control")
            gateway.revoke_key.assert_not_called()
        else:
            gateway.revoke_key.assert_called_once_with("control")
            gateway.revoke_key_confirmed.assert_not_called()
