"""Managed models (FutureAGI-supplied provider keys, billed with a markup) and
the gateway's model "auto": the org opt-in, routing.auto validation, the
payload pushed to the gateway, and the console endpoints."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload
from agentcc.services.gateway_client import GatewayClientError

PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
CLIENT = "agentcc.views.managed_models.get_gateway_client"


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


@pytest.mark.integration
@pytest.mark.api
class TestOrgOptIn:
    @patch(PUSH, return_value=True)
    def test_opt_in_is_saved_and_pushed(self, _push, auth_client, user):
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"managed_models": {"enabled": True}},
            format="json",
        )

        assert response.status_code == 200, response.json()
        config = _active(user.organization)
        assert config.managed_models == {"enabled": True}
        payload = _build_payload(str(user.organization.id), config)
        assert payload["managed_models"] == {"enabled": True}

    @patch(PUSH, return_value=True)
    @pytest.mark.parametrize(
        "managed_models",
        [{"enabled": "yes"}, {"enabled": True, "markup_percent": 0}, ["enabled"]],
    )
    def test_opt_in_rejects_anything_but_enabled(
        self, _push, auth_client, managed_models
    ):
        """An org can opt in or out; it cannot set its own markup."""
        response = auth_client.post(
            "/agentcc/org-configs/",
            {"managed_models": managed_models},
            format="json",
        )

        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestAutoRoutingConfig:
    @patch(PUSH, return_value=True)
    def test_auto_config_is_saved_and_pushed(self, _push, auth_client, user):
        auto = {
            "preference": "quality",
            "candidates": ["anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"],
            "max_output_per_mtok": 20,
        }
        response = auth_client.post(
            "/agentcc/org-configs/", {"routing": {"auto": auto}}, format="json"
        )

        assert response.status_code == 200, response.json()
        payload = _build_payload(str(user.organization.id), _active(user.organization))
        assert payload["routing"]["auto"] == auto

    @patch(PUSH, return_value=True)
    @pytest.mark.parametrize(
        "auto",
        [
            {"preference": "fastest"},
            {"candidates": "openai/gpt-6-sol"},
            {"candidates": [""]},
            {"max_input_per_mtok": -1},
            {"max_output_per_mtok": True},
            {"tiers": {}},
        ],
    )
    def test_invalid_auto_config_is_rejected(self, _push, auth_client, auto):
        response = auth_client.post(
            "/agentcc/org-configs/", {"routing": {"auto": auto}}, format="json"
        )

        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
class TestManagedModelsEndpoints:
    def test_status_reports_opt_in_and_balance(self, auth_client, user):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            managed_models={"enabled": True},
        )
        client = MagicMock()
        client.get_org_credits.return_value = {"balance_usd": 12.5, "markup_percent": 5}
        with patch(CLIENT, return_value=client):
            response = auth_client.get("/agentcc/managed-models/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == {
            "available": True,
            "enabled": True,
            "balance_usd": 12.5,
            "markup_percent": 5,
        }
        client.get_org_credits.assert_called_once_with(str(user.organization.id))

    def test_status_when_the_gateway_offers_no_managed_models(self, auth_client):
        client = MagicMock()
        client.get_org_credits.side_effect = GatewayClientError("not found", 404)
        with patch(CLIENT, return_value=client):
            response = auth_client.get("/agentcc/managed-models/")

        assert response.status_code == 200
        assert response.json()["result"]["available"] is False
        assert response.json()["result"]["enabled"] is False

    def test_catalog_is_proxied_and_cached(self, auth_client):
        from django.core.cache import cache

        from agentcc.views.managed_models import CATALOG_CACHE_KEY

        cache.delete(CATALOG_CACHE_KEY)
        catalog = {
            "markup_percent": 5,
            "providers": [],
            "models": [{"id": "anthropic/claude-sonnet-5.5", "provider": "openrouter"}],
        }
        client = MagicMock()
        client.get_catalog.return_value = catalog
        with patch(CLIENT, return_value=client):
            first = auth_client.get("/agentcc/managed-models/catalog/")
            second = auth_client.get("/agentcc/managed-models/catalog/")

        assert first.status_code == 200, first.json()
        assert first.json()["result"] == catalog
        assert second.json()["result"] == catalog
        client.get_catalog.assert_called_once()
        cache.delete(CATALOG_CACHE_KEY)

    def test_endpoints_need_authentication(self, api_client):
        assert api_client.get("/agentcc/managed-models/").status_code in (401, 403)
        assert api_client.get("/agentcc/managed-models/catalog/").status_code in (
            401,
            403,
        )


@pytest.mark.integration
@pytest.mark.api
class TestUpdateConfigPatches:
    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_nulls_in_a_new_section_are_dropped_not_stored(
        self, _push, auth_client, user
    ):
        """A null deletes a key. In a section the config does not have yet it
        must not be stored either: a stored null fails validation on every
        later save of the org config."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=user.organization,
            version=1,
            is_active=True,
            routing={"strategy": "round_robin"},
        )

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {
                "routing": {"auto": {"preference": "cost", "candidates": None}},
                "managed_models": {"enabled": True},
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        config = _active(user.organization)
        assert config.routing == {
            "strategy": "round_robin",
            "auto": {"preference": "cost"},
        }
        assert config.managed_models == {"enabled": True}

        with patch(PUSH, return_value=True):
            resave = auth_client.post(
                "/agentcc/org-configs/", {"routing": config.routing}, format="json"
            )
        assert resave.status_code == 200, resave.json()
