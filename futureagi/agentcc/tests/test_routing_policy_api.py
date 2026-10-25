"""Coverage for the /agentcc/routing-policies/ endpoints.

Routing policies are retired: routing is versioned in org configs. The rows
stay readable and deprecated, nothing is pushed to the gateway, and
sync and activate answer 410 routing_policies_retired.
"""

from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.routing_policy import AgentccRoutingPolicy

PUSH = "agentcc.services.config_push.push_org_config"


def _make_policy(user, name="fastest", is_active=True, version=1, **overrides):
    # AgentccRoutingPolicy has no workspace field; it is org-scoped only.
    kwargs = {
        "organization": user.organization,
        "created_by": user,
        "name": name,
        "version": version,
        "is_active": is_active,
        "config": {"strategy": "fastest"},
    }
    kwargs.update(overrides)
    return AgentccRoutingPolicy.objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyList:
    def test_list_returns_all_versions(self, auth_client, user):
        _make_policy(user, name="chatty", version=1, is_active=False)
        _make_policy(user, name="chatty", version=2, is_active=True)
        _make_policy(user, name="prompty", version=1, is_active=True)

        response = auth_client.get("/agentcc/routing-policies/")
        assert response.status_code == 200
        assert response["Deprecation"] == "true"
        rows = response.json()["result"]
        assert len(rows) == 3

    def test_list_active_only_filter(self, auth_client, user):
        _make_policy(user, name="chatty", version=1, is_active=False)
        _make_policy(user, name="chatty", version=2, is_active=True)

        response = auth_client.get("/agentcc/routing-policies/?active_only=true")
        assert response.status_code == 200
        rows = response.json()["result"]
        assert len(rows) == 1
        assert rows[0]["version"] == 2

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/routing-policies/")
        assert response.status_code in (401, 403)


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyRetrieve:
    def test_retrieve_returns_policy(self, auth_client, user):
        policy = _make_policy(user, name="lookup-me")
        response = auth_client.get(f"/agentcc/routing-policies/{policy.id}/")
        assert response.status_code == 200
        assert response["Deprecation"] == "true"
        assert response.json()["result"]["id"] == str(policy.id)


def _org_config_versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active", "routing")
    )


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyCreate:
    @patch(PUSH, return_value=True)
    def test_create_new_version_deactivates_prior(self, mock_push, auth_client, user):
        prior = _make_policy(user, name="fastest", version=1, is_active=True)

        response = auth_client.post(
            "/agentcc/routing-policies/",
            {"name": "fastest", "config": {"strategy": "fastest"}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response["Deprecation"] == "true"
        prior.refresh_from_db()
        assert prior.is_active is False
        new_row = AgentccRoutingPolicy.no_workspace_objects.get(
            organization=user.organization,
            name="fastest",
            is_active=True,
            deleted=False,
        )
        assert new_row.version == 2
        assert "gateway_synced" not in response.json()["result"]
        mock_push.assert_not_called()

    @patch(PUSH, return_value=True)
    def test_create_leaves_the_org_config_alone(self, mock_push, auth_client, user):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            routing={"strategy": "round_robin", "auto": {"preference": "cost"}},
        )

        response = auth_client.post(
            "/agentcc/routing-policies/",
            {"name": "fastest", "config": {"strategy": "fastest"}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert _org_config_versions(user.organization) == [
            (1, True, {"strategy": "round_robin", "auto": {"preference": "cost"}})
        ]
        mock_push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyImmutable:
    def test_put_returns_400(self, auth_client, user):
        policy = _make_policy(user, name="frozen")
        response = auth_client.put(
            f"/agentcc/routing-policies/{policy.id}/",
            {"name": "frozen", "config": {"strategy": "round_robin"}},
            format="json",
        )
        assert response.status_code == 400
        assert "versioned" in response.json()["message"].lower()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyDestroy:
    @patch(PUSH, return_value=True)
    def test_destroy_soft_deletes_without_a_push(self, mock_push, auth_client, user):
        policy = _make_policy(user, name="to-delete")
        response = auth_client.delete(f"/agentcc/routing-policies/{policy.id}/")
        assert response.status_code == 200
        assert response.json()["result"] == {"deleted": True}
        assert response["Deprecation"] == "true"
        deleted = AgentccRoutingPolicy.all_objects.get(id=policy.id)
        assert deleted.deleted is True
        mock_push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyActivate:
    @patch(PUSH, return_value=True)
    def test_routing_policy_activate_returns_410(self, mock_push, auth_client, user):
        v1 = _make_policy(user, name="fastest", version=1, is_active=True)
        v2 = _make_policy(user, name="fastest", version=2, is_active=False)

        response = auth_client.post(f"/agentcc/routing-policies/{v2.id}/activate/")

        assert response.status_code == 410, response.json()
        assert response.json()["code"] == "routing_policies_retired"
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_active is True
        assert v2.is_active is False
        mock_push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicySync:
    @patch(PUSH, return_value=True)
    def test_routing_policy_sync_returns_410(self, mock_push, auth_client, user):
        _make_policy(user, name="fastest", is_active=True)
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            routing={"strategy": "round_robin"},
        )

        response = auth_client.post("/agentcc/routing-policies/sync/")

        assert response.status_code == 410, response.json()
        body = response.json()
        assert body["code"] == "routing_policies_retired"
        assert "org config" in body["message"].lower()
        assert _org_config_versions(user.organization) == [
            (1, True, {"strategy": "round_robin"})
        ]
        mock_push.assert_not_called()

    def test_sync_still_needs_authentication(self, api_client):
        response = api_client.post("/agentcc/routing-policies/sync/")
        assert response.status_code in (401, 403)
