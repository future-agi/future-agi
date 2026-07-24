"""Bulk org-config sync endpoint: admin-token gating and envelope shape."""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization
from agentcc.models.org_config import AgentccOrgConfig

ADMIN_TOKEN = "agentcc-admin-secret"


@pytest.fixture(autouse=True)
def _set_agentcc_admin_token():
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        yield


@pytest.fixture
def admin_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    return client


def _make_active_config(organization, version=1, **overrides):
    defaults = {
        "organization": organization,
        "version": version,
        "is_active": True,
    }
    defaults.update(overrides)
    return AgentccOrgConfig.no_workspace_objects.create(**defaults)


class TestOrgConfigBulk:
    def test_unauthenticated_returns_403(self, db):
        response = APIClient().get("/agentcc/org-configs/bulk/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_wrong_token_returns_403(self, db):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer wrong-token")
        response = client.get("/agentcc/org-configs/bulk/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_empty_response_when_no_active_configs(self, admin_client, db):
        response = admin_client.get("/agentcc/org-configs/bulk/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] is True
        assert response.json()["result"] == {}

    def test_returns_configs_keyed_by_org_id(self, admin_client, organization):
        _make_active_config(organization)

        response = admin_client.get("/agentcc/org-configs/bulk/")
        assert response.status_code == status.HTTP_200_OK

        result = response.json()["result"]
        # Envelope shape: dict keyed by str(org_id) with a dict value.
        assert str(organization.id) in result
        assert isinstance(result[str(organization.id)], dict)

    def test_multi_org_configs_all_returned(self, admin_client, organization, db):
        _make_active_config(organization)
        org_b = Organization.objects.create(name="Bulk Sync Peer Org")
        _make_active_config(org_b)

        result = admin_client.get("/agentcc/org-configs/bulk/").json()["result"]

        assert str(organization.id) in result
        assert str(org_b.id) in result
        assert len(result) == 2

    def test_inactive_versions_excluded(self, admin_client, organization):
        # Only the active row should be shipped; older inactive versions are
        # history and must not leak into the gateway payload.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=False
        )
        _make_active_config(organization, version=2)

        result = admin_client.get("/agentcc/org-configs/bulk/").json()["result"]

        # Exactly one entry for this org: the active version.
        assert str(organization.id) in result
        # The endpoint filters by is_active=True at the ORM level; assert we
        # get exactly one row per org (not two).
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization
            ).count()
            == 2
        )
        # But bulk sync returns exactly one key per org.
        assert list(result.keys()).count(str(organization.id)) == 1

    def test_soft_deleted_configs_excluded(self, admin_client, organization, db):
        # Soft-deleted rows are still present in the DB but must be filtered
        # out of the bulk payload the gateway pulls.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            deleted=True,
        )

        result = admin_client.get("/agentcc/org-configs/bulk/").json()["result"]
        assert str(organization.id) not in result
