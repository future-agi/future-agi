"""The top-level tools and identity sections are stored and pushed.

Both are in the gateway contract and the gateway's tenant parsing; Django stores
them (prism 0045), lists them among the writer's sections, serializes them and
pushes them. The stored identity has no secret: the IdP client secret and the
delegated grants are push-only, so a save that carries either is refused.
"""

import secrets
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
ORG_CONFIGS = "/agentcc/org-configs/"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
# A random token: IsAdminToken refuses a short, placeholder or published one
# (agentcc/admin_token.py), as the gateway does at start-up.
ADMIN_TOKEN = secrets.token_hex(32)

TOOLS = {
    "capabilities": {
        "search": {"enabled": True, "providers": ["exa"], "max_cost_usd_per_call": 0.05}
    },
    "policy": {"zdr_required": True, "domains": {"deny": ["example.com"]}},
}
IDENTITY = {
    "require_human_sponsor": True,
    "grants": {"max_ttl_days": 30},
    "idp": {
        "issuer": "https://idp.example.com",
        "token_endpoint": "https://idp.example.com/token",
        "client_id": "gateway",
    },
}


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _bulk(organization):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"][str(organization.id)]


@pytest.mark.integration
@pytest.mark.api
@patch(SAVE_PUSH, return_value=True)
@patch("agentcc.views.gateway.push_org_config", return_value=True)
@patch("agentcc.services.config_push._assemble_providers", return_value={})
class TestToolsAndIdentityStorage:
    def test_update_config_stores_and_pushes_tools_and_identity(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        response = auth_client.post(
            UPDATE_CONFIG, {"tools": TOOLS, "identity": IDENTITY}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert active.tools == TOOLS
        assert active.identity == IDENTITY
        payload = _build_payload(str(organization.id), active)
        assert payload["tools"]["policy"]["zdr_required"] is True
        assert payload["identity"]["idp"]["client_id"] == "gateway"
        bulk = _bulk(organization)
        assert bulk["tools"] == payload["tools"]
        assert bulk["identity"] == payload["identity"]

    def test_post_org_configs_stores_both_and_activate_keeps_them(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        first = auth_client.post(
            ORG_CONFIGS, {"tools": TOOLS, "identity": IDENTITY}, format="json"
        )
        assert first.status_code == status.HTTP_200_OK, first.json()
        v1 = _active(organization)
        assert (v1.tools, v1.identity) == (TOOLS, IDENTITY)
        assert first.json()["result"]["tools"] == TOOLS
        assert first.json()["result"]["identity"] == IDENTITY

        second = auth_client.post(ORG_CONFIGS, {"tools": {}}, format="json")
        assert second.status_code == status.HTTP_200_OK, second.json()
        assert _active(organization).tools == {}
        assert _active(organization).identity == IDENTITY  # not named: copied

        rollback = auth_client.post(f"{ORG_CONFIGS}{v1.id}/activate/", format="json")
        assert rollback.status_code == status.HTTP_200_OK, rollback.json()
        active = _active(organization)
        assert active.pk == v1.pk
        assert (active.tools, active.identity) == (TOOLS, IDENTITY)
        assert _build_payload(str(organization.id), active)["tools"] == TOOLS

    @pytest.mark.parametrize(
        "identity",
        [
            {"idp": {**IDENTITY["idp"], "client_secret": "s3cret"}},
            {"idp": {**IDENTITY["idp"], "clientSecret": "s3cret"}},
            {
                "delegated_grants": {
                    "g1": {
                        "grant_id": "g1",
                        "agent_id": "a",
                        "grantor_user_id": "u",
                        "scopes": {},
                        "expires_at": "2027-01-01T00:00:00Z",
                    }
                }
            },
        ],
    )
    def test_push_only_identity_secrets_are_refused_on_every_path(
        self, _providers, _gw_push, _push, auth_client, organization, identity
    ):
        for url in (UPDATE_CONFIG, ORG_CONFIGS):
            response = auth_client.post(url, {"identity": identity}, format="json")
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (url, identity)
            assert "secret_in_config" in str(response.json()), response.json()
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()

    def test_identity_off_contract_is_refused(
        self, _providers, _gw_push, _push, auth_client, organization
    ):
        response = auth_client.post(
            ORG_CONFIGS, {"identity": {"grants": {"max_ttl_days": 0}}}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
