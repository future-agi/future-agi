"""Personal keys (M25x): only a key's own member marks it personal, and the
key sync then names that member, never a value a tenant set.

Each test is one property. The gateway is never called: the toggle changes
what the next key sync carries (``auth_bridge.gateway_key_payloads``), which
the tests read directly.
"""

import uuid

import pytest

from accounts.models import Organization
from agentcc.models.api_key import AgentccAPIKey
from agentcc.models.mcp_upstream_oauth import AgentccMCPPersonalKey
from agentcc.services import auth_bridge
from agentcc.tests.mcp_upstream_oauth_helpers import member
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db

TOGGLE = "/agentcc/mcp/keys/{}/personal/"


def _key(org, user, **fields):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org,
        user=user,
        gateway_key_id=f"gk-{uuid.uuid4().hex[:12]}",
        key_hash=uuid.uuid4().hex * 2,
        key_prefix="sk-agentcc-test",
        name=fields.pop("name", "m25x key"),
        owner=fields.pop("owner", "a label"),
        **fields,
    )


def _toggle(client, key, personal):
    return client.post(TOGGLE.format(key.pk), {"personal": personal}, format="json")


def _synced_metadata(key):
    key.refresh_from_db()
    (payload,) = auth_bridge.gateway_key_payloads([key])
    single = auth_bridge.gateway_key_payload(key)
    assert single["metadata"] == payload["metadata"]
    return payload["metadata"]


# --- Property: only the key's own member changes it ---------------------------


def test_personal_flag_owner_only(auth_client, organization, user, workspace):
    owner, owner_client = member(organization, workspace)
    _, other_client = member(organization, workspace)
    _, admin_client = member(organization, workspace, level=Level.ADMIN)
    key = _key(organization, owner)

    assert _toggle(auth_client, key, True).status_code == 403  # the org's Owner
    assert _toggle(admin_client, key, True).status_code == 403
    assert _toggle(other_client, key, True).status_code == 403
    assert not AgentccMCPPersonalKey.objects.exists()

    response = _toggle(owner_client, key, True)
    assert response.status_code == 200
    assert response.json()["result"] == {"key_id": str(key.pk), "personal": True}
    assert AgentccMCPPersonalKey.objects.get(api_key=key).user_id == owner.pk

    assert _toggle(admin_client, key, False).status_code == 403
    assert AgentccMCPPersonalKey.objects.filter(api_key=key).exists()
    assert _toggle(owner_client, key, False).json()["result"]["personal"] is False
    assert not AgentccMCPPersonalKey.objects.exists()


def test_a_key_with_no_member_cannot_be_made_personal(auth_client, organization):
    key = _key(organization, None)

    assert _toggle(auth_client, key, True).status_code == 403


def test_a_key_of_another_org_is_not_found(auth_client, user):
    elsewhere = Organization.objects.create(name="M25x Elsewhere")
    key = _key(elsewhere, user)

    assert _toggle(auth_client, key, True).status_code == 404
    missing = auth_client.post(
        TOGGLE.format(uuid.uuid4()), {"personal": True}, format="json"
    )
    assert missing.status_code == 404


def test_the_toggle_takes_a_boolean_only(auth_client, organization, user):
    key = _key(organization, user)

    for body in ({}, {"personal": "maybe"}, {"personal": True, "user_id": "x"}):
        response = auth_client.post(TOGGLE.format(key.pk), body, format="json")
        assert response.status_code == 400, body
    assert not AgentccMCPPersonalKey.objects.exists()


# --- Property: the sync names the key's own member, and nothing a tenant set ---


def test_no_key_is_personal_by_default(organization, user):
    key = _key(organization, user, metadata={"mcp_personal": "true", "env": "prod"})

    assert "mcp_personal" not in _synced_metadata(key)


def test_a_personal_key_syncs_its_own_members_id(auth_client, organization, user):
    key = _key(organization, user, owner="someone-else")
    _toggle(auth_client, key, True)

    assert _synced_metadata(key)["mcp_personal"] == str(user.pk)

    # The owner label is the tenant's; renaming it never renames the member.
    AgentccAPIKey.no_workspace_objects.filter(pk=key.pk).update(
        owner=str(uuid.uuid4()), metadata={"mcp_personal": str(uuid.uuid4())}
    )
    assert _synced_metadata(key)["mcp_personal"] == str(user.pk)

    _toggle(auth_client, key, False)
    assert "mcp_personal" not in _synced_metadata(key)


def test_a_mark_is_not_sent_once_the_key_is_no_longer_its_members(
    auth_client, organization, user, workspace
):
    other, _ = member(organization, workspace)
    key = _key(organization, user)
    _toggle(auth_client, key, True)

    AgentccAPIKey.no_workspace_objects.filter(pk=key.pk).update(user=other)
    assert "mcp_personal" not in _synced_metadata(key)
    AgentccAPIKey.no_workspace_objects.filter(pk=key.pk).update(user=None)
    assert "mcp_personal" not in _synced_metadata(key)


def test_personal_keys_keep_the_rest_of_the_payload(auth_client, organization, user):
    key = _key(organization, user, metadata={"env": "prod"})
    before = auth_bridge.gateway_key_payloads([key])[0]
    _toggle(auth_client, key, True)
    after = auth_bridge.gateway_key_payloads([key])[0]

    assert {k: v for k, v in after["metadata"].items() if k != "mcp_personal"} == (
        before["metadata"]
    )
    assert {k: v for k, v in after.items() if k != "metadata"} == {
        k: v for k, v in before.items() if k != "metadata"
    }
