"""Guardrail config objects share the credential extraction and gateway boundary."""

import copy
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from agentcc.models import AgentccGuardrailPolicy, AgentccOrgConfig
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import (
    OrgConfigRejected,
    _build_payload,
    validate_org_config,
)
from integrations.services.credentials import CredentialManager

CHECK = "lakera-guard"
SECRET = "synthetic-config-object-credential"
INVALID_CONFIGS = [
    pytest.param([["api_key", SECRET]], id="pairs"),
    pytest.param([], id="empty-array"),
    pytest.param("scalar", id="string"),
    pytest.param("", id="empty-string"),
    pytest.param(7, id="number"),
    pytest.param(0, id="zero"),
    pytest.param(False, id="false"),
    pytest.param(None, id="null"),
]
SHAPES = ["checks-map", "checks-list", "rules"]


def guardrails(shape, config, **extra):
    check = {"enabled": True, "action": "block", "config": config, **extra}
    if shape == "checks-map":
        return {"checks": {CHECK: check}}
    return {"rules" if shape == "rules" else "checks": [{"name": CHECK, **check}]}


def versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


@pytest.fixture(autouse=True)
def encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID_CONFIGS)
def test_contract_rejects_non_object_config(organization, shape, invalid):
    config = AgentccOrgConfig(
        organization=organization, guardrails=guardrails(shape, invalid)
    )
    original = copy.deepcopy(config.guardrails)
    with pytest.raises(OrgConfigRejected, match="config"):
        validate_org_config(config)
    assert config.guardrails == original


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("cfg", [{}, {"api_key": "__encrypted__"}])
def test_contract_accepts_objects(organization, shape, cfg):
    config = AgentccOrgConfig(
        organization=organization, guardrails=guardrails(shape, cfg)
    )
    validate_org_config(config)
    assert config.guardrails == guardrails(shape, cfg)


@pytest.mark.parametrize("shape", SHAPES)
def test_contract_accepts_omitted_config(organization, shape):
    value = guardrails(shape, {})
    checks = value.get("checks", value.get("rules"))
    check = checks[CHECK] if isinstance(checks, dict) else checks[0]
    del check["config"]
    validate_org_config(AgentccOrgConfig(organization=organization, guardrails=value))


@pytest.mark.parametrize("shape", ["checks-map", "rules"])
@pytest.mark.parametrize("invalid", INVALID_CONFIGS)
@patch("agentcc.services.config_push.get_gateway_client")
def test_editor_refusal_preserves_rows_and_never_stores_credentials(
    gateway, auth_client, organization, shape, invalid
):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )
    response = auth_client.post(
        "/agentcc/org-configs/",
        {"guardrails": guardrails(shape, invalid)},
        format="json",
    )
    stored = list(
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).values_list("guardrails", flat=True)
    )
    policies = list(
        AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).values_list("checks", flat=True)
    )
    readback = auth_client.get("/agentcc/org-configs/")
    assert SECRET not in json.dumps(stored), "plaintext stored in org config"
    assert SECRET not in json.dumps(policies), "plaintext stored in policy"
    assert SECRET not in json.dumps(response.json())
    assert SECRET not in json.dumps(readback.json())
    assert response.status_code == 400, response.json()
    assert "config" in json.dumps(response.json())
    assert versions(organization) == [(1, True)]
    assert policies == []
    gateway.return_value.set_org_config.assert_not_called()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID_CONFIGS)
@patch("agentcc.services.config_push.get_gateway_client")
def test_activation_refuses_stored_non_object_config(
    gateway, auth_client, organization, shape, invalid
):
    stale = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=False,
        guardrails=guardrails(shape, invalid),
    )
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=2, is_active=True
    )
    response = auth_client.post(f"/agentcc/org-configs/{stale.id}/activate/")
    assert response.status_code == 400, response.json()
    assert SECRET not in json.dumps(response.json())
    assert versions(organization) == [(1, False), (2, True)]
    stale.refresh_from_db()
    assert stale.guardrails == guardrails(shape, invalid)
    gateway.return_value.set_org_config.assert_not_called()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID_CONFIGS)
