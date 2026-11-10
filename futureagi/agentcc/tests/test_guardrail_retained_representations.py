"""Every retained guardrail representation crosses the same secret boundary."""

import copy
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from agentcc.models import AgentccGuardrailPolicy, AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected, validate_org_config
from integrations.services.credentials import CredentialManager

SECRET = "synthetic-retained-rules-secret"
CHECK_SECRET = "synthetic-selected-check-secret"
SHAPES = ["map-and-rules", "list-and-rules", "empty-map-and-rules"]
INVALID = [
    pytest.param([["api_key", SECRET]], id="pairs"),
    pytest.param([], id="empty-array"),
    pytest.param("scalar", id="string"),
    pytest.param("", id="empty-string"),
    pytest.param(7, id="number"),
    pytest.param(0, id="zero"),
    pytest.param(False, id="false"),
    pytest.param(None, id="null"),
]


def mixed(shape, config):
    check = {"enabled": True, "config": {"api_key": CHECK_SECRET}}
    checks = {"lakera-guard": check}
    if shape == "list-and-rules":
        checks = [{"name": "lakera-guard", **check}]
    elif shape == "empty-map-and-rules":
        checks = {}
    return {
        "checks": checks,
        "rules": [{"name": "lakera-guard", "enabled": True, "config": config}],
        "failOpen": False,
        "pipelineMode": "parallel",
        "timeoutMs": 5000,
    }


def bulk():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer synthetic-admin")
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", "synthetic-admin"):
        return client.get("/agentcc/org-configs/bulk/")


@pytest.fixture(autouse=True)
def encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID)
def test_all_retained_configs_are_validated(organization, shape, invalid):
    config = AgentccOrgConfig(
        organization=organization, guardrails=mixed(shape, invalid)
    )
    original = copy.deepcopy(config.guardrails)
    with pytest.raises(OrgConfigRejected, match="config"):
        validate_org_config(config)
    assert config.guardrails == original


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID)
@patch("agentcc.services.config_push.get_gateway_client")
def test_mixed_save_refuses_without_storage_or_readback(
    gateway, auth_client, organization, shape, invalid
):
    original = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )
    response = auth_client.post(
        "/agentcc/org-configs/", {"guardrails": mixed(shape, invalid)}, format="json"
    )
    rows = list(AgentccOrgConfig.no_workspace_objects.filter(organization=organization))
    policies = list(
        AgentccGuardrailPolicy.no_workspace_objects.filter(organization=organization)
    )
    for secret in (SECRET, CHECK_SECRET):
        assert secret not in json.dumps([row.guardrails for row in rows]), (
            "plaintext stored in org config"
        )
        assert secret not in json.dumps([policy.checks for policy in policies])
        assert secret not in json.dumps(response.json())
        assert secret not in json.dumps(auth_client.get("/agentcc/org-configs/").json())
    assert response.status_code == 400, response.json()
    assert "config" in json.dumps(response.json())
    assert [(row.id, row.is_active) for row in rows] == [(original.id, True)]
    assert policies == []
    gateway.return_value.set_org_config.assert_not_called()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID)
@patch("agentcc.services.config_push.get_gateway_client")
def test_mixed_activation_refuses_without_mutation(
    gateway, auth_client, organization, shape, invalid
):
    stored = mixed(shape, invalid)
    stale = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=False, guardrails=stored
    )
    current = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=2, is_active=True
    )
    response = auth_client.post(f"/agentcc/org-configs/{stale.id}/activate/")
    assert response.status_code == 400, response.json()
    assert SECRET not in json.dumps(response.json())
    stale.refresh_from_db()
    current.refresh_from_db()
    assert stale.guardrails == stored
    assert not stale.is_active
    assert current.is_active
    gateway.return_value.set_org_config.assert_not_called()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("invalid", INVALID)
