"""Bulk API-key sync endpoint: expires_at wire format and expired-key filtering."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models import AgentccAPIKey
from agentcc.serializers.contracts import (
    APIKeyBulkItemSerializer,
    APIKeyBulkResponseSerializer,
)

ADMIN_TOKEN = "agentcc-admin-secret"


@pytest.fixture(autouse=True)
def set_agentcc_admin_token():
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        yield


@pytest.fixture
def admin_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    return client


def _make_key(organization, workspace, gateway_key_id, key_hash, expires_at):
    return AgentccAPIKey.objects.create(
        gateway_key_id=gateway_key_id,
        name=gateway_key_id,
        organization=organization,
        workspace=workspace,
        key_hash=key_hash,
        status=AgentccAPIKey.ACTIVE,
        expires_at=expires_at,
    )


class TestAPIKeyBulkExpiresAt:
    def test_future_and_null_expiry_are_serialized_on_the_wire(
        self, admin_client, organization, workspace
    ):
        future = timezone.now() + timedelta(days=7)
        _make_key(organization, workspace, "gw-expiring", "a" * 64, future)
        _make_key(organization, workspace, "gw-perpetual", "b" * 64, None)

        response = admin_client.get("/agentcc/api-keys/bulk/")
        assert response.status_code == status.HTTP_200_OK

        # Assert on the rendered JSON the gateway actually receives, not the
        # pre-render Python datetime.
        by_id = {item["id"]: item for item in response.json()["result"]}

        # Future-expiry key: expires_at is an ISO 8601 string the gateway can
        # parse as RFC3339 (DRF renders aware datetimes with a 'Z'/offset).
        expiring = by_id["gw-expiring"]["expires_at"]
        assert isinstance(expiring, str)
        parsed = datetime.fromisoformat(expiring.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None  # must be timezone-aware

        # Null expiry stays null (never-expiring keys).
        assert by_id["gw-perpetual"]["expires_at"] is None

    def test_expired_keys_are_not_shipped(
        self, admin_client, organization, workspace
    ):
        past = timezone.now() - timedelta(days=1)
        _make_key(organization, workspace, "gw-expired", "c" * 64, past)
        _make_key(organization, workspace, "gw-live", "d" * 64, None)

        ids = {item["id"] for item in admin_client.get(
            "/agentcc/api-keys/bulk/"
        ).json()["result"]}

        assert "gw-expired" not in ids  # filtered at the query level
        assert "gw-live" in ids


class TestAPIKeyBulkPayload:
    def test_restored_keys_keep_their_id_prefix_and_org(
        self, admin_client, organization, workspace
    ):
        key = _make_key(organization, workspace, "key_2", "e" * 64, None)
        key.key_prefix = "sk-agentcc-e..."
        key.save(update_fields=["key_prefix"])

        (item,) = [
            item
            for item in admin_client.get("/agentcc/api-keys/bulk/").json()["result"]
            if item["id"] == "key_2"
        ]

        assert item["key_hash"] == "e" * 64
        assert item["key_prefix"] == "sk-agentcc-e..."
        assert item["metadata"]["org_id"] == str(organization.id)

    def test_every_field_served_is_declared(
        self, admin_client, organization, workspace
    ):
        _make_key(organization, workspace, "key_3", "f" * 64, None)

        body = admin_client.get("/agentcc/api-keys/bulk/").json()

        for item in body["result"]:
            assert set(item) == set(APIKeyBulkItemSerializer().fields)
        response = APIKeyBulkResponseSerializer(data=body)
        assert response.is_valid(), response.errors
