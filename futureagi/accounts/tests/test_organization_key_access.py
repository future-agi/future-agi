import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import OrgApiKey
from accounts.tests.test_rbac_regressions import (
    _admin_client,
    _jwt_client,
    _make_workspace_user,
    _member_client,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db

ORGANIZATION_KEY = "/accounts/keys/"


def test_a_member_cannot_read_the_organization_key(organization, workspace):
    response = _member_client(organization, workspace).get(ORGANIZATION_KEY)

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert response.data["detail"] == (
        "Only organization admins can see the organization key. "
        "Ask an admin for an API key."
    )
    assert "secret_key" not in str(response.data)


def test_an_admin_reads_the_organization_key(organization, workspace):
    response = _admin_client(organization, workspace).get(ORGANIZATION_KEY)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert "*" not in response.data["data"]["secret_key"]


def test_a_members_own_key_acts_as_the_member(organization, workspace):
    member = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
        prefix="own-key-member",
    )
    _jwt_client(member, organization, workspace)
    key = OrgApiKey.objects.create(
        name="member key", organization=organization, type="user", user=member
    )
    client = APIClient()
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_ORGANIZATION_ID=str(organization.id),
    )

    who = client.get("/accounts/user-info/")
    organization_key = client.get(ORGANIZATION_KEY)

    assert who.status_code == status.HTTP_200_OK, who.data
    assert member.email in str(who.data)
    assert organization_key.status_code == status.HTTP_403_FORBIDDEN


def _member(organization, workspace, prefix):
    return _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
        prefix=prefix,
    )


def _switch(client, key, action):
    return client.post(
        f"/accounts/key/{action}_key/", {"key_id": str(key.id)}, format="json"
    )


@pytest.mark.parametrize("action", ["disable", "enable"])
def test_a_member_cannot_switch_the_organization_key_or_a_teammates(
    organization, workspace, action
):
    owner_of_key = _member(organization, workspace, "key-owner")
    system = OrgApiKey.objects.create(organization=organization, type="system")
    teammate = OrgApiKey.objects.create(
        name="teammate key", organization=organization, type="user", user=owner_of_key
    )
    enabled = action == "disable"
    OrgApiKey.objects.filter(id__in=[system.id, teammate.id]).update(enabled=enabled)
    member = _member_client(organization, workspace)

    for key in (system, teammate):
        response = _switch(member, key, action)
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        assert "Only organization admins or the key's creator" in str(response.data)
        key.refresh_from_db()
        assert key.enabled is enabled


def test_a_member_switches_their_own_key(organization, workspace):
    member = _member(organization, workspace, "own-switch")
    client = _jwt_client(member, organization, workspace)
    key = OrgApiKey.objects.create(
        name="my key", organization=organization, type="user", user=member
    )

    off = _switch(client, key, "disable")
    on = _switch(client, key, "enable")

    assert off.status_code == status.HTTP_200_OK, off.data
    assert on.status_code == status.HTTP_200_OK, on.data
    key.refresh_from_db()
    assert key.enabled is True


def test_an_admin_switches_any_key(organization, workspace):
    teammate = _member(organization, workspace, "admin-target")
    key = OrgApiKey.objects.create(
        name="teammate key", organization=organization, type="user", user=teammate
    )

    response = _switch(_admin_client(organization, workspace), key, "disable")

    assert response.status_code == status.HTTP_200_OK, response.data
    key.refresh_from_db()
    assert key.enabled is False


def test_the_key_list_says_which_keys_the_caller_can_switch(organization, workspace):
    member = _member(organization, workspace, "list-member")
    client = _jwt_client(member, organization, workspace)
    mine = OrgApiKey.objects.create(
        name="mine", organization=organization, type="user", user=member
    )
    OrgApiKey.objects.create(organization=organization, type="system", name="system")

    rows = client.get("/accounts/key/get_secret_keys/").data["result"]["table"]

    switchable = {str(row["id"]): row["can_switch"] for row in rows}
    assert switchable.pop(str(mine.id)) is True
    assert switchable and not any(switchable.values())
