"""Decision providers saved under Gateway > Providers reach the gateway intact.

The saves, the push and what the gateway builds from it are shared with the
gateway's own test (TestDecisionProvidersPushedByTheBackend), so the frontend
presets, the backend and the gateway cannot drift apart unnoticed.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.serializers.provider_credential import (
    AgentccProviderCredentialCreateSerializer,
    AgentccProviderCredentialUpdateSerializer,
)
from integrations.services.credentials import CredentialManager

CASES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "api_contracts/gateway/decision-providers.json"
    ).read_text()
)


@pytest.fixture
def gateway():
    """The gateway client config_push sends to, recording every push."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _pushed_providers(gateway, organization):
    org_id, payload = gateway.set_org_config.call_args.args
    assert org_id == str(organization.id)
    return payload["providers"]


@pytest.mark.integration
@pytest.mark.api
class TestDecisionProvidersReachTheGateway:
    @pytest.fixture(autouse=True)
    def dns(self, provider_dns):
        return provider_dns(CASES["dns"])

    def test_preset_saves_are_pushed_intact(self, auth_client, organization, gateway):
        for save in CASES["saves"]:
            response = auth_client.post(
                "/agentcc/gateways/default/update-provider/", save, format="json"
            )
            assert response.status_code == 200, response.json()
            assert response.json()["result"]["gateway_synced"] is True

        assert _pushed_providers(gateway, organization) == CASES["pushed"]["providers"]
        for save in CASES["saves"]:
            credential = AgentccProviderCredential.no_workspace_objects.get(
                organization=organization, provider_name=save["name"], deleted=False
            )
            assert credential.api_format == "systemone"
            assert credential.base_url == save["config"]["base_url"]
            assert credential.models_list == save["config"]["models"]
            assert credential.extra_config == {}

    def test_resaving_without_a_new_key_keeps_the_format_and_key(
        self, auth_client, organization, gateway
    ):
        save = CASES["saves"][0]
        auth_client.post(
            "/agentcc/gateways/default/update-provider/", save, format="json"
        )
        edit = {
            "name": save["name"],
            "config": {k: v for k, v in save["config"].items() if k != "api_key"}
            | {"models": ["jev-1.13.0"]},
        }
        response = auth_client.post(
            "/agentcc/gateways/default/update-provider/", edit, format="json"
        )

        assert response.status_code == 200, response.json()
        pushed = _pushed_providers(gateway, organization)[save["name"]]
        assert pushed == {
            **CASES["pushed"]["providers"][save["name"]],
            "models": ["jev-1.13.0"],
        }

    @pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
    def test_credential_api_accepts_and_pushes_a_decision_format(
        self, auth_client, organization, gateway, api_format
    ):
        response = auth_client.post(
            "/agentcc/provider-credentials/",
            {
                "provider_name": "typesafe",
                "credentials": {"api_key": "synthetic-typesafe-key"},
                "base_url": "https://api.typesafe.ai",
                "api_format": api_format,
                "models_list": ["jev-1.13.0", "jev-latest"],
            },
            format="json",
        )

        assert response.status_code == 201, response.json()
        assert response.json()["result"]["api_format"] == api_format
        assert _pushed_providers(gateway, organization)["typesafe"] == {
            **CASES["pushed"]["providers"]["typesafe"],
            "api_format": api_format,
        }


@pytest.mark.integration
@pytest.mark.api
class TestDecisionProvidersAreNeverProbed:
    """A listing call is not free on every decision vendor, so fetch_models
    answers for a System One provider without calling it."""

    @pytest.fixture(autouse=True)
    def no_vendor_call(self):
        with patch(
            "agentcc.views.provider_credential.AgentccProviderCredentialViewSet._fetch_models_from_provider"
        ) as fetch:
            yield fetch
        fetch.assert_not_called()

    @pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
    def test_unsaved_key(self, auth_client, api_format):
        response = auth_client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {
                "api_key": "synthetic-typesafe-key",
                "base_url": "https://api.typesafe.ai",
                "api_format": api_format,
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == {"models": []}

    def test_saved_credential_lists_what_was_saved(self, auth_client, organization):
        AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="perplexity-decider",
            encrypted_credentials=CredentialManager.encrypt(
                {"api_key": "synthetic-perplexity-key"}
            ),
            base_url="https://api.perplexity.ai",
            api_format="systemone",
            models_list=["pplx-decider-v1.1-27b"],
        )

        response = auth_client.post(
            "/agentcc/provider-credentials/fetch_models/",
            {"provider_name": "perplexity-decider"},
            format="json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["result"] == {"models": ["pplx-decider-v1.1-27b"]}


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
def test_credential_serializers_keep_a_decision_format(api_format):
    create = AgentccProviderCredentialCreateSerializer(
        data={
            "provider_name": "typesafe",
            "credentials": {"api_key": "synthetic-typesafe-key"},
            "base_url": "https://api.typesafe.ai",
            "api_format": api_format,
            "models_list": ["jev-latest"],
        }
    )
    update = AgentccProviderCredentialUpdateSerializer(
        data={"api_format": api_format}, partial=True
    )

    assert create.is_valid(), create.errors
    assert update.is_valid(), update.errors
    assert create.validated_data["api_format"] == api_format
    assert update.validated_data["api_format"] == api_format


@pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
@pytest.mark.parametrize("field", ["api_format", "apiFormat"])
def test_gateway_contract_carries_a_decision_format(api_format, field):
    pushed = CASES["pushed"]["providers"]["typesafe"]
    provider = {k: v for k, v in pushed.items() if k != "api_format"}
    provider[field] = api_format

    dumped = GatewayOrgConfig.model_validate(
        {"providers": {"typesafe": provider}}
    ).model_dump(by_alias=True, exclude_none=True)

    assert dumped["providers"]["typesafe"] == {**pushed, "api_format": api_format}
