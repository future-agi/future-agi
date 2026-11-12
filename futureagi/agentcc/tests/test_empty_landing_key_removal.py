"""An empty workspace landing must not prevent removing an own org-wide key."""

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import WorkspaceMembership
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from agentcc.models import AgentccAPIKey
from agentcc.tests.test_agentcc_permissions import _user_with_level
from agentcc.tests.test_personal_key_self_service import gateway as gateway
from agentcc.tests.test_personal_key_self_service import session_client
from agentcc.tests.test_personal_key_workspace_boundary import own_key
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("operation", ["revoke", "destroy"])
@pytest.mark.parametrize("target", ["own", "peer", "foreign"])
def test_empty_landing_key_removal_is_owner_and_org_scoped(
    organization, workspace, gateway, scope_no_egress, operation, target
):
    clear_workspace_context()
    user = _user_with_level(organization, Level.MEMBER)
    peer = _user_with_level(organization, Level.MEMBER)
    key = own_key(peer if target == "peer" else user, organization)
    if target == "foreign":
        other = Organization.objects.create(name="Foreign key org")
        AgentccAPIKey._base_manager.filter(pk=key.pk).update(organization=other)
    WorkspaceMembership._base_manager.filter(user=user).delete()
    client = session_client(user, organization, workspace)
    client._credentials.pop("HTTP_X_WORKSPACE_ID")
    before = list(AgentccAPIKey._base_manager.order_by("pk").values())
    try:
        if operation == "revoke":
            response = client.post(f"/agentcc/api-keys/{key.pk}/revoke/")
        else:
            response = client.delete(f"/agentcc/api-keys/{key.pk}/")
        if target == "own":
            assert response.status_code in (200, 204), response.data
            gateway.revoke_key.assert_called_once()
            current = AgentccAPIKey._base_manager.filter(pk=key.pk).first()
            assert (
                current is None
                or current.deleted
                or current.status == AgentccAPIKey.REVOKED
            )
        else:
            assert response.status_code == 404, response.data
            gateway.revoke_key.assert_not_called()
            assert list(AgentccAPIKey._base_manager.order_by("pk").values()) == before
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("operation", ["list", "retrieve"])
def test_empty_landing_key_reads_keep_existing_scope(
    organization, workspace, gateway, scope_no_egress, operation
):
    clear_workspace_context()
    user = _user_with_level(organization, Level.MEMBER)
    key = own_key(user, organization)
    WorkspaceMembership._base_manager.filter(user=user).delete()
    client = session_client(user, organization, workspace)
    client._credentials.pop("HTTP_X_WORKSPACE_ID")
    try:
        path = (
            "/agentcc/api-keys/"
            if operation == "list"
            else f"/agentcc/api-keys/{key.pk}/"
        )
        response = client.get(path)
        assert response.status_code == (200 if operation == "list" else 404), (
            response.data
        )
        if operation == "list":
            assert response.data["result"] == []
        gateway.revoke_key.assert_not_called()
    finally:
        clear_workspace_context()
