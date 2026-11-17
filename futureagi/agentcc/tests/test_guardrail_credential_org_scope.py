"""Guardrail credentials are decrypted only from the config's own organization."""

import json

import pytest
from cryptography.fernet import Fernet

from accounts.models.organization import Organization
from agentcc.models import AgentccGuardrailPolicy, AgentccOrgConfig
from agentcc.services.config_push import _build_payload
from integrations.services.credentials import CredentialManager

CHECK = "lakera-guard"
OWN_SECRET = "sk-own-org-lakera"
FOREIGN_SECRET = "sk-foreign-org-lakera"


@pytest.fixture
def policies(db, settings, organization):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    foreign_org = Organization.objects.create(name="Guardrail Credential Foreign Org")

    def policy(org, secret):
        return AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=org,
            name=f"lakera-{org.id}",
            checks=[{"name": CHECK, "config": {"api_key": "__encrypted__"}}],
            encrypted_check_configs=CredentialManager.encrypt(
                {CHECK: {"api_key": secret}}
            ),
        )

    return policy(organization, OWN_SECRET), policy(foreign_org, FOREIGN_SECRET)


def _check(policy):
    return {
        "enabled": True,
        "action": "block",
        "_policy": str(policy.id),
        "config": {"api_key": "__encrypted__"},
    }


def _guardrails(shape, policy):
    if shape == "rules":
        return {"rules": [{"name": CHECK, **_check(policy)}]}
    if shape == "checks-map":
        return {"checks": {CHECK: _check(policy)}}
    return {"checks": [{"name": CHECK, **_check(policy)}]}


@pytest.mark.parametrize("shape", ["rules", "checks-map", "checks-list"])
def test_own_policy_credentials_are_injected(organization, policies, shape):
    own, _ = policies
    config = AgentccOrgConfig(
        organization=organization, guardrails=_guardrails(shape, own)
    )

    payload = _build_payload(str(organization.id), config)

    assert payload["guardrails"]["checks"][CHECK]["config"]["api_key"] == OWN_SECRET


@pytest.mark.parametrize("shape", ["rules", "checks-map", "checks-list"])
def test_foreign_policy_credentials_are_never_decrypted(
    organization, policies, shape
):
    _, foreign = policies
    config = AgentccOrgConfig(
        organization=organization, guardrails=_guardrails(shape, foreign)
    )

    payload = _build_payload(str(organization.id), config)

    check_config = payload["guardrails"]["checks"][CHECK]["config"]
    assert "api_key" not in check_config
    assert FOREIGN_SECRET not in json.dumps(payload)
