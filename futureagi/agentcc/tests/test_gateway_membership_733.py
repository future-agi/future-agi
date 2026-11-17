"""Use real org/membership rows: Organization has no soft-delete column."""

import secrets
from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.tests.test_retirement_733 import bulk, make_key

pytestmark = pytest.mark.django_db


def test_733_personal_key_issue_bulk_and_restore_on_real_org(organization, user):
    client = MagicMock()
    raw = "sk-agentcc-" + secrets.token_hex(24)
    client.create_key.return_value = {"id": "person-733", "key": raw}
    client.list_keys.return_value = {"data": []}
    client.import_keys.return_value = {"loaded": 1}
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        key, issued = auth_bridge.provision_key(
            name="personal", organization=organization, user=user
        )
        assert issued == raw
        assert key.user_id == user.pk
        # A workspace, if the request context attached one, is the key's org's.
        assert key.workspace is None or key.workspace.organization_id == organization.pk
        assert client.create_key.call_args.kwargs["metadata"]["user_id"] == str(user.pk)
        response = bulk()
        assert response.status_code == 200, response.data
        assert [row["id"] for row in response.data["result"]] == [key.gateway_key_id]
        assert response.data["result"][0]["metadata"]["org_id"] == str(organization.pk)
        assert response.data["result"][0]["metadata"]["user_id"] == str(user.pk)
        assert auth_bridge.sync_keys(organization) == 1
    client.import_keys.assert_called_once()
    assert client.import_keys.call_args.args[0][0]["id"] == key.gateway_key_id


@pytest.mark.parametrize(
    "removed", ["inactive_membership", "deleted_membership", "inactive_user"]
)
def test_733_ineligible_person_cannot_issue_load_or_be_adopted(
    organization, user, removed
):
    key = make_key(organization, user)
    if removed == "inactive_user":
        # Deliberately leave the in-memory user active: eligibility must read DB.
        User._base_manager.filter(pk=user.pk).update(is_active=False)
    else:
        OrganizationMembership._base_manager.filter(
            organization=organization, user=user
        ).update(
            **{"is_active": False}
            if removed == "inactive_membership"
            else {"deleted": True}
        )
    client = MagicMock()
    client.list_keys.return_value = {
        "data": [
            {
                "id": "unadopted",
                "status": "active",
                "metadata": {"org_id": str(organization.pk), "user_id": str(user.pk)},
            }
        ]
    }
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        with pytest.raises(ValueError, match="live organization membership"):
            auth_bridge.provision_key(
                name="refused", organization=organization, user=user
            )
        client.create_key.assert_not_called()
        assert not auth_bridge.gateway_loadable_keys().filter(pk=key.pk).exists()
        assert auth_bridge.sync_keys(organization) == 0
    client.revoke_key.assert_called_once_with("unadopted")
    assert not AgentccAPIKey._base_manager.filter(gateway_key_id="unadopted").exists()
    response = bulk()
    assert response.status_code == 200
    assert response.data["result"] == []
    assert {"id": key.gateway_key_id, "key_hash": key.key_hash} in response.data[
        "retired"
    ]


@pytest.mark.parametrize("removed", ["inactive_membership", "deleted_membership"])
def test_733_live_membership_in_another_org_cannot_restore_person_key(
    organization, user, removed
):
    key = make_key(organization, user)
    other_org = Organization.objects.create(name="Other person-key organization")
    OrganizationMembership._base_manager.create(
        organization=other_org, user=user, is_active=True
    )
    other_key = make_key(other_org, user)
    OrganizationMembership._base_manager.filter(
        organization=organization, user=user
    ).update(
        **{"is_active": False}
        if removed == "inactive_membership"
        else {"deleted": True}
    )

    # Same live user, but only org B has a live membership. The Exists query
    # must correlate BOTH user_id and organization_id, not just the person.
    assert User._base_manager.get(pk=user.pk).is_active
    assert not auth_bridge.gateway_loadable_keys().filter(pk=key.pk).exists()
    assert not auth_bridge.gateway_loadable_keys(organization).exists()
    assert auth_bridge.gateway_loadable_keys(other_org).filter(pk=other_key.pk).exists()
    response = bulk()
    assert response.status_code == 200, response.data
    assert [row["id"] for row in response.data["result"]] == [other_key.gateway_key_id]
    assert {"id": key.gateway_key_id, "key_hash": key.key_hash} in response.data[
        "retired"
    ]

    client = MagicMock()
    client.list_keys.return_value = {"data": []}
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        assert auth_bridge.sync_keys(organization) == 0
    client.list_keys.assert_called_once_with()
    client.import_keys.assert_not_called()
    client.create_key.assert_not_called()
    client.revoke_key.assert_not_called()
    assert AgentccAPIKey._base_manager.get(pk=key.pk).organization_id == organization.pk


def test_733_sync_adopts_only_the_live_person_on_real_org(organization, user):
    client = MagicMock()
    client.list_keys.return_value = {
        "data": [
            {
                "id": "adopted-person",
                "status": "active",
                "metadata": {"org_id": str(organization.pk), "user_id": str(user.pk)},
            }
        ]
    }
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        assert auth_bridge.sync_keys(organization) == 1
    row = AgentccAPIKey._base_manager.get(gateway_key_id="adopted-person")
    assert row.organization_id == organization.pk
    assert row.user_id == user.pk
    client.revoke_key.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
def test_733_tenant_cannot_supply_person_identity(
    organization, user, workspace, operation
):
    from agentcc.tests.test_personal_key_self_service import session_client

    auth_client = session_client(user, organization, workspace)
    key = make_key(organization, user, metadata={"env": "test"})
    with patch("agentcc.views.api_key.auth_bridge") as bridge:
        if operation == "create":
            response = auth_client.post(
                "/agentcc/api-keys/",
                {"name": "forged-person", "metadata": {"user_id": str(user.pk)}},
                format="json",
            )
        else:
            response = auth_client.patch(
                f"/agentcc/api-keys/{key.pk}/",
                {"metadata": {"user_id": str(user.pk)}},
                format="json",
            )
        assert response.status_code == 400, response.data
        assert "user_id" in str(response.data)
        bridge.provision_key.assert_not_called()
        bridge.update_key.assert_not_called()
    key.refresh_from_db()
    assert key.metadata == {"env": "test"}


def test_733_empty_real_org_still_has_a_bulk_snapshot(organization):
    response = bulk()
    assert response.status_code == 200, response.data
    assert response.data["result"] == []
    assert response.data["snapshot"] == {"complete": True, "issued_ever": 0}
