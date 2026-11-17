"""update-config answers a rejected config with the rejection's own code.

A typed `OrgConfigRejected` (for example `SecretInConfig`) carries a `code`
the console can act on; update-config returns it, as POST org-configs/ does.
A rejection without a code keeps the generic 400 code.
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from tfc.utils.api_errors import ApiErrorCode

URL = "/agentcc/gateways/default/update-config/"

UPSTREAM_KEYS = {
    "openrouter": {
        "api_key": "upstream-key-test-value",
        "key_hash": "kh_3b9e",
        "label": "acme-prod",
    }
}


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestUpdateConfigErrorCodes:
    @pytest.mark.parametrize(
        "patch_body",
        [
            {"managed_models": {"upstream_keys": UPSTREAM_KEYS}},
            {
                "routing": {
                    "rollout": {"managed_models": {"upstreamKeys": UPSTREAM_KEYS}}
                }
            },
        ],
        ids=["managed_models", "rollout_overlay"],
    )
    def test_update_config_returns_secret_in_config_code(
        self, push, auth_client, organization, patch_body
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            managed_models={"enabled": True},
        )

        response = auth_client.post(URL, patch_body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["code"] == "secret_in_config"
        assert "upstream_keys" in body["message"] or "upstreamKeys" in body["message"]
        assert "upstream-key-test-value" not in response.content.decode()
        assert _versions(organization) == [(1, True)]
        push.assert_not_called()

    def test_a_rejection_without_a_code_keeps_the_generic_code(
        self, push, auth_client, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        response = auth_client.post(
            URL, {"routing": {"default_strategy": "round-robin"}}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["code"] == ApiErrorCode.INVALID.value
        assert "routing.default_strategy" in body["message"]
        assert _versions(organization) == [(1, True)]
