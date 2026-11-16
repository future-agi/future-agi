"""Selected config transport isolates bad numbers without rewriting saved checks."""

import copy
import json
import math
import os
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import (
    _build_key_policy,
    _build_payload,
    _project_key_check,
)
from agentcc.services.gateway_client import GatewayClient
from agentcc.tests.test_guardrail_key_policies_round5 import _key

HUGE = 10**400
# Pin the promised transport budget, independently of the implementation constant.
CONFIG_DEPTH_LIMIT = 128
POLICY_ID = "00000000-0000-0000-0000-000000000001"
KEY_ID = "00000000-0000-0000-0000-000000000002"
ORG_ID = "00000000-0000-0000-0000-000000000003"
SIBLING = {
    "name": "keyword-blocklist",
    "enabled": True,
    "action": "block",
    "threshold": 0.5,
    "config": {"words": ["forbidden"], "future_option": [True, False, None]},
}


def _policy(checks):
    # Do not deep-copy here: the production boundary must handle deep/cyclic
    # in-memory values itself, before any recursive copy or serialization.
    return AgentccGuardrailPolicy(
        id=POLICY_ID,
        organization_id=ORG_ID,
        name="numeric-transport",
        mode="enforce",
        priority=100,
        scope="key",
        applied_keys=[KEY_ID],
        checks=checks,
        runs_on_gateway=True,
    )


def _entry(checks):
    return _build_key_policy(ORG_ID, _policy(checks), ["synthetic-key"])


def _org_roundtrip(entry):
    model = GatewayOrgConfig.model_validate(
        {"providers": {}, "guardrails": {"checks": {}, "key_policies": [entry]}}
    )
    encoded = model.model_dump_json(by_alias=True, exclude_none=True)
    restored = GatewayOrgConfig.model_validate_json(encoded)
    return restored.model_dump(by_alias=True, exclude_none=True)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("action", ["block", "warn", "log"])
@pytest.mark.parametrize(
    "config",
    [
        {"weights": {"toxicity": HUGE}},
        {"max_entities": HUGE},
        {"unknown": [{"deep": [-HUGE]}]},
        {"unknown": [float("nan")]},
        {"unknown": {"positive": float("inf")}},
        {"unknown": {"negative": -float("inf")}},
    ],
)
def test_bad_nested_numbers_only_replace_wire_config(config, enabled, action):
    raw = {
        "name": "pii-detection",
        "enabled": enabled,
        "action": action,
        "confidence_threshold": 0.35,
        "config": config,
    }
    before = copy.deepcopy(raw)
    entry = _entry([raw, SIBLING])
    check = entry["checks"]["pii-detection"]
    assert check == {
        "enabled": enabled,
        "action": action,
        "confidence_threshold": 0.35,
        "config": {},
        "configuration_error": "invalid_config",
    }
    assert entry["checks"][SIBLING["name"]] == _project_key_check(SIBLING)
    assert _org_roundtrip(entry)["guardrails"]["key_policies"] == [entry]
    assert raw == before
    assert raw["config"] is config


def test_opaque_json_and_finite_float64_edges_are_preserved_not_normalized():
    shared = {"null": None, "flags": [True, False], "empty": [[], {}, ""]}
    config = {
        "unknown": [shared, shared],
        "numbers": [
            sys.float_info.max,
            -sys.float_info.max,
            int(sys.float_info.max),
            5e-324,
            -5e-324,
            0,
            -0.0,
            2**53 + 1,
            2**63,
            "1e400",
        ],
    }
    raw = {"name": "custom-static", "threshold": 0, "config": config}
    entry = _entry([raw, SIBLING])
    check = entry["checks"]["custom-static"]
    assert "configuration_error" not in check
    assert check["confidence_threshold"] == 0
    assert check["config"] == config
    restored = _org_roundtrip(entry)["guardrails"]["key_policies"][0]["checks"]
    assert restored["custom-static"]["config"] == config
    assert math.copysign(1, restored["custom-static"]["config"]["numbers"][6]) == -1
    check["config"]["unknown"][0]["flags"].append("wire-only")
    assert shared["flags"] == [True, False]


