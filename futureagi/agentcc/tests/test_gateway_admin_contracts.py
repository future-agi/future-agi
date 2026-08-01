import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agentcc.contracts.gateway_admin import (
    CreateKeyRequest,
    UpdateKeyRequest,
)
from agentcc.contracts.gateway_admin import (
    OrgConfig as GatewayOrgConfig,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import (
    _assemble_providers,
    _build_payload,
    push_all_org_configs,
)

ORG_CONFIG_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures/gateway_org_config.full.json"
)


def test_shared_org_config_fixture_validates_against_generated_python_contract():
    payload = json.loads(ORG_CONFIG_FIXTURE.read_text())

    contract = GatewayOrgConfig.model_validate(payload)
    dumped = contract.model_dump(by_alias=True, exclude_none=True)

    assert dumped["providers"]["openai"]["api_key"] == "sk-test-openai"
    assert dumped["routing"]["strategy"] == "weighted"
    assert dumped["budgets"]["teams"]["engineering"]["action"] == "warn"


def test_gateway_org_config_accepts_camel_case_input_and_dumps_snake_case():
    contract = GatewayOrgConfig.model_validate(
        {
            "providers": {
                "bedrock": {
                    "apiFormat": "bedrock",
                    "baseURL": "https://bedrock-runtime.us-east-1.amazonaws.com",
                    "timeout": 60,
                    "awsAccessKeyID": "ak",
                    "awsSecretAccessKey": "sk",
                    "awsRegion": "us-east-1",
                }
            },
            "routing": {
                "modelFallbacks": {"gpt-4o": ["claude-haiku-4-5"]},
                "circuitBreaker": {"enabled": False},
                "failover": {"enabled": True, "onStatusCodes": [429, 500]},
                "retry": {"enabled": False, "onStatusCodes": [429]},
            },
            "cache": {"enabled": True, "defaultTTL": 60, "maxEntries": 25},
        }
    )

    dumped = contract.model_dump(by_alias=True, exclude_none=True)

    assert dumped["providers"]["bedrock"]["api_format"] == "bedrock"
    assert dumped["providers"]["bedrock"]["base_url"].startswith("https://bedrock")
    assert dumped["providers"]["bedrock"]["aws_access_key_id"] == "ak"
    assert dumped["routing"]["model_fallbacks"] == {"gpt-4o": ["claude-haiku-4-5"]}
    assert dumped["routing"]["circuit_breaker"] == {"enabled": False}
    assert dumped["routing"]["failover"]["on_status_codes"] == [429, 500]
    assert dumped["routing"]["retry"]["on_status_codes"] == [429]
    assert dumped["cache"]["default_ttl"] == 60
    assert dumped["cache"]["max_entries"] == 25


def test_gateway_org_config_accepts_duplicate_saved_aliases():
    contract = GatewayOrgConfig.model_validate(
        {
            "routing": {
                "model_fallbacks": {"gpt-5.2": ["claude-haiku-4-5"]},
                "modelFallbacks": {"gpt-4o": ["claude-haiku-4-5"]},
                "circuit_breaker": {"enabled": False},
                "circuitBreaker": {"enabled": True},
                "failover": {
                    "enabled": True,
                    "on_status_codes": [429, 500, 502, 503, 504, 401],
                    "onStatusCodes": [401],
                },
                "retry": {
                    "enabled": False,
                    "on_status_codes": [429, 500, 502, 503, 401],
                    "onStatusCodes": [500],
                },
            },
        }
    )

    dumped = contract.model_dump(by_alias=True, exclude_none=True)

    assert dumped["routing"]["model_fallbacks"] == {"gpt-5.2": ["claude-haiku-4-5"]}
    assert dumped["routing"]["circuit_breaker"] == {"enabled": False}
    assert dumped["routing"]["failover"]["on_status_codes"] == [
        429,
        500,
        502,
        503,
        504,
        401,
    ]
    assert dumped["routing"]["retry"]["on_status_codes"] == [429, 500, 502, 503, 401]
    assert "modelFallbacks" not in dumped["routing"]
    assert "circuitBreaker" not in dumped["routing"]
    assert "onStatusCodes" not in dumped["routing"]["failover"]
    assert "onStatusCodes" not in dumped["routing"]["retry"]


