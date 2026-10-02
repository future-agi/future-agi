"""Coverage for the /agentcc/routing-policies/ endpoints."""

from unittest.mock import patch

import pytest

from agentcc.models.routing_policy import AgentccRoutingPolicy


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
        rows = response.json()["result"]
        assert len(rows) == 3

    def test_list_active_only_filter(self, auth_client, user):
        _make_policy(user, name="chatty", version=1, is_active=False)
        _make_policy(user, name="chatty", version=2, is_active=True)

        response = auth_client.get(
            "/agentcc/routing-policies/?active_only=true"
        )
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
        response = auth_client.get(
            f"/agentcc/routing-policies/{policy.id}/"
        )
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(policy.id)


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyCreate:
    @patch(
        "agentcc.views.routing_policy.AgentccRoutingPolicyViewSet._sync_routing_to_gateway",
        return_value=True,
    )
    def test_create_new_version_deactivates_prior(
        self, mock_sync, auth_client, user
    ):
        prior = _make_policy(user, name="fastest", version=1, is_active=True)

        response = auth_client.post(
            "/agentcc/routing-policies/",
            {"name": "fastest", "config": {"strategy": "fastest"}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        prior.refresh_from_db()
        assert prior.is_active is False
        new_row = AgentccRoutingPolicy.no_workspace_objects.get(
            organization=user.organization,
            name="fastest",
            is_active=True,
            deleted=False,
        )
        assert new_row.version == 2
        mock_sync.assert_called_once()


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
    @patch(
        "agentcc.views.routing_policy.AgentccRoutingPolicyViewSet._sync_routing_to_gateway",
        return_value=True,
    )
    def test_destroy_soft_deletes_and_syncs(self, mock_sync, auth_client, user):
        policy = _make_policy(user, name="to-delete")
        response = auth_client.delete(
            f"/agentcc/routing-policies/{policy.id}/"
        )
        assert response.status_code == 200
        assert response.json()["result"]["deleted"] is True
        mock_sync.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicyActivate:
    @patch(
        "agentcc.views.routing_policy.AgentccRoutingPolicyViewSet._sync_routing_to_gateway",
        return_value=True,
    )
    def test_activate_swaps_active_within_same_name(
        self, mock_sync, auth_client, user
    ):
        v1 = _make_policy(user, name="fastest", version=1, is_active=True)
        v2 = _make_policy(user, name="fastest", version=2, is_active=False)

        response = auth_client.post(
            f"/agentcc/routing-policies/{v2.id}/activate/"
        )
        assert response.status_code == 200, response.json()
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_active is False
        assert v2.is_active is True
        mock_sync.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
class TestRoutingPolicySync:
    @patch(
        "agentcc.views.routing_policy.AgentccRoutingPolicyViewSet._sync_routing_to_gateway",
        return_value=True,
    )
    def test_sync_triggers_gateway_push(self, mock_sync, auth_client, user):
        _make_policy(user, name="fastest", is_active=True)
        response = auth_client.post("/agentcc/routing-policies/sync/")
        assert response.status_code == 200
        assert response.json()["result"]["synced"] is True
        mock_sync.assert_called_once()
