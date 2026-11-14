"""Key-scoped policies validate dashboard IDs and reach the gateway payload."""

import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.config_push import OrgConfigRejected, _build_payload
from integrations.services.credentials import CredentialManager

URL = "/agentcc/guardrail-policies/"


def key(org, **kwargs):
    return AgentccAPIKey.no_workspace_objects.create(
        organization=org,
        gateway_key_id=f"gateway-{uuid.uuid4()}",
        name="Synthetic key",
        **kwargs,
    )


def body(key_id):
    return {
        "name": "payments-rollout",
        "scope": "key",
        "mode": "monitor",
        "applied_keys": [str(key_id)],
        "checks": [
            {"name": "secret-detection", "enabled": True, "action": "block"},
            {
                "name": "pii-detection",
                "enabled": True,
                "action": "log",
                "config": {"remediation": "redact"},
            },
        ],
    }


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"applied_keys": ["malformed"]}, "applied_keys"),
        ({"applied_keys": [str(uuid.uuid4())]}, "applied_keys"),
        ({"applied_keys": []}, "applied_keys"),
        ({"scope": "global"}, "applied_keys"),
        ({"name": "__reserved"}, "name"),
        ({"checks": [{"name": "pii-detection", "action": "ignore"}]}, "checks"),
        ({"checks": [{"name": "pii-detection", "threshold": True}]}, "checks"),
        ({"checks": [{"name": "pii-detection", "threshold": 1.1}]}, "checks"),
    ],
)
def test_invalid_policy_inputs(auth_client, organization, change, field):
    data = body(key(organization).id)
    data.update(change)
    response = auth_client.post(URL, data, format="json")
    assert response.status_code == 400, response.json()
    assert field in response.json()["result"]


def test_foreign_key_is_rejected(auth_client, organization):
    foreign = Organization.objects.create(name="Foreign policy org")
    response = auth_client.post(URL, body(key(foreign).id), format="json")
    assert response.status_code == 400
    assert "applied_keys" in response.json()["result"]


def test_docs_example_round_trip(auth_client, organization):
    api_key = key(organization)
    response = auth_client.post(URL, body(api_key.id), format="json")
    assert response.status_code == 200, response.json()
    row = response.json()["result"]
    assert row["applied_keys"] == [str(api_key.id)]
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    guardrails = _build_payload(str(organization.id), config)["guardrails"]
    assert guardrails["checks"] == {}
    policy = guardrails["key_policies"][0]
    assert policy["key_ids"] == [api_key.gateway_key_id]
    assert policy["mode"] == "monitor"
    assert policy["checks"]["pii-detection"]["config"]["remediation"] == "redact"
    update = auth_client.patch(f"{URL}{row['id']}/", {"mode": "enforce"}, format="json")
    assert update.status_code == 200, update.json()
    assert (
        _build_payload(str(organization.id), config)["guardrails"]["key_policies"][0][
            "mode"
        ]
        == "enforce"
    )


def test_create_normalizes_and_deduplicates_keys(auth_client, organization):
    api_key = key(organization)
    payload = body(api_key.id)
    payload["applied_keys"] = [str(api_key.id).upper(), str(api_key.id)]
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["applied_keys"] == [str(api_key.id)]


def test_stale_and_revoked_keys_are_skipped(organization):
    live = key(organization)
    revoked = key(organization, status=AgentccAPIKey.REVOKED)
    deleted = key(organization, deleted=True)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="mixed",
        scope="key",
        runs_on_gateway=True,
        applied_keys=[str(live.id), str(revoked.id), str(deleted.id), "invalid"],
        checks=[{"name": "secrets-detector", "threshold": 0.4, "action": "block"}],
    )
    config = AgentccOrgConfig(organization=organization)
    policies = _build_payload(str(organization.id), config)["guardrails"][
        "key_policies"
    ]
    assert policies[0]["key_ids"] == [live.gateway_key_id]
    assert policies[0]["checks"]["secret-detection"]["confidence_threshold"] == 0.4
    policy.applied_keys = [str(revoked.id), str(deleted.id)]
    policy.save(update_fields=["applied_keys"])
    assert "key_policies" not in (
        _build_payload(str(organization.id), config)["guardrails"] or {}
    )


def test_secrets_are_decrypted_per_policy(organization, settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    policies = []
    for number in (1, 2):
        api_key = key(organization)
        policies.append(
            AgentccGuardrailPolicy.no_workspace_objects.create(
                organization=organization,
                name=f"policy-{number}",
                scope="key",
                runs_on_gateway=True,
                applied_keys=[str(api_key.id)],
                checks=[
                    {"name": "lakera-guard", "config": {"api_key": "__encrypted__"}}
                ],
                encrypted_check_configs=CredentialManager.encrypt(
                    {"lakera-guard": {"api_key": f"synthetic-{number}"}}
                ),
            )
        )
    config = AgentccOrgConfig(organization=organization)
    results = _build_payload(str(organization.id), config)["guardrails"]["key_policies"]
    assert [p["checks"]["lakera-guard"]["config"]["api_key"] for p in results] == [
        "synthetic-1",
        "synthetic-2",
    ]
    assert all("_policy" not in p["checks"]["lakera-guard"] for p in results)


def test_key_save_does_not_mutate_org_guardrails(auth_client, organization):
    original = {"fail_open": True, "pipeline_mode": "sequential", "checks": {}}
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, guardrails=original
    )
    response = auth_client.post(URL, body(key(organization).id), format="json")
    assert response.status_code == 200, response.json()
    config.refresh_from_db()
    assert config.guardrails == original


