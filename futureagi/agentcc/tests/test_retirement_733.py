"""733 F1/F2: negative sync, owner membership and failed last-key revoke."""

import hashlib
import secrets
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClientError

pytestmark = pytest.mark.django_db


def make_key(organization, user=None, **kwargs):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        user=user,
        gateway_key_id=secrets.token_hex(12),
        name="733-test",
        key_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
        **kwargs,
    )


def bulk(header=None):
    token = secrets.token_urlsafe(48)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", token):
        return client.get(
            "/agentcc/api-keys/bulk/",
            **(
                {"HTTP_X_AGENTCC_CONFIG_KEY_HASHES": header}
                if header is not None
                else {}
            ),
        )


def test_ownerless_is_not_loadable(organization):
    key = make_key(organization)
    assert not auth_bridge.gateway_loadable_keys().filter(pk=key.pk).exists()


def test_payload_uses_actual_person_not_metadata(organization, user):
    key = make_key(organization, user, metadata={"user_id": "spoof"})
    assert auth_bridge.gateway_key_payload(key)["metadata"]["user_id"] == str(user.pk)


def test_membership_removed_not_loadable(organization, user):
    key = make_key(organization, user)
    OrganizationMembership.objects.filter(user=user, organization=organization).update(
        is_active=False
    )
    assert not auth_bridge.gateway_loadable_keys().filter(pk=key.pk).exists()


def test_bulk_retired_deleted_and_persistent_conflicts(organization):
    key = make_key(organization, status=AgentccAPIKey.REVOKED, deleted=True)
    # The 7-day window applies to retired only, never to config-conflict checks.
    AgentccAPIKey._base_manager.filter(pk=key.pk).update(
        updated_at=timezone.now() - timedelta(days=8)
    )
    body = bulk(key.key_hash.upper()).json()
    assert body["snapshot"] == {"complete": True, "issued_ever": 1}
    assert body["retired"] == []
    assert body["config_conflicts"] == [key.key_hash]


def test_bulk_retired_recent_ownerless(organization):
    key = make_key(organization)
    body = bulk().json()
    assert body["result"] == []
    assert body["retired"] == [{"id": key.gateway_key_id, "key_hash": key.key_hash}]


@pytest.mark.parametrize("header", ["nope", "a" * 63, ",", ",".join(["a" * 64] * 33)])
def test_bulk_rejects_bad_config_hashes(header):
    assert bulk(header).status_code == 400


def test_last_key_failed_offboarding_reports_pending(organization, user):
    key = make_key(organization, user)
    client = MagicMock()
    client.revoke_key.side_effect = GatewayClientError("unavailable", status_code=503)
    client.list_keys.return_value = {
        "data": [{"id": key.gateway_key_id, "status": "active"}]
    }
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        report = auth_bridge.revoke_person_keys(user, organization)
    key.refresh_from_db()
    assert key.status == AgentccAPIKey.REVOKED
    assert report["pending"] == [key.gateway_key_id]
    assert report["message"] == "gateway revocation pending"
    client.list_keys.assert_called_once()
    assert {"id": key.gateway_key_id, "key_hash": key.key_hash} in bulk().json()[
        "retired"
    ]


@pytest.mark.parametrize(
    "body", [None, [], {}, {"data": None}, {"data": [{"id": 1, "status": "revoked"}]}]
)
def test_malformed_revocation_readback_is_pending(organization, user, body):
    key = make_key(organization, user)
    client = MagicMock()
    client.list_keys.return_value = body
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        report = auth_bridge.revoke_person_keys(user, organization)
    assert report["pending"] == [key.gateway_key_id]
    assert report["message"] == "gateway revocation pending"
    key.refresh_from_db()
    assert key.status == AgentccAPIKey.REVOKED


def test_sync_does_not_adopt_ownerless(organization):
    client = MagicMock()
    client.list_keys.return_value = {
        "data": [
            {
                "id": "ownerless",
                "metadata": {"org_id": str(organization.pk)},
                "status": "active",
            }
        ]
    }
    with patch.object(auth_bridge, "get_gateway_client", return_value=client):
        auth_bridge.sync_keys(organization)
    assert not AgentccAPIKey.no_workspace_objects.filter(
        gateway_key_id="ownerless"
    ).exists()
