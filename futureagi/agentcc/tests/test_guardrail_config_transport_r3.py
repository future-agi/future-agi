"""Malformed legacy selected checks remain unavailable, never disappear in push."""

import copy
import json
import uuid
from unittest.mock import patch

import pytest

from agentcc.contracts.gateway_admin import (
    KeyGuardrailPolicy as GatewayKeyGuardrailPolicy,
)
from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.config_push import (
    _build_key_policy,
    _build_payload,
    push_org_config,
)
from agentcc.tests.test_guardrail_builtin_config_r3 import FAMILIES
from agentcc.tests.test_guardrail_key_policies_round5 import _key

SIBLING = {"name": "custom-static", "config": {"untouched": ["sibling"]}}


def _policy(checks):
    return AgentccGuardrailPolicy(
        organization_id=uuid.uuid4(),
        name="synthetic-legacy",
        scope="key",
        checks=copy.deepcopy(checks),
        runs_on_gateway=True,
    )


def _build(checks):
    policy = _policy(checks)
    before = copy.deepcopy(policy.checks)
    entry = _build_key_policy(str(policy.organization_id), policy, ["synthetic-key"])
    GatewayKeyGuardrailPolicy.model_validate(entry)
    assert policy.checks == before
    return entry


@pytest.mark.parametrize("name", FAMILIES + ("keyword-blocklist", "custom-malformed"))
@pytest.mark.parametrize(
    "config", [None, False, 0, "", [], [42], "private-malformed-value"]
)
def test_nonobject_config_carries_marker_and_keeps_original_name_and_sibling(
    name, config
):
    entry = _build([{"name": name, "config": config}, SIBLING])
    assert entry["key_ids"] == ["synthetic-key"]
    assert entry["checks"][name]["configuration_error"] == "invalid_config"
    assert entry["checks"][name]["enabled"] is True
    assert entry["checks"][name]["action"] == "block"
    assert entry["checks"]["custom-static"]["config"] == SIBLING["config"]
    assert "configuration_error" not in entry["checks"]["custom-static"]
    assert "private-malformed-value" not in json.dumps(entry)


@pytest.mark.parametrize(
    "envelope",
    [None, "private-envelope", 42, [], {}, {"name": []}, {"name": 42}, {"name": ""}],
)
def test_unusable_envelope_retains_a_failed_placeholder_and_valid_sibling(envelope):
    entry = _build([envelope, SIBLING])
    assert entry["checks"]["custom-static"]["config"] == SIBLING["config"]
    failed = [
        check for check in entry["checks"].values() if check.get("configuration_error")
    ]
    assert len(failed) == 1
    assert failed[0]["configuration_error"] == "invalid_config"
    assert failed[0]["enabled"] is True
    assert failed[0]["action"] == "block"
    assert "private-envelope" not in json.dumps(entry)


@pytest.mark.parametrize("checks", [None, "private-envelope", {}, 42])
def test_unusable_checks_container_keeps_policy_with_failed_placeholder(checks):
    entry = _build(checks)
    assert entry["name"] == "synthetic-legacy"
    assert len(entry["checks"]) == 1
    check = next(iter(entry["checks"].values()))
    assert check["configuration_error"] == "invalid_config"
    assert check["action"] == "block"


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", "false"),
        ("enabled", None),
        ("action", []),
        ("action", None),
        ("threshold", "private-threshold"),
        ("threshold", True),
        ("confidence_threshold", []),
        ("confidence_threshold", None),
    ],
)
def test_untyped_envelope_fields_are_marked_instead_of_coerced_or_dropping_policy(
    field, value
):
    entry = _build(
        [{"name": "pii-detection", field: value, "config": {"entities": [42]}}, SIBLING]
    )
    check = entry["checks"]["pii-detection"]
    assert check["configuration_error"] == "invalid_config"
    assert check["config"] == {"entities": [42]}
    assert entry["checks"]["custom-static"]["config"] == SIBLING["config"]
    assert "private-threshold" not in json.dumps(entry)


@pytest.mark.parametrize(
    "config", [{"entities": [42]}, {"entities": ["email", "invalid"]}]
)
def test_invalid_object_config_is_not_rewritten_into_defaults(config):
    entry = _build(
        [{"name": "pii-detection", "threshold": 50, "config": config}, SIBLING]
    )
    check = entry["checks"]["pii-detection"]
    assert check["config"] == config
    assert check["confidence_threshold"] == 50
    assert "configuration_error" not in check


@pytest.mark.parametrize("name", FAMILIES)
def test_disabled_malformed_config_remains_disabled_in_transport(name):
    entry = _build([{"name": name, "enabled": False, "config": [42]}, SIBLING])
    assert entry["checks"][name]["enabled"] is False
    assert entry["checks"][name]["configuration_error"] == "invalid_config"


def test_omission_and_intentionally_empty_checks_are_not_configuration_errors():
    assert _build([])["checks"] == {}
    check = _build([{"name": "pii-detection"}])["checks"]["pii-detection"]
    assert check["config"] == {}
    assert "configuration_error" not in check


@pytest.mark.django_db
@pytest.mark.parametrize(
    "checks",
    [
        [{"name": "pii-detection", "config": "private-malformed-value"}, SIBLING],
        [None, SIBLING],
        "private-malformed-envelope",
    ],
)
def test_real_payload_and_push_keep_failed_policy_sibling_and_database_unchanged(
    organization, checks
):
    key = _key(organization)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="malformed-live",
        scope="key",
        applied_keys=[str(key.id)],
        checks=checks,
        runs_on_gateway=True,
    )
    sibling = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="valid-sibling",
        scope="key",
        applied_keys=[str(key.id)],
        checks=[{"name": "secret-detection"}],
        runs_on_gateway=True,
    )
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails={"checks": {}}
    )
    before = copy.deepcopy(policy.checks)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        assert push_org_config(str(organization.id), config)
    payload = client.return_value.set_org_config.call_args.args[1]
    assert payload == _build_payload(str(organization.id), config)
    live = {item["id"]: item for item in payload["guardrails"]["key_policies"]}
    assert set(live) == {str(policy.id), str(sibling.id)}
    assert live[str(policy.id)]["key_ids"] == [key.gateway_key_id]
    assert any(
        check.get("configuration_error") == "invalid_config"
        for check in live[str(policy.id)]["checks"].values()
    )
    policy.refresh_from_db()
    assert policy.checks == before