@patch("agentcc.services.config_push._assemble_providers")
def test_build_payload_emits_gateway_org_config_contract(mock_assemble_providers):
    mock_assemble_providers.return_value = {
        "openai": {
            "api_key": "sk-test-openai",
            "api_format": "openai",
            "models": ["gpt-4o-mini"],
            "timeout": 17,
            "enabled": True,
        }
    }
    config = AgentccOrgConfig(
        routing={
            "strategy": "weighted",
            "fallbackEnabled": True,
            "failover": {"enabled": True, "onStatusCodes": [429, 500]},
            "retry": {"enabled": False, "onStatusCodes": [429]},
            "circuitBreaker": {"enabled": False},
            "modelFallbacks": {"gpt-4o": ["gpt-4o-mini"]},
        },
        budgets={
            "enabled": True,
            "org_limit": {"limit": 100, "period": "monthly", "onExceed": "block"},
            "teams": {"engineering": {"limit": 50, "actionMode": "warn"}},
        },
        cache={"enabled": True, "backend": "memory", "defaultTTL": "60s"},
        model_map={"fast": "gpt-4o-mini"},
    )

    payload = _build_payload("org-123", config)
    contract = GatewayOrgConfig.model_validate(payload)

    assert payload["providers"]["openai"]["timeout"] == 17
    assert contract.routing.strategy == "weighted"
    assert contract.routing.failover.on_status_codes == [429, 500]
    assert contract.routing.retry.on_status_codes == [429]
    assert contract.routing.circuit_breaker.enabled is False
    assert contract.routing.model_fallbacks == {"gpt-4o": ["gpt-4o-mini"]}
    assert contract.cache.default_ttl == 60
    assert contract.budgets.org_limit == 100
    assert contract.budgets.hard_limit is True
    assert contract.budgets.teams["engineering"].hard is False


@patch("integrations.services.credentials.CredentialManager.decrypt")
@patch(
    "agentcc.models.provider_credential.AgentccProviderCredential.no_workspace_objects"
)
def test_assemble_providers_maps_legacy_aws_credentials_to_gateway_contract(
    mock_manager,
    mock_decrypt,
):
    mock_decrypt.return_value = {
        "access_key": "ak",
        "secret_key": "sk",
        "region": "us-east-1",
    }
    mock_manager.filter.return_value = [
        SimpleNamespace(
            provider_name="bedrock",
            encrypted_credentials=b"encrypted",
            extra_config={"weight": 2, "ignored": "not-supported"},
            base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
            api_format="bedrock",
            models_list=["anthropic.claude-3-5-sonnet-20241022-v2:0"],
            default_timeout_seconds=60,
            max_concurrent=10,
            conn_pool_size=20,
        )
    ]

    providers = _assemble_providers("org-123")

    assert providers["bedrock"]["timeout"] == 60
    assert providers["bedrock"]["aws_access_key_id"] == "ak"
    assert providers["bedrock"]["aws_secret_access_key"] == "sk"
    assert providers["bedrock"]["aws_region"] == "us-east-1"
    assert providers["bedrock"]["weight"] == 2
    assert "access_key" not in providers["bedrock"]
    assert "ignored" not in providers["bedrock"]


@patch("integrations.services.credentials.CredentialManager.decrypt")
@patch(
    "agentcc.models.provider_credential.AgentccProviderCredential.no_workspace_objects"
)
def test_assemble_providers_skips_unsupported_credential_provider(
    mock_manager,
    mock_decrypt,
):
    mock_decrypt.side_effect = [
        {"api_secret": "secret"},
        {"api_key": "sk-test"},
    ]
    mock_manager.filter.return_value = [
        SimpleNamespace(
            provider_name="custom",
            encrypted_credentials=b"encrypted",
            extra_config={},
            base_url="https://example.com",
            api_format="openai",
            models_list=["custom-model"],
            default_timeout_seconds=30,
            max_concurrent=10,
            conn_pool_size=20,
        ),
        SimpleNamespace(
            provider_name="openai",
            encrypted_credentials=b"encrypted-openai",
            extra_config={},
            base_url="https://api.openai.com",
            api_format="openai",
            models_list=["gpt-4o"],
            default_timeout_seconds=30,
            max_concurrent=10,
            conn_pool_size=20,
        ),
    ]

    providers = _assemble_providers("org-123")

    assert "custom" not in providers
    assert providers["openai"]["api_key"] == "sk-test"


@patch("agentcc.services.config_push._build_payload")
@patch("agentcc.services.config_push.get_gateway_client")
@patch("agentcc.services.config_push.AgentccOrgConfig")
def test_push_all_org_configs_continues_after_payload_error(
    mock_org_config,
    mock_get_gateway_client,
    mock_build_payload,
):
    bad_cfg = SimpleNamespace(organization_id="bad-org")
    good_cfg = SimpleNamespace(organization_id="good-org")
    mock_org_config.no_workspace_objects.filter.return_value.select_related.return_value = [
        bad_cfg,
        good_cfg,
    ]
    mock_build_payload.side_effect = [ValueError("bad provider"), {"cache": {}}]
    client = Mock()
    mock_get_gateway_client.return_value = client

    push_all_org_configs()

    client.set_org_config.assert_called_once_with("good-org", {"cache": {}})


def test_key_admin_requests_are_typed_at_backend_boundary():
    create = CreateKeyRequest(
        name="Production key",
        owner="platform",
        models=["gpt-4o"],
        providers=["openai"],
        metadata={"purpose": "gateway-startup"},
    )
    update = UpdateKeyRequest(metadata={"enabled": "true"})

    assert (
        create.model_dump(exclude_none=True)["metadata"]["purpose"] == "gateway-startup"
    )
    assert update.model_dump(exclude_none=True) == {"metadata": {"enabled": "true"}}
