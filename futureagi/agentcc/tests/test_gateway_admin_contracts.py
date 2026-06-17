import json
from pathlib import Path
from unittest.mock import patch

from agentcc.contracts.gateway_admin import (
    CreateKeyRequest,
    OrgConfig as GatewayOrgConfig,
    UpdateKeyRequest,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload


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


@patch("agentcc.services.config_push._assemble_providers")
def test_build_payload_emits_gateway_org_config_contract(mock_assemble_providers):
    mock_assemble_providers.return_value = {
        "openai": {
            "api_key": "sk-test-openai",
            "api_format": "openai",
            "models": ["gpt-4o-mini"],
            "enabled": True,
        }
    }
    config = AgentccOrgConfig(
        routing={
            "strategy": "weighted",
            "fallback_enabled": True,
            "fallback_on_status_codes": [429, 500],
        },
        budgets={
            "enabled": True,
            "org_limit": {"limit": 100, "period": "monthly", "on_exceed": "block"},
            "teams": {"engineering": {"limit": 50, "action_mode": "warn"}},
        },
        cache={"enabled": True, "backend": "memory"},
        model_map={"fast": "gpt-4o-mini"},
    )

    payload = _build_payload("org-123", config)
    contract = GatewayOrgConfig.model_validate(payload)

    assert contract.routing.strategy == "weighted"
    assert contract.budgets.org_limit == 100
    assert contract.budgets.hard_limit is True
    assert contract.budgets.teams["engineering"].hard is False


def test_key_admin_requests_are_typed_at_backend_boundary():
    create = CreateKeyRequest(
        name="Production key",
        owner="platform",
        models=["gpt-4o"],
        providers=["openai"],
        metadata={"purpose": "gateway-startup"},
    )
    update = UpdateKeyRequest(metadata={"enabled": "true"})

    assert create.model_dump(exclude_none=True)["metadata"]["purpose"] == "gateway-startup"
    assert update.model_dump(exclude_none=True) == {"metadata": {"enabled": "true"}}
