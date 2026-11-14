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
class TestKeylessGenericSystemOneProviders:
    @pytest.fixture(autouse=True)
    def dns(self, provider_dns):
        return provider_dns(CASES["dns"])

    def _save(self, client, endpoint, config, name="custom-systemone"):
        if endpoint == "gateway":
            return client.post(
                "/agentcc/gateways/default/update-provider/",
                {"name": name, "config": config},
                format="json",
            )
        return client.post(
            "/agentcc/provider-credentials/",
            {
                "provider_name": name,
                "credentials": {"api_key": config["api_key"]}
                if "api_key" in config
                else {},
                "base_url": config.get("base_url", ""),
                "api_format": config.get("api_format", "openai"),
                "models_list": config.get("models", []),
            },
            format="json",
        )

    @pytest.mark.parametrize("endpoint", ["gateway", "credentials"])
    @pytest.mark.parametrize("api_format", ["systemone", "typesafe"])
    @pytest.mark.parametrize("key_config", [{}, {"api_key": ""}, {"api_key": "   "}])
    def test_create_and_update_push_a_keyless_generic_provider(
        self, auth_client, organization, gateway, endpoint, api_format, key_config
    ):
        config = {
            "base_url": "https://decider.example.com",
            "api_format": api_format,
            "models": ["strands-decider"],
            **key_config,
        }
        response = self._save(auth_client, endpoint, config)
        expected_status = 200 if endpoint == "gateway" else 201
        assert response.status_code == expected_status, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        credential = AgentccProviderCredential.no_workspace_objects.get(
            organization=organization, provider_name="custom-systemone", deleted=False
        )
        stored = CredentialManager.decrypt(bytes(credential.encrypted_credentials))
        assert not stored.get("api_key")
        pushed = _pushed_providers(gateway, organization)["custom-systemone"]
        assert pushed["enabled"] is True
        assert pushed["base_url"] == config["base_url"]
        assert pushed["api_format"] == api_format
        assert pushed["models"] == config["models"]
        assert not pushed.get("api_key")

        if endpoint == "gateway":
            response = self._save(
                auth_client, endpoint, config | {"models": ["new-decider"]}
            )
        else:
            response = auth_client.patch(
                f"/agentcc/provider-credentials/{credential.id}/",
                {"models_list": ["new-decider"]},
                format="json",
            )
        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = _pushed_providers(gateway, organization)["custom-systemone"]
        assert pushed["models"] == ["new-decider"]
        assert not pushed.get("api_key")

    @pytest.mark.parametrize("endpoint", ["gateway", "credentials"])
    @pytest.mark.parametrize("key_config", [{}, {"api_key": ""}, {"api_key": "   "}])
    @pytest.mark.parametrize(
        "name,api_format,base_url",
        [
            ("typesafe", "systemone", "https://api.typesafe.ai"),
            ("typesafe-proxy", "systemone", "https://decider.example.com"),
            ("perplexity-decider", "systemone", "https://decider.example.com"),
            ("openai", "openai", "https://api.openai.com/v1"),
            ("openai", "systemone", "https://decider.example.com"),
            ("custom", "openai", "https://decider.example.com"),
            ("custom-systemone", "systemone", "https://API.TypeSafe.AI./v1"),
            ("custom-systemone", "systemone", "https://api.perplexity.ai"),
            ("custom-systemone", "systemone", "https://api.openai.com/v1"),
        ],
    )
    def test_other_providers_still_require_a_key(
        self,
        auth_client,
        organization,
        gateway,
        endpoint,
        key_config,
        name,
        api_format,
        base_url,
    ):
        response = self._save(
            auth_client,
            endpoint,
            {"base_url": base_url, "api_format": api_format, **key_config},
            name=name,
        )
        assert response.status_code == 400, response.json()
        assert not AgentccProviderCredential.no_workspace_objects.filter(
            organization=organization, provider_name=name, deleted=False
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["gateway", "credentials"])
    @pytest.mark.parametrize(
        "base_url",
        [
            "",
            "   ",
            "ftp://decider.example.com",
            "https://",
            "https://bad host.invalid",
            "https://decider.example.com:invalid",
            "http://127.0.0.1:8080",
            "http://169.254.169.254",
            "http://10.0.0.12:8080",
        ],
    )
    def test_keyless_generic_requires_a_valid_explicit_base_url(
        self, auth_client, organization, gateway, endpoint, base_url
    ):
        response = self._save(
            auth_client,
            endpoint,
            {"base_url": base_url, "api_format": "systemone"},
        )
        assert response.status_code == 400, response.json()
        assert not AgentccProviderCredential.no_workspace_objects.filter(
            organization=organization, provider_name="custom-systemone", deleted=False
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["gateway", "credentials"])
    def test_keyless_generic_respects_private_endpoint_opt_in(
        self, auth_client, organization, gateway, endpoint, monkeypatch
    ):
        monkeypatch.setenv("AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", "true")
        response = self._save(
            auth_client,
            endpoint,
            {"base_url": "http://10.0.0.12:8080", "api_format": "systemone"},
        )
        expected_status = 200 if endpoint == "gateway" else 201
        assert response.status_code == expected_status, response.json()
        pushed = _pushed_providers(gateway, organization)["custom-systemone"]
        assert pushed["base_url"] == "http://10.0.0.12:8080"
        assert not pushed.get("api_key")

    @pytest.mark.parametrize("api_key", [False, {}, []])
    def test_keyless_exception_does_not_accept_malformed_credentials(
        self, auth_client, organization, gateway, api_key
    ):
        response = self._save(
            auth_client,
            "gateway",
            {
                "base_url": "https://decider.example.com",
                "api_format": "systemone",
                "api_key": api_key,
            },
        )
        assert response.status_code == 400, response.json()
        assert not AgentccProviderCredential.no_workspace_objects.filter(
            organization=organization, provider_name="custom-systemone", deleted=False
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("base_url", ["https://", "https://bad host.invalid"])
    def test_gateway_still_validates_the_url_when_a_key_is_supplied(
        self, auth_client, organization, gateway, base_url
    ):
        response = self._save(
            auth_client,
            "gateway",
            {"base_url": base_url, "api_format": "systemone", "api_key": "key"},
        )
        assert response.status_code == 400, response.json()
        assert not AgentccProviderCredential.no_workspace_objects.filter(
            organization=organization, provider_name="custom-systemone", deleted=False
        ).exists()
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("endpoint", ["gateway", "credentials"])
    @pytest.mark.parametrize("stored_credentials", [{}, {"region": "us-east-1"}])
    @pytest.mark.parametrize(
        "patch_config",
        [
            {"base_url": ""},
            {"base_url": "https://api.typesafe.ai"},
            {"base_url": "https://api.openai.com/v1"},
            {"api_format": "openai"},
        ],
    )
    def test_keyless_update_cannot_remove_the_exception_requirements(
        self,
        auth_client,
        organization,
        gateway,
        endpoint,
        patch_config,
        stored_credentials,
    ):
        config = {"base_url": "https://decider.example.com", "api_format": "systemone"}
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="custom-systemone",
            encrypted_credentials=CredentialManager.encrypt(stored_credentials),
            **config,
        )
        if endpoint == "gateway":
            response = self._save(auth_client, endpoint, config | patch_config)
        else:
            response = auth_client.patch(
                f"/agentcc/provider-credentials/{credential.id}/",
                patch_config,
                format="json",
            )
        assert response.status_code == 400, response.json()
        credential.refresh_from_db()
        assert credential.base_url == config["base_url"]
        assert credential.api_format == config["api_format"]
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize(
        "name,api_format,base_url",
        [
            ("typesafe", "systemone", "https://api.typesafe.ai"),
            ("openai", "openai", "https://api.openai.com/v1"),
        ],
    )
    def test_updating_a_vendor_cannot_blank_its_key(
        self, auth_client, organization, gateway, name, api_format, base_url
    ):
        credential = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name=name,
            encrypted_credentials=CredentialManager.encrypt({"api_key": "stored-key"}),
            base_url=base_url,
            api_format=api_format,
        )
        response = self._save(
            auth_client,
            "gateway",
            {"base_url": base_url, "api_format": api_format, "api_key": ""},
            name=name,
        )
        assert response.status_code == 400, response.json()
        credential.refresh_from_db()
        assert CredentialManager.decrypt(bytes(credential.encrypted_credentials)) == {
            "api_key": "stored-key"
        }
        gateway.set_org_config.assert_not_called()


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
