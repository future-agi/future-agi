"""The API-key endpoints: content mode, key limits and the admin-only writes.

A key's ``log_content`` lives in the key's metadata, which
reaches the gateway in the key's synced metadata; ``expires_at`` is a model
column. The key limits ``rate_limit_rpm``, ``rate_limit_tpm`` and
``allowed_ips`` are refused with ``key_limit_not_enforced``: the gateway reads
them only for config-file keys, never for a key synced from here, so a key
that took them would look limited and not be. Every write on
``/agentcc/api-keys/`` is for org Owners and Admins only.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models import AgentccAPIKey
from agentcc.serializers.api_key import (
    AgentccAPIKeyCreateSerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.services import auth_bridge
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.error_codes import get_error_message

KEYS = "/agentcc/api-keys/"
NOT_ENFORCED = "key_limit_not_enforced"
# Each unenforced limit with a well-formed value, so a refusal is the field's
# own and not a value check.
UNENFORCED_LIMITS = {
    "rate_limit_rpm": 60,
    "rate_limit_tpm": 100000,
    "allowed_ips": ["10.0.0.0/8"],
}
# The limits in the form a key's metadata stores them.
STORED_LIMITS = {
    "rate_limit_rpm": 60,
    "rate_limit_tpm": 100000,
    "allowed_ips": "10.0.0.0/8",
}


@pytest.fixture
def gateway():
    """The gateway client auth_bridge calls, mocked. Every call returns plain
    data: a MagicMock reaching a response would make the renderer loop
    instead of failing."""
    client = MagicMock()
    client.update_key.return_value = {}
    client.revoke_key.return_value = {}
    client.list_keys.return_value = {"data": []}
    client.import_keys.return_value = {"loaded": 0}
    client.create_key.return_value = {
        "id": f"gw-{uuid.uuid4().hex[:8]}",
        "key": "pk-test-raw-key-value",  # a test value, not a credential
        "key_prefix": "pk-test",
    }
    with patch("agentcc.services.auth_bridge.get_gateway_client", return_value=client):
        yield client


@pytest.fixture
def api_key(organization, workspace):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        gateway_key_id=f"gw-key-{uuid.uuid4().hex[:8]}",
        key_prefix="pk-key",
        key_hash="keyhash",
        name="content-mode-key",
        metadata={"org_id": str(organization.id), "unit": "core"},
    )


@pytest.fixture
def client_with_role(organization, workspace):
    """``client_with_role(level, role)``: a client for a new org user with that
    role in the test org and workspace."""
    clients = []

    def build(level, role):
        user = User.objects.create_user(
            email=f"role-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name="Role User",
            organization=organization,
            organization_role=role,
        )
        membership = OrganizationMembership.no_workspace_objects.create(
            user=user, organization=organization, role=role, level=level, is_active=True
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            organization_membership=membership,
            is_active=True,
        )
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.stop_workspace_injection()


@pytest.fixture
def member_client(client_with_role):
    """An org Member: may read keys, may not write them."""
    return client_with_role(Level.MEMBER, OrganizationRoles.MEMBER)


def _patch(client, api_key, body):
    return client.patch(f"{KEYS}{api_key.id}/", body, format="json")


def _sent_metadata(gateway):
    """The metadata the last key update pushed to the gateway."""
    return gateway.update_key.call_args.kwargs["metadata"]


def _assert_not_enforced(response, *fields):
    """A 400 with the key_limit_not_enforced code naming exactly ``fields``."""
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = response.json()
    assert body["code"] == NOT_ENFORCED
    assert body["result"] == get_error_message("KEY_LIMIT_NOT_ENFORCED").format(
        ", ".join(sorted(fields))
    )


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["full", "metadata", "none"])
def test_log_content_lands_in_key_metadata(auth_client, api_key, gateway, mode):
    response = _patch(auth_client, api_key, {"log_content": mode})

    assert response.status_code == status.HTTP_200_OK, response.json()
    api_key.refresh_from_db()
    assert api_key.metadata["log_content"] == mode
    assert api_key.metadata["unit"] == "core"  # the rest of the metadata stays
    assert _sent_metadata(gateway)["log_content"] == mode
    assert auth_bridge.gateway_key_payload(api_key)["metadata"]["log_content"] == mode


@pytest.mark.integration
@pytest.mark.parametrize("value", ["everything", "", "NONE", None])
def test_log_content_rejects_other_values(auth_client, api_key, gateway, value):
    response = _patch(auth_client, api_key, {"log_content": value})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "log_content" in response.json()["result"]
    gateway.update_key.assert_not_called()
    api_key.refresh_from_db()
    assert "log_content" not in api_key.metadata


@pytest.mark.integration
def test_member_cannot_set_log_content(member_client, api_key, gateway):
    response = _patch(member_client, api_key, {"log_content": "full"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    gateway.update_key.assert_not_called()


@pytest.mark.integration
def test_create_takes_log_content(auth_client, gateway):
    response = auth_client.post(
        KEYS, {"name": "new-key", "log_content": "none"}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED, response.json()
    sent = gateway.create_key.call_args.kwargs["metadata"]
    assert sent["log_content"] == "none"
    key = AgentccAPIKey.no_workspace_objects.get(id=response.json()["result"]["id"])
    assert key.metadata["log_content"] == "none"


@pytest.mark.integration
def test_expires_at_is_set_and_cleared(auth_client, api_key, gateway):
    expires_at = (timezone.now() + timedelta(days=30)).replace(microsecond=0)
    response = _patch(auth_client, api_key, {"expires_at": expires_at.isoformat()})

    assert response.status_code == status.HTTP_200_OK, response.json()
    api_key.refresh_from_db()
    assert api_key.expires_at == expires_at
    payload = auth_bridge.gateway_key_payload(api_key)
    assert payload["expires_at"] == expires_at.isoformat()
    assert payload["metadata"]["org_id"] == str(api_key.organization_id)

    # null clears the expiry.
    response = _patch(auth_client, api_key, {"expires_at": None})
    assert response.status_code == status.HTTP_200_OK, response.json()
    api_key.refresh_from_db()
    assert api_key.expires_at is None


@pytest.mark.integration
@pytest.mark.parametrize("field", sorted(UNENFORCED_LIMITS))
@pytest.mark.parametrize("valued", [True, False], ids=["value", "null"])
def test_update_refuses_unenforced_limit(auth_client, api_key, gateway, field, valued):
    body = {field: UNENFORCED_LIMITS[field] if valued else None}

    response = _patch(auth_client, api_key, body)

    _assert_not_enforced(response, field)
    gateway.update_key.assert_not_called()
    api_key.refresh_from_db()
    assert field not in api_key.metadata


@pytest.mark.integration
@pytest.mark.parametrize("field", sorted(UNENFORCED_LIMITS))
def test_update_refuses_unenforced_limit_in_metadata(
    auth_client, api_key, gateway, field
):
    response = _patch(auth_client, api_key, {"metadata": {"unit": "x", field: "60"}})

    _assert_not_enforced(response, field)
    gateway.update_key.assert_not_called()
    api_key.refresh_from_db()
    assert api_key.metadata == {"org_id": str(api_key.organization_id), "unit": "core"}


@pytest.mark.integration
@pytest.mark.parametrize("field", sorted(UNENFORCED_LIMITS))
@pytest.mark.parametrize("valued", [True, False], ids=["value", "null"])
def test_create_refuses_unenforced_limit(auth_client, gateway, field, valued):
    name = f"refused-{field}"
    body = {"name": name, field: UNENFORCED_LIMITS[field] if valued else None}

    response = auth_client.post(KEYS, body, format="json")

    _assert_not_enforced(response, field)
    gateway.create_key.assert_not_called()
    assert not AgentccAPIKey.no_workspace_objects.filter(name=name).exists()


@pytest.mark.integration
@pytest.mark.parametrize("field", sorted(UNENFORCED_LIMITS))
def test_create_refuses_unenforced_limit_in_metadata(auth_client, gateway, field):
    name = f"refused-meta-{field}"
    body = {"name": name, "metadata": {field: "60"}}

    response = auth_client.post(KEYS, body, format="json")

    _assert_not_enforced(response, field)
    gateway.create_key.assert_not_called()
    assert not AgentccAPIKey.no_workspace_objects.filter(name=name).exists()


@pytest.mark.integration
def test_refusal_names_every_unenforced_limit_and_writes_nothing(
    auth_client, api_key, gateway
):
    # With log_content valid alongside, nothing at all is written: the limits
    # are not silently dropped while the rest applies.
    response = _patch(
        auth_client,
        api_key,
        {
            "log_content": "none",
            "rate_limit_tpm": 1000,
            "metadata": {"allowed_ips": "10.0.0.1"},
        },
    )

    _assert_not_enforced(response, "allowed_ips", "rate_limit_tpm")
    gateway.update_key.assert_not_called()
    api_key.refresh_from_db()
    assert "log_content" not in api_key.metadata


@pytest.mark.integration
def test_create_with_log_content_and_metadata_still_works(auth_client, gateway):
    response = auth_client.post(
        KEYS,
        {
            "name": "content-only",
            "log_content": "metadata",
            "metadata": {"unit": "a"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED, response.json()
    sent = gateway.create_key.call_args.kwargs["metadata"]
    assert sent["log_content"] == "metadata" and sent["unit"] == "a"
    assert not set(UNENFORCED_LIMITS) & set(sent)


@pytest.fixture
def key_with_stored_limits(organization, workspace):
    """A key whose metadata holds the limits."""
    return AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        gateway_key_id=f"gw-key-{uuid.uuid4().hex[:8]}",
        key_prefix="pk-key",
        key_hash="storedlimits",
        name="stored-limits",
        metadata={
            "org_id": str(organization.id),
            "unit": "core",
            "log_content": "none",
            **STORED_LIMITS,
        },
    )


@pytest.mark.integration
def test_reads_do_not_return_stored_limits(auth_client, key_with_stored_limits):
    key = key_with_stored_limits

    detail = auth_client.get(f"{KEYS}{key.id}/")
    listed = auth_client.get(KEYS)

    assert detail.status_code == status.HTTP_200_OK, detail.json()
    assert listed.status_code == status.HTTP_200_OK, listed.json()
    [from_list] = [k for k in listed.json()["result"] if k["id"] == str(key.id)]
    for read in (detail.json()["result"], from_list):
        assert read["metadata"] == {
            "org_id": str(key.organization_id),
            "unit": "core",
            "log_content": "none",
        }
    key.refresh_from_db()
    assert STORED_LIMITS.items() <= key.metadata.items()  # the row is untouched


@pytest.mark.integration
def test_updates_keep_stored_limits_in_the_row(
    auth_client, key_with_stored_limits, gateway
):
    key = key_with_stored_limits

    renamed = _patch(auth_client, key, {"name": "renamed-key"})
    assert renamed.status_code == status.HTTP_200_OK, renamed.json()
    key.refresh_from_db()
    assert STORED_LIMITS.items() <= key.metadata.items()

    replaced = _patch(
        auth_client, key, {"metadata": {"unit": "search"}, "log_content": "full"}
    )

    assert replaced.status_code == status.HTTP_200_OK, replaced.json()
    assert not set(STORED_LIMITS) & set(replaced.json()["result"]["metadata"])
    key.refresh_from_db()
    assert key.metadata == {
        "org_id": str(key.organization_id),
        "unit": "search",
        "log_content": "full",
        **STORED_LIMITS,
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "body,field",
    [
        ({"expires_at": "2020-01-01T00:00:00Z"}, "expires_at"),
        ({"log_content": "loud"}, "log_content"),
    ],
)
def test_other_key_fields_still_validated(auth_client, api_key, gateway, body, field):
    response = _patch(auth_client, api_key, body)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] != NOT_ENFORCED
    assert field in response.json()["result"]
    gateway.update_key.assert_not_called()


def test_serializers_validate_key_settings():
    for serializer_class in (
        AgentccAPIKeyUpdateSerializer,
        AgentccAPIKeyCreateSerializer,
    ):
        ok = serializer_class(data={"name": "k", "log_content": "metadata"})
        assert ok.is_valid(), ok.errors
        assert ok.validated_data["log_content"] == "metadata"
        bad = serializer_class(data={"name": "k", "log_content": "loud"})
        assert not bad.is_valid()
        assert "log_content" in bad.errors


@pytest.mark.parametrize("field", sorted(UNENFORCED_LIMITS))
@pytest.mark.parametrize("where", ["top-level", "metadata"])
def test_serializers_refuse_unenforced_limits(field, where):
    data = {"name": "k"}
    if where == "metadata":
        data["metadata"] = {field: "60"}
    else:
        data[field] = UNENFORCED_LIMITS[field]
    for serializer_class in (
        AgentccAPIKeyUpdateSerializer,
        AgentccAPIKeyCreateSerializer,
    ):
        serializer = serializer_class(data=data)
        assert not serializer.is_valid()
        [detail] = serializer.errors["non_field_errors"]
        assert detail.code == NOT_ENFORCED
        assert field in str(detail)


@pytest.mark.integration
@pytest.mark.parametrize("reserved", ["log_content", "org_id"])
def test_raw_metadata_cannot_carry_key_settings_or_org(
    auth_client, api_key, gateway, reserved
):
    response = _patch(auth_client, api_key, {"metadata": {reserved: "x"}})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] != NOT_ENFORCED
    assert "metadata" in response.json()["result"]
    gateway.update_key.assert_not_called()


@pytest.mark.integration
def test_raw_metadata_keeps_key_settings_and_org(auth_client, api_key, gateway):
    assert _patch(auth_client, api_key, {"log_content": "none"}).status_code == 200

    response = _patch(auth_client, api_key, {"metadata": {"unit": "search"}})

    assert response.status_code == status.HTTP_200_OK, response.json()
    api_key.refresh_from_db()
    assert api_key.metadata == {
        "unit": "search",
        "log_content": "none",
        "org_id": str(api_key.organization_id),
    }
    assert _sent_metadata(gateway) == api_key.metadata


@pytest.mark.integration
def test_raw_metadata_update_binds_the_keys_own_org(
    auth_client, organization, workspace, gateway
):
    # A raw replace keeps what the caller sent, and the update binds org_id to
    # the key's own organization: update_key assigns the row's org, so the
    # gateway never holds a key with a missing or tenant-chosen org.
    key = AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        gateway_key_id=f"gw-key-{uuid.uuid4().hex[:8]}",
        key_prefix="pk-key",
        name="no-org-key",
        metadata={"before": True},
    )

    response = _patch(auth_client, key, {"metadata": {"updated": True}})

    assert response.status_code == status.HTTP_200_OK, response.json()
    bound = {"updated": True, "org_id": str(organization.id)}
    assert _sent_metadata(gateway) == bound
    key.refresh_from_db()
    assert key.metadata == bound
    assert auth_bridge.gateway_key_payload(key)["metadata"]["org_id"] == str(
        organization.id
    )


@pytest.mark.integration
class TestMemberCannotWriteApiKeys:
    """Members read API keys; every write is Owner or Admin only."""

    def test_member_cannot_write_api_keys(self, member_client, api_key, gateway):
        writes = {
            "create": member_client.post(KEYS, {"name": "member-key"}, format="json"),
            "update": member_client.put(
                f"{KEYS}{api_key.id}/", {"name": "renamed"}, format="json"
            ),
            "partial update": _patch(member_client, api_key, {"name": "renamed"}),
            "delete": member_client.delete(f"{KEYS}{api_key.id}/"),
            "revoke": member_client.post(f"{KEYS}{api_key.id}/revoke/"),
            "sync": member_client.post(f"{KEYS}sync/"),
        }

        for action, response in writes.items():
            assert response.status_code == status.HTTP_403_FORBIDDEN, action
        api_key.refresh_from_db()
        assert api_key.name == "content-mode-key"
        assert api_key.status == AgentccAPIKey.ACTIVE
        assert not api_key.deleted
        gateway.create_key.assert_not_called()
        gateway.update_key.assert_not_called()
        gateway.revoke_key.assert_not_called()
        gateway.list_keys.assert_not_called()

    def test_member_still_reads_api_keys(self, member_client, api_key):
        listed = member_client.get(KEYS)
        assert listed.status_code == status.HTTP_200_OK
        assert str(api_key.id) in [k["id"] for k in listed.json()["result"]]
        assert (
            member_client.get(f"{KEYS}{api_key.id}/").status_code == status.HTTP_200_OK
        )

    def test_admin_can_write_api_keys(self, client_with_role, api_key, gateway):
        admin = client_with_role(Level.ADMIN, OrganizationRoles.ADMIN)

        response = _patch(admin, api_key, {"name": "admin-renamed"})

        assert response.status_code == status.HTTP_200_OK, response.json()
        api_key.refresh_from_db()
        assert api_key.name == "admin-renamed"
