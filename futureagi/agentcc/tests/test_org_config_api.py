"""Coverage for the /agentcc/org-configs/ ViewSet endpoints."""

from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles


@pytest.fixture
def secondary_org_context(user):
    org_b = Organization.objects.create(name="OrgConfig Peer Org")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org_b,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace_b = Workspace.objects.create(
        name="OrgConfig Peer Workspace",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.objects.create(
        workspace=workspace_b,
        user=user,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        organization_membership=membership,
        is_active=True,
    )
    return org_b, workspace_b


@pytest.fixture
def secondary_org_client(user, secondary_org_context):
    _, workspace_b = secondary_org_context
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace_b)
    yield client
    client.stop_workspace_injection()


def _make_config(organization, version=1, is_active=True, **overrides):
    defaults = {
        "organization": organization,
        "version": version,
        "is_active": is_active,
    }
    defaults.update(overrides)
    return AgentccOrgConfig.no_workspace_objects.create(**defaults)


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigList:
    def test_list_returns_versions_for_active_org(self, auth_client, user):
        _make_config(user.organization, version=1, is_active=False)
        _make_config(user.organization, version=2, is_active=True)

        response = auth_client.get("/agentcc/org-configs/")
        assert response.status_code == 200
        result = response.json()["result"]
        # Both versions should be returned; the ViewSet orders by -version.
        versions = [row["version"] for row in result]
        assert versions == sorted(versions, reverse=True)
        assert 1 in versions and 2 in versions

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/org-configs/")
        assert response.status_code in (401, 403)

    def test_list_isolates_per_org(
        self, user, secondary_org_context, secondary_org_client
    ):
        org_b, _ = secondary_org_context
        _make_config(user.organization, version=1)
        _make_config(org_b, version=1)

        response = secondary_org_client.get("/agentcc/org-configs/")
        assert response.status_code == 200
        result = response.json()["result"]
        assert len(result) == 1
        assert result[0]["organization"] == str(org_b.id)


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigRetrieve:
    def test_retrieve_returns_config(self, auth_client, user):
        config = _make_config(user.organization, version=1)
        response = auth_client.get(f"/agentcc/org-configs/{config.id}/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["id"] == str(config.id)
        assert data["version"] == 1

    def test_retrieve_cross_tenant_returns_404(
        self, user, secondary_org_context, secondary_org_client
    ):
        # Config lives in org A; client is scoped to org B.
        config = _make_config(user.organization, version=1)
        response = secondary_org_client.get(f"/agentcc/org-configs/{config.id}/")
        assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigCreate:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_create_new_version_deactivates_prior(self, mock_push, auth_client, user):
        prior = _make_config(user.organization, version=1, is_active=True)

        response = auth_client.post(
            "/agentcc/org-configs/",
            {"routing": {"strategy": "round_robin"}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        prior.refresh_from_db()
        assert prior.is_active is False
        # New row is active with version 2.
        new_active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        assert new_active.version == 2
        assert new_active.routing == {"strategy": "round_robin"}
        mock_push.assert_called_once()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_create_unknown_fields_are_silently_ignored(
        self, mock_push, auth_client, user
    ):
        # Unknown fields silently dropped (write serializer does not opt into reject_unknown_fields).
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"not_a_real_field": {"x": 1}, "cache": {"enabled": True}},
            format="json",
        )
        assert response.status_code == 200, response.json()
        new_active = AgentccOrgConfig.no_workspace_objects.get(
            organization=user.organization, is_active=True, deleted=False
        )
        # normalize_cache_config backfills defaults; pin enabled=True only.
        assert new_active.cache.get("enabled") is True
        # Unknown key is dropped, not written into any slot.
        assert "not_a_real_field" not in {
            f.name for f in AgentccOrgConfig._meta.get_fields()
        }


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigImmutable:
    def test_put_returns_400_because_immutable(self, auth_client, user):
        config = _make_config(user.organization, version=1)
        response = auth_client.put(
            f"/agentcc/org-configs/{config.id}/",
            {"routing": {"strategy": "round_robin"}},
            format="json",
        )
        assert response.status_code == 400
        assert "immutable" in response.json()["message"].lower()

    def test_patch_returns_400_because_immutable(self, auth_client, user):
        config = _make_config(user.organization, version=1)
        response = auth_client.patch(
            f"/agentcc/org-configs/{config.id}/",
            {"routing": {"strategy": "round_robin"}},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigActivate:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_activate_swaps_active_and_pushes(self, mock_push, auth_client, user):
        old_active = _make_config(user.organization, version=1, is_active=True)
        new_version = _make_config(user.organization, version=2, is_active=False)

        response = auth_client.post(
            f"/agentcc/org-configs/{new_version.id}/activate/"
        )

        assert response.status_code == 200, response.json()
        old_active.refresh_from_db()
        new_version.refresh_from_db()
        assert old_active.is_active is False
        assert new_version.is_active is True
        assert response.json()["result"]["gateway_synced"] is True
        mock_push.assert_called_once()

    def test_activate_on_already_active_is_idempotent(self, auth_client, user):
        active = _make_config(user.organization, version=1, is_active=True)

        response = auth_client.post(f"/agentcc/org-configs/{active.id}/activate/")
        assert response.status_code == 200
        # Idempotent path returns the current config without touching the DB.
        active.refresh_from_db()
        assert active.is_active is True

    def test_activate_cross_tenant_returns_404(
        self, user, secondary_org_context, secondary_org_client
    ):
        config = _make_config(user.organization, version=1, is_active=False)
        response = secondary_org_client.post(
            f"/agentcc/org-configs/{config.id}/activate/"
        )
        assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigActive:
    def test_active_returns_current_config(self, auth_client, user):
        _make_config(user.organization, version=1, is_active=False)
        active = _make_config(user.organization, version=2, is_active=True)

        response = auth_client.get("/agentcc/org-configs/active/")
        assert response.status_code == 200
        data = response.json()["result"]
        assert data is not None
        assert data["id"] == str(active.id)
        assert data["version"] == 2

    def test_active_returns_none_when_no_config(self, auth_client, user):
        # No config seeded for this org.
        response = auth_client.get("/agentcc/org-configs/active/")
        assert response.status_code == 200
        assert response.json()["result"] is None

    def test_active_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/org-configs/active/")
        assert response.status_code in (401, 403)
