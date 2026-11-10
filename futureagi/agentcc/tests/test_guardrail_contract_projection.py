"""Stored editor checks must retain their meaning at the gateway boundary."""

import copy
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rest_framework.test import APIClient

from agentcc.contracts.gateway_admin import GuardrailConfig
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _transform_guardrails


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, 0.8),
        ({"threshold": 0}, 0),
        ({"confidenceThreshold": 0.4, "threshold": 0.9}, 0.4),
        (
            {"confidence_threshold": 0, "confidenceThreshold": 0.4, "threshold": 0.9},
            0,
        ),
    ],
)
def test_dict_check_threshold_aliases(fields, expected):
    result = _transform_guardrails(
        {"checks": {"pii-detector": {"mode": "sync", **fields}}}
    )

    check = GuardrailConfig.model_validate(result).checks["pii-detection"]
    assert check.confidence_threshold == expected
    assert check.enabled is True
    assert check.action == "block"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "lakera"),
        ({"provider": "custom"}, "custom"),
        ({"provider": "custom", "config": {"provider": "explicit"}}, "explicit"),
    ],
)
def test_dict_check_provider_precedence(fields, expected):
    result = _transform_guardrails(
        {"checks": {"lakera-guard": {"enabled": False, **fields}}}
    )

    check = GuardrailConfig.model_validate(result).checks["lakera-guard"]
    assert check.config["provider"] == expected
    assert check.enabled is False


@pytest.mark.parametrize(
    "options",
    [
        {"fail_open": True, "pipeline_mode": "sequential", "timeout_ms": 0},
        {"failOpen": True, "pipelineMode": "sequential", "timeoutMs": 0},
        {
            "fail_open": True,
            "failOpen": False,
            "pipeline_mode": "sequential",
            "pipelineMode": "parallel",
            "timeout_ms": 0,
            "timeoutMs": 2000,
        },
    ],
)
def test_dict_checks_preserve_contract_options(options):
    result = _transform_guardrails(
        {"checks": {"pii-detector": {}}, "enabled": True, **options}
    )

    contract = GuardrailConfig.model_validate(result)
    assert contract.fail_open is True
    assert contract.pipeline_mode == "sequential"
    assert contract.timeout_ms == 0


@pytest.mark.parametrize("rules", [None, []])
def test_empty_dict_checks_project_outer_metadata(rules):
    result = _transform_guardrails(
        {"checks": {}, "rules": rules, "enabled": False, "_meta": "editor"}
    )

    assert result == {"checks": {}, "fail_open": False, "pipeline_mode": "parallel"}
    assert GuardrailConfig.model_validate(result).checks == {}


def test_empty_dict_checks_keep_active_legacy_rules():
    # toggle-guardrail adds checks={} even when editing a legacy rules array.
    result = _transform_guardrails(
        {
            "checks": {},
            "rules": [{"name": "pii-detector", "enabled": True, "threshold": 0.6}],
        }
    )

    check = GuardrailConfig.model_validate(result).checks["pii-detection"]
    assert check.enabled is True
    assert check.confidence_threshold == 0.6


@pytest.mark.parametrize("invalid", [None, False, "invalid", []])
def test_malformed_checks_are_rejected_not_silently_dropped(invalid):
    result = _transform_guardrails(
        {"checks": {"futureagi-eval": invalid}}, org_id="synthetic-org"
    )

    with pytest.raises(ValidationError):
        GuardrailConfig.model_validate(result)


def test_dict_projection_keeps_credential_scope_and_input_immutable():
    guardrails = {
        "checks": {
            "futureagi-eval": {
                "enabled": True,
                "_policy": "synthetic-policy",
                "config": {"eval_ids": [42, None, ""], "api_key": "__encrypted__"},
            }
        }
    }
    original = copy.deepcopy(guardrails)

    def inject_policy(checks, org_id):
        assert org_id == "synthetic-org"
        assert checks["futureagi-eval"]["_policy"] == "synthetic-policy"
        checks["futureagi-eval"]["config"]["api_key"] = "synthetic-policy-value"

    def inject_platform(checks, org_id):
        assert org_id == "synthetic-org"
        assert "_policy" not in checks["futureagi-eval"]
        checks["futureagi-eval"]["config"]["secret_key"] = "synthetic-platform-value"

    with (
        patch(
            "agentcc.services.config_push._inject_guardrail_credentials",
            side_effect=inject_policy,
        ) as policy,
        patch(
            "agentcc.services.config_push._inject_fi_credentials",
            side_effect=inject_platform,
        ) as platform,
    ):
        result = _transform_guardrails(guardrails, org_id="synthetic-org")

    policy.assert_called_once()
    platform.assert_called_once()
    assert guardrails == original
    check = GuardrailConfig.model_validate(result).checks["futureagi-eval"]
    assert check.config == {
        "provider": "futureagi",
        "eval_ids": ["42"],
        "eval_id": "42",
        "api_key": "synthetic-policy-value",
        "secret_key": "synthetic-platform-value",
    }


@pytest.fixture
def editor_guardrails():
    return {
        "enabled": True,
        "rules": [{"name": "stale-rule"}],
        "checks": {
            "pii-detector": {
                "enabled": True,
                "action": "block",
                "mode": "sync",
                "stage": "pre",
                "threshold": 0.6,
                "config": {"entities": ["EMAIL_ADDRESS"]},
            }
        },
    }


@patch("agentcc.services.config_push.get_gateway_client")
def test_editor_save_activates_and_pushes_contract(
    gateway_client, auth_client, organization, editor_guardrails
):
    response = auth_client.post(
        "/agentcc/org-configs/", {"guardrails": editor_guardrails}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["result"]["gateway_synced"] is True
    config = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )
    assert config.guardrails["checks"]["pii-detector"]["stage"] == "pre"
    gateway_client.return_value.set_org_config.assert_called_once()
    org_id, payload = gateway_client.return_value.set_org_config.call_args.args
    assert org_id == str(organization.id)
    assert payload["guardrails"] == {
        "checks": {
            "pii-detection": {
                "enabled": True,
                "action": "block",
                "confidence_threshold": 0.6,
                "config": {"entities": ["EMAIL_ADDRESS"]},
            }
        },
        "fail_open": False,
        "pipeline_mode": "parallel",
    }


def test_bulk_sync_includes_editor_guardrails(organization, editor_guardrails):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        guardrails=editor_guardrails,
    )
    client = APIClient()
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", "synthetic-admin"):
        client.credentials(HTTP_AUTHORIZATION="Bearer synthetic-admin")
        response = client.get("/agentcc/org-configs/bulk/")

    assert response.status_code == 200
    body = response.json()
    assert body["unbuilt_org_ids"] == []
    config = GuardrailConfig.model_validate(
        body["result"][str(organization.id)]["guardrails"]
    )
    assert set(config.checks) == {"pii-detection"}
    assert config.checks["pii-detection"].confidence_threshold == 0.6