def test_bulk_sync_retains_last_good_for_invalid_org(organization, shape, invalid):
    stale = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        guardrails=guardrails(shape, invalid),
    )
    healthy = Organization.objects.create(name="Healthy config object organization")
    AgentccOrgConfig.no_workspace_objects.create(
        organization=healthy, version=1, is_active=True
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer synthetic-admin")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", "synthetic-admin"):
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == 200
    body = response.json()
    assert SECRET not in json.dumps(body)
    assert body["unbuilt_org_ids"] == [str(organization.id)]
    assert set(body["result"]) == {str(healthy.id)}
    stale.refresh_from_db()
    assert stale.guardrails == guardrails(shape, invalid)


@pytest.mark.parametrize("invalid", INVALID_CONFIGS)
@pytest.mark.parametrize("update", [False, True])
def test_policy_serializer_refuses_non_object_config(organization, invalid, update):
    policy = None
    if update:
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="Object policy", checks=[]
        )
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={"name": "Object policy", "checks": [{"name": CHECK, "config": invalid}]},
        partial=update,
    )
    assert not serializer.is_valid()
    assert "config" in str(serializer.errors)
    assert SECRET not in str(serializer.errors)
    if policy:
        policy.refresh_from_db()
        assert policy.checks == []


@pytest.mark.parametrize("shape", ["checks-map", "rules"])
@patch("agentcc.services.config_push.get_gateway_client")
def test_editor_objects_encrypt_read_back_and_activate(
    gateway, auth_client, organization, shape
):
    response = auth_client.post(
        "/agentcc/org-configs/",
        {"guardrails": guardrails(shape, {"api_key": SECRET})},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    config = AgentccOrgConfig.no_workspace_objects.get(organization=organization)
    policy = AgentccGuardrailPolicy.no_workspace_objects.get(organization=organization)
    assert SECRET not in json.dumps(config.guardrails)
    assert SECRET not in json.dumps(policy.checks)
    assert SECRET.encode() not in bytes(policy.encrypted_check_configs)
    assert CredentialManager.decrypt(bytes(policy.encrypted_check_configs))[CHECK] == {
        "api_key": SECRET
    }
    for body in (
        response.json(),
        auth_client.get("/agentcc/org-configs/").json(),
        auth_client.get(f"/agentcc/org-configs/{config.id}/").json(),
        auth_client.get("/agentcc/org-configs/active/").json(),
    ):
        assert SECRET not in json.dumps(body)
        assert "__encrypted__" in json.dumps(body)
    assert (
        _build_payload(str(organization.id), config)["guardrails"]["checks"][CHECK][
            "config"
        ]["api_key"]
        == SECRET
    )
    assert SECRET not in json.dumps(config.guardrails)
    config.is_active = False
    config.save(update_fields=["is_active"])
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=2, is_active=True
    )
    activated = auth_client.post(f"/agentcc/org-configs/{config.id}/activate/")
    assert activated.status_code == 200, activated.json()
    assert SECRET not in json.dumps(activated.json())
    assert versions(organization) == [(1, True), (2, False)]
    assert gateway.return_value.set_org_config.call_count == 2
    for call in gateway.return_value.set_org_config.call_args_list:
        assert (
            call.args[1]["guardrails"]["checks"][CHECK]["config"]["api_key"] == SECRET
        )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer synthetic-admin")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", "synthetic-admin"):
        bulk = client.get("/agentcc/org-configs/bulk/")
    assert bulk.status_code == 200
    assert bulk.json()["unbuilt_org_ids"] == []
    assert (
        bulk.json()["result"][str(organization.id)]["guardrails"]["checks"][CHECK][
            "config"
        ]["api_key"]
        == SECRET
    )
    config.refresh_from_db()
    assert SECRET not in json.dumps(config.guardrails)