def _nested_config(depth):
    config = {"leaf": "kept"}
    for _ in range(depth - 1):
        config = {"next": config}
    return config


def test_depth_budget_is_explicit_and_keeps_the_saved_json_and_siblings():
    for depth in (CONFIG_DEPTH_LIMIT, CONFIG_DEPTH_LIMIT + 1, 2000):
        config = _nested_config(depth)
        raw = {"name": "custom-static", "config": config}
        entry = _entry([raw, SIBLING])
        check = entry["checks"]["custom-static"]
        if depth == CONFIG_DEPTH_LIMIT:
            assert "configuration_error" not in check
            assert check["config"] == config
        else:
            assert check["configuration_error"] == "invalid_config"
            assert check["config"] == {}
        _org_roundtrip(entry)
        assert entry["checks"][SIBLING["name"]] == _project_key_check(SIBLING)
        assert raw["config"] is config
        cursor = config
        for _ in range(depth - 1):
            cursor = cursor["next"]
        assert cursor == {"leaf": "kept"}


@pytest.mark.parametrize("kind", ["object", "array"])
def test_non_json_cycles_are_unavailable_without_losing_the_policy(kind):
    config = {}
    config["cycle"] = config if kind == "object" else [config]
    entry = _entry([{"name": "custom-static", "config": config}, SIBLING])
    assert entry["checks"]["custom-static"]["configuration_error"] == "invalid_config"
    assert entry["checks"]["custom-static"]["config"] == {}
    assert entry["checks"][SIBLING["name"]] == _project_key_check(SIBLING)
    _org_roundtrip(entry)
    assert (config["cycle"] if kind == "object" else config["cycle"][0]) is config


@pytest.mark.parametrize("credential", [HUGE, "synthetic-secret"])
def test_injected_values_use_the_same_wire_boundary_without_mutating_saved_data(
    credential,
):
    raw = {"name": "custom-static", "config": {"api_key": "__encrypted__"}}
    policy = _policy([raw, SIBLING])
    policy.encrypted_check_configs = b"synthetic-ciphertext"
    with patch(
        "integrations.services.credentials.CredentialManager.decrypt",
        return_value={"custom-static": {"api_key": credential}},
    ):
        entry = _build_key_policy(ORG_ID, policy, ["synthetic-key"])
    check = entry["checks"]["custom-static"]
    if credential == HUGE:
        assert check["configuration_error"] == "invalid_config"
        assert check["config"] == {}
    else:
        assert "configuration_error" not in check
        assert check["config"] == {"api_key": credential}
    assert check["enabled"] is True
    assert check["action"] == "block"
    assert entry["checks"][SIBLING["name"]] == _project_key_check(SIBLING)
    assert raw["config"] == {"api_key": "__encrypted__"}
    assert policy.encrypted_check_configs == b"synthetic-ciphertext"


def _numeric_checks():
    return [
        {
            "name": "content-moderation",
            "enabled": False,
            "action": "block",
            "threshold": 0.8,
            "config": {"weights": {"toxicity": HUGE}},
        },
        {
            "name": "pii-detection",
            "enabled": True,
            "action": "block",
            "threshold": 0.5,
            "config": {"max_entities": HUGE},
        },
        copy.deepcopy(SIBLING),
    ]


