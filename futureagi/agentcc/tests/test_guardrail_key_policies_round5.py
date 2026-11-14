"""Fifth review round of key-scoped guardrail policies.

A key policy saved before the policy API validated checks can hold values the
API now refuses, such as a threshold of 50. A save that does not resend checks
(a mode or Active change, or apply with key_ids) makes the row run on the
gateway, so it must not activate checks that could never act: a detector score
never exceeds 1, so a Block check with threshold 50 would pass every secret.
"""

import uuid

import pytest

from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.config_push import _build_payload

URL = "/agentcc/guardrail-policies/"
BAD_CHECKS = [{"name": "secret-detection", "action": "block", "threshold": 50}]
GOOD_CHECKS = [{"name": "secret-detection", "action": "block", "threshold": 0.5}]


def _key(org):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org, gateway_key_id=f"gateway-{uuid.uuid4()}", name="Synthetic"
    )


def _setup(organization, scope="key", checks=BAD_CHECKS):
    api_key = _key(organization)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="saved-before-validation",
        scope=scope,
        applied_keys=[str(api_key.id)] if scope == "key" else [],
        checks=checks,
    )
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        guardrails={"checks": {}},
    )
    return api_key, policy, config


def _running(organization, config):
    payload = _build_payload(str(organization.id), config)
    return [p["name"] for p in payload["guardrails"].get("key_policies", [])]


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{"mode": "enforce"}, {"is_active": True}])
def test_save_without_checks_does_not_activate_invalid_stored_checks(
    auth_client, organization, body
):
    _, policy, config = _setup(organization)
    response = auth_client.patch(f"{URL}{policy.id}/", body, format="json")
    assert response.status_code == 400, response.json()
    assert "threshold" in str(response.json())
    policy.refresh_from_db()
    assert policy.runs_on_gateway is False
    assert _running(organization, config) == []


@pytest.mark.django_db
def test_apply_with_key_ids_does_not_activate_invalid_stored_checks(
    auth_client, organization
):
    api_key, policy, config = _setup(organization, scope="global")
    response = auth_client.post(
        f"{URL}{policy.id}/apply/", {"key_ids": [str(api_key.id)]}, format="json"
    )
    assert response.status_code == 400, response.json()
    policy.refresh_from_db()
    assert (policy.scope, policy.applied_keys, policy.runs_on_gateway) == (
        "global",
        [],
        False,
    )
    assert _running(organization, config) == []


@pytest.mark.django_db
def test_resending_corrected_checks_activates_the_policy(auth_client, organization):
    _, policy, config = _setup(organization)
    response = auth_client.patch(
        f"{URL}{policy.id}/",
        {"mode": "enforce", "checks": GOOD_CHECKS},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["runs_on_gateway"] is True
    assert _running(organization, config) == ["saved-before-validation"]


@pytest.mark.django_db
def test_an_invalid_dormant_policy_can_still_be_switched_off(auth_client, organization):
    _, policy, config = _setup(organization)
    response = auth_client.patch(
        f"{URL}{policy.id}/", {"is_active": False}, format="json"
    )
    assert response.status_code == 200, response.json()
    policy.refresh_from_db()
    assert policy.is_active is False
    assert _running(organization, config) == []


@pytest.mark.django_db
def test_valid_stored_checks_still_activate_without_resending(
    auth_client, organization
):
    _, policy, config = _setup(organization, checks=GOOD_CHECKS)
    response = auth_client.patch(
        f"{URL}{policy.id}/", {"mode": "enforce"}, format="json"
    )
    assert response.status_code == 200, response.json()
    assert _running(organization, config) == ["saved-before-validation"]
