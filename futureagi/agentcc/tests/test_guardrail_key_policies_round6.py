"""Apply only changes assignments: recovery must save checks via PATCH first."""

from unittest.mock import patch

import pytest

from agentcc.services.config_push import _build_payload
from agentcc.tests.test_guardrail_key_policies_round5 import (
    BAD_CHECKS,
    GOOD_CHECKS,
    URL,
    _key,
    _setup,
)


def _error_envelope(guidance):
    checks_error = (
        "The stored checks cannot run on the gateway "
        f"(Check at index 0 has invalid threshold). {guidance}"
    )
    message = f"checks: {checks_error}"
    return {
        "status": False,
        "type": "validation_error",
        "code": "invalid",
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "attr": "checks",
        "details": {"checks": [checks_error]},
    }


@pytest.mark.django_db
def test_apply_advertises_patch_then_retry_and_keeps_checks_out_of_apply(
    auth_client, organization
):
    original_key, policy, config = _setup(organization)
    target_key = _key(organization)
    detail_url = f"{URL}{policy.id}/"
    apply_url = f"{detail_url}apply/"
    original_updated_at = policy.updated_at
    assert policy.is_active is True
    assert policy.runs_on_gateway is False
    assert (
        _build_payload(str(organization.id), config)["guardrails"].get(
            "key_policies", []
        )
        == []
    )

    pushed_payloads = []

    def push(org_id, saved_config):
        pushed_payloads.append(_build_payload(org_id, saved_config))
        return True

    with patch("agentcc.views.guardrail_policy.push_org_config", side_effect=push):
        first = auth_client.post(
            apply_url, {"key_ids": [str(target_key.id)]}, format="json"
        )
        retry_with_checks = auth_client.post(
            apply_url,
            {"key_ids": [str(target_key.id)], "checks": GOOD_CHECKS},
            format="json",
        )
        assert first.status_code == retry_with_checks.status_code == 400
        assert retry_with_checks.json() == first.json()
        policy.refresh_from_db()
        assert (policy.scope, policy.applied_keys, policy.is_active) == (
            "key",
            [str(original_key.id)],
            True,
        )
        assert policy.checks == BAD_CHECKS
        assert policy.runs_on_gateway is False
        assert policy.updated_at == original_updated_at
        assert pushed_payloads == []
        assert (
            _build_payload(str(organization.id), config)["guardrails"].get(
                "key_policies", []
            )
            == []
        )
        assert first.json() == _error_envelope(
            "PATCH the policy with corrected checks, then retry apply."
        )

        corrected = auth_client.patch(
            detail_url, {"checks": GOOD_CHECKS}, format="json"
        )
        assert corrected.status_code == 200, corrected.json()
        assert corrected.json()["result"]["runs_on_gateway"] is True
        policy.refresh_from_db()
        assert policy.checks == GOOD_CHECKS
        assert policy.applied_keys == [str(original_key.id)]
        assert pushed_payloads[-1]["guardrails"]["key_policies"][0]["key_ids"] == [
            original_key.gateway_key_id
        ]

        applied = auth_client.post(
            apply_url, {"key_ids": [str(target_key.id)]}, format="json"
        )
        assert applied.status_code == 200, applied.json()
        result = applied.json()["result"]
        assert result["scope"] == "key"
        assert result["applied_keys"] == [str(target_key.id)]
        assert result["checks"] == GOOD_CHECKS
        assert result["is_active"] is True
        assert result["runs_on_gateway"] is True
        assert result["gateway_synced"] is True
        policy.refresh_from_db()
        assert (policy.scope, policy.applied_keys, policy.runs_on_gateway) == (
            "key",
            [str(target_key.id)],
            True,
        )
        assert policy.checks == GOOD_CHECKS
        assert len(pushed_payloads) == 2
        live = pushed_payloads[-1]["guardrails"]["key_policies"]
        assert len(live) == 1
        assert live[0]["id"] == str(policy.id)
        assert live[0]["key_ids"] == [target_key.gateway_key_id]
        assert live[0]["checks"]["secret-detection"]["confidence_threshold"] == 0.5


@pytest.mark.django_db
def test_patch_still_advertises_corrected_checks_in_the_same_save(
    auth_client, organization
):
    _, policy, _ = _setup(organization)
    response = auth_client.patch(
        f"{URL}{policy.id}/", {"mode": "enforce"}, format="json"
    )
    assert response.status_code == 400, response.json()
    assert response.json() == _error_envelope("Send corrected checks with this save.")