def test_full_payload_and_client_serializer_match_real_go_decoder_fixture():
    policy = _policy(_numeric_checks())
    before = copy.deepcopy(policy.checks)
    config = SimpleNamespace(**dict.fromkeys(GatewayOrgConfig.model_fields))
    config.guardrails = {"checks": {}, "pipeline_mode": "parallel", "fail_open": False}
    key = SimpleNamespace(id=uuid.UUID(KEY_ID), gateway_key_id="synthetic-key")
    with (
        patch(
            "agentcc.services.config_push._load_guardrail_policies", return_value=[policy]
        ),
        patch("agentcc.services.config_push._assemble_providers", return_value={}),
        patch(
            "agentcc.models.AgentccAPIKey.no_workspace_objects.filter", return_value=[key]
        ),
    ):
        payload = _build_payload(ORG_ID, config)
    captured = []

    def capture(request):
        captured.append(request.content)
        return httpx.Response(200, json={"status": "ok"})

    with patch(
        "agentcc.services.gateway_client.httpx.Client",
        return_value=httpx.Client(transport=httpx.MockTransport(capture)),
    ):
        GatewayClient("http://synthetic.invalid").set_org_config(ORG_ID, payload)
    assert len(captured) == 1
    body = captured[0]
    # Export even on a red assertion, so cloud qualification can feed the exact
    # before/after HTTPX bytes to the Go handler test. No default file output.
    if output := os.environ.get("GUARDRAIL_R5_PAYLOAD_PATH"):
        Path(output).write_bytes(body)
    decoded = json.loads(body)
    assert (
        GatewayOrgConfig.model_validate_json(body).model_dump(
            by_alias=True, exclude_none=True
        )
        == payload
    )
    source = (
        Path(__file__).resolve().parents[3]
        / "agentcc-gateway/internal/server/r5_config_transport_test.go"
    ).read_text()
    fixture = re.search(r"const r5ProjectedOrgJSON = `([^`]+)`", source)
    assert fixture is not None
    assert decoded == json.loads(fixture.group(1))
    assert policy.checks == before


@pytest.mark.django_db
def test_saved_numeric_rows_and_sibling_policy_survive_full_payload_roundtrip(
    organization,
):
    key = _key(organization)
    checks = _numeric_checks()
    serializer = AgentccGuardrailPolicySerializer(
        data={
            "name": "saved-numeric-transport",
            "scope": "key",
            "mode": "enforce",
            "is_active": True,
            "applied_keys": [str(key.id)],
            "checks": checks,
        },
        context={"organization": organization},
    )
    assert serializer.is_valid(), serializer.errors
    policy = serializer.save(organization=organization, runs_on_gateway=True)
    sibling = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="sibling-policy",
        scope="key",
        applied_keys=[str(key.id)],
        checks=[{"name": "secret-detection", "action": "block"}],
        runs_on_gateway=True,
    )
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails={"checks": {}}
    )
    policy.refresh_from_db()
    before = copy.deepcopy(policy.checks)
    updated_at = policy.updated_at
    payload = _build_payload(str(organization.id), config)
    model = GatewayOrgConfig.model_validate(payload)
    restored = GatewayOrgConfig.model_validate_json(
        model.model_dump_json(by_alias=True, exclude_none=True)
    ).model_dump(by_alias=True, exclude_none=True)
    assert restored == payload
    live = {item["id"]: item for item in restored["guardrails"]["key_policies"]}
    assert set(live) == {str(policy.id), str(sibling.id)}
    selected = live[str(policy.id)]["checks"]
    for name, enabled, threshold in (
        ("content-moderation", False, 0.8),
        ("pii-detection", True, 0.5),
    ):
        assert selected[name] == {
            "enabled": enabled,
            "action": "block",
            "confidence_threshold": threshold,
            "configuration_error": "invalid_config",
            "config": {},
        }
    assert selected[SIBLING["name"]] == _project_key_check(SIBLING)
    assert live[str(sibling.id)]["checks"]["secret-detection"]["enabled"] is True
    assert (
        "configuration_error" not in live[str(sibling.id)]["checks"]["secret-detection"]
    )
    policy.refresh_from_db()
    config.refresh_from_db()
    sibling.refresh_from_db()
    assert policy.checks == before == checks
    assert policy.updated_at == updated_at
    assert config.guardrails == {"checks": {}}
    assert sibling.checks == [{"name": "secret-detection", "action": "block"}]