def test_mixed_bulk_isolates_bad_organization(organization, shape, invalid):
    stored = mixed(shape, invalid)
    stale = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails=stored
    )
    healthy = Organization.objects.create(name="Healthy retained config organization")
    good = AgentccOrgConfig.no_workspace_objects.create(
        organization=healthy,
        version=1,
        is_active=True,
        guardrails={
            "checks": {"keyword-blocklist": {"config": {"words": ["synthetic"]}}}
        },
    )
    response = bulk()
    assert response.status_code == 200
    body = response.json()
    assert body["unbuilt_org_ids"] == [str(organization.id)]
    assert set(body["result"]) == {str(healthy.id)}
    assert body["result"][str(healthy.id)]["guardrails"]["checks"]["keyword-blocklist"][
        "config"
    ]["words"] == ["synthetic"]
    assert SECRET not in json.dumps(body)
    stale.refresh_from_db()
    good.refresh_from_db()
    assert stale.guardrails == stored
    assert stale.is_active and good.is_active


@pytest.mark.parametrize("shape", SHAPES)
@patch("agentcc.services.config_push.get_gateway_client")
def test_mixed_objects_encrypt_each_representation_and_preserve_on_resave(
    gateway, auth_client, organization, shape
):
    response = auth_client.post(
        "/agentcc/org-configs/",
        {"guardrails": mixed(shape, {"api_key": SECRET})},
        format="json",
    )
    assert response.status_code == 200, response.json()
    config = AgentccOrgConfig.no_workspace_objects.get(organization=organization)
    policies = list(
        AgentccGuardrailPolicy.no_workspace_objects.filter(organization=organization)
    )
    decrypted = [
        CredentialManager.decrypt(bytes(p.encrypted_check_configs)) for p in policies
    ]
    assert SECRET in json.dumps(decrypted)
    if shape != "empty-map-and-rules":
        assert CHECK_SECRET in json.dumps(decrypted)
    for secret in (SECRET, CHECK_SECRET):
        assert secret not in json.dumps(config.guardrails)
        assert secret not in json.dumps([p.checks for p in policies])
        assert all(
            secret.encode() not in bytes(p.encrypted_check_configs) for p in policies
        )
        for body in (
            response.json(),
            auth_client.get("/agentcc/org-configs/").json(),
            auth_client.get(f"/agentcc/org-configs/{config.id}/").json(),
            auth_client.get("/agentcc/org-configs/active/").json(),
        ):
            assert secret not in json.dumps(body)
            assert "__encrypted__" in json.dumps(body)
    resave = auth_client.post(
        "/agentcc/org-configs/", {"guardrails": config.guardrails}, format="json"
    )
    assert resave.status_code == 200, resave.json()
    activated = auth_client.post(f"/agentcc/org-configs/{config.id}/activate/")
    assert activated.status_code == 200, activated.json()
    expected = SECRET if shape == "empty-map-and-rules" else CHECK_SECRET
    for call in gateway.return_value.set_org_config.call_args_list:
        assert (
            call.args[1]["guardrails"]["checks"]["lakera-guard"]["config"]["api_key"]
            == expected
        )
    response = bulk()
    assert response.status_code == 200
    assert response.json()["unbuilt_org_ids"] == []
    assert (
        response.json()["result"][str(organization.id)]["guardrails"]["checks"][
            "lakera-guard"
        ]["config"]["api_key"]
        == expected
    )
    # Switching to the retained rules must deliver their own credential, even
    # when a same-named selected check had a different one.
    config.refresh_from_db()
    switched = auth_client.post(
        "/agentcc/org-configs/",
        {"guardrails": {"rules": config.guardrails["rules"]}},
        format="json",
    )
    assert switched.status_code == 200, switched.json()
    assert (
        gateway.return_value.set_org_config.call_args.args[1]["guardrails"]["checks"][
            "lakera-guard"
        ]["config"]["api_key"]
        == SECRET
    )


@pytest.mark.parametrize("key", ["policies", "guardrailChecks", "guardrail_rules"])
@patch("agentcc.services.config_push.get_gateway_client")
def test_unknown_sibling_collections_cannot_be_retained(
    gateway, auth_client, organization, key
):
    value = mixed("map-and-rules", {})
    value[key] = [{"name": "lakera-guard", "config": [["api_key", SECRET]]}]
    with pytest.raises(OrgConfigRejected):
        validate_org_config(
            AgentccOrgConfig(organization=organization, guardrails=value)
        )
    response = auth_client.post(
        "/agentcc/org-configs/", {"guardrails": value}, format="json"
    )
    assert response.status_code == 400
    assert SECRET not in json.dumps(response.json())
    assert not AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).exists()
    assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
        organization=organization
    ).exists()
    gateway.return_value.set_org_config.assert_not_called()