def test_policy_save_creates_active_config_after_inactive_history(
    auth_client, organization
):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=False
    )
    response = auth_client.post(URL, body(key(organization).id), format="json")
    assert response.status_code == 200, response.json()
    assert (
        AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True
        ).version
        == 2
    )


def test_rejected_payload_rolls_back_and_does_not_push(auth_client, organization):
    with (
        patch(
            "agentcc.views.guardrail_policy.validate_org_config",
            side_effect=OrgConfigRejected("rejected"),
        ),
        patch("agentcc.views.guardrail_policy.push_org_config") as push,
    ):
        response = auth_client.post(URL, body(key(organization).id), format="json")
    assert response.status_code == 400
    assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
        name="payments-rollout"
    ).exists()
    assert not AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).exists()
    push.assert_not_called()


def test_bulk_contains_policy_with_two_queries(organization):
    """The payload builder performs one policy and one key lookup per org."""
    config = AgentccOrgConfig.no_workspace_objects.create(organization=organization)
    api_key = key(organization)
    AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="bulk-key",
        scope="key",
        runs_on_gateway=True,
        applied_keys=[str(api_key.id)],
        checks=[{"name": "pii-detection"}],
    )
    global_policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="bulk-global",
        scope="global",
        checks=[{"name": "secret-detection"}],
    )
    config.guardrails = {
        "checks": [
            {
                "name": "secret-detection",
                "action": "log",
                "_policy": str(global_policy.id),
            }
        ]
    }
    # Exactly one lookup each of policies and keys, even with org policy credentials.
    with CaptureQueriesContext(connection) as queries:
        payload = _build_payload(str(organization.id), config)
    assert sum('FROM "agentcc_guardrail_policy"' in q["sql"] for q in queries) == 1
    assert sum('FROM "agentcc_api_key"' in q["sql"] for q in queries) == 1
    assert payload["guardrails"]["key_policies"][0]["key_ids"] == [
        api_key.gateway_key_id
    ]

    # The startup bulk pull uses the same builder and returns the key policy.
    from rest_framework.test import APIClient

    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", "synthetic-admin-token"):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer synthetic-admin-token")
        response = client.get("/agentcc/org-configs/bulk/")
    assert response.status_code == 200, response.json()
    assert response.json()["result"][str(organization.id)]["guardrails"][
        "key_policies"
    ][0]["key_ids"] == [api_key.gateway_key_id]


def test_apply_validates_and_normalizes(auth_client, organization):
    first = key(organization)
    second = key(organization)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="apply-policy",
        checks=[{"name": "pii-detection"}],
    )
    path = f"{URL}{policy.id}/apply/"
    bad = auth_client.post(
        path, {"key_ids": [str(first.id), "not-a-uuid"]}, format="json"
    )
    assert bad.status_code == 400
    assert "applied_keys" in bad.json()["result"]
    policy.refresh_from_db()
    assert policy.scope == "global"
    good = auth_client.post(
        path,
        {"key_ids": [str(first.id).upper(), str(first.id), str(second.id)]},
        format="json",
    )
    assert good.status_code == 200, good.json()
    assert good.json()["result"]["applied_keys"] == [str(first.id), str(second.id)]
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert config.guardrails["checks"] == {}


def test_partial_key_update_cannot_clear_keys_or_change_scope(
    auth_client, organization
):
    api_key = key(organization)
    response = auth_client.post(URL, body(api_key.id), format="json")
    policy_id = response.json()["result"]["id"]
    for change in ({"applied_keys": []}, {"scope": "global"}):
        response = auth_client.patch(f"{URL}{policy_id}/", change, format="json")
        assert response.status_code == 400, response.json()
        assert "applied_keys" in response.json()["result"]


def test_global_to_key_transition_removes_global_checks(auth_client, organization):
    api_key = key(organization)
    response = auth_client.post(
        URL,
        {
            "name": "transition-global",
            "scope": "global",
            "checks": [{"name": "pii-detection", "action": "block"}],
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    policy_id = response.json()["result"]["id"]
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True
    )
    assert (
        "pii-detection"
        in _build_payload(str(organization.id), config)["guardrails"]["checks"]
    )
    response = auth_client.post(
        f"{URL}{policy_id}/apply/", {"key_ids": [str(api_key.id)]}, format="json"
    )
    assert response.status_code == 200, response.json()
    config.refresh_from_db()
    guardrails = _build_payload(str(organization.id), config)["guardrails"]
    assert guardrails["checks"] == {}
    assert guardrails["key_policies"][0]["key_ids"] == [api_key.gateway_key_id]
