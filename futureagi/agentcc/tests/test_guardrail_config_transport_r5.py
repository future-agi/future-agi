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
from pydantic import ValidationError
from rest_framework.renderers import JSONRenderer

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
from tfc.utils.general_methods import GeneralMethods

HUGE = 10**400
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
    assert check["config"]["unknown"][0] is check["config"]["unknown"][1]
    assert check["config"]["unknown"][0] is not shared
    check["config"]["unknown"][0]["flags"].append("wire-only")
    assert shared["flags"] == [True, False]


def _nested_config(depth, kind="object"):
    # Exactly depth containers, including the config root (always an object).
    config = {"leaf": "kept"}
    for level in range(depth - 1):
        config = (
            [config]
            if kind == "array" and level < depth - 2
            else {"next": config}
        )
    return config


def _nested_leaf(config, depth):
    for _ in range(depth - 1):
        config = config[0] if isinstance(config, list) else config["next"]
    return config


@pytest.mark.parametrize("depth", [129, 200])
@pytest.mark.parametrize("kind", ["object", "array"])
def test_opaque_depth_survives_actual_full_payload_transports(depth, kind):
    config = _nested_config(depth, kind)
    raw = {
        "name": "pii-detection",
        "enabled": True,
        "action": "block",
        "config": config,
    }
    policy = _policy([raw, SIBLING])
    payload = _build_test_payload(policy)
    body = _client_body(payload)
    bulk = _bulk_body(payload)
    # Retain exact base/final HTTPX and DRF bytes before the Go assertions.
    if output := os.environ.get("GUARDRAIL_R5_DEPTH_PAYLOAD_DIR"):
        Path(output, f"r5-depth-{depth}-{kind}.json").write_bytes(body)
        Path(output, f"r5-depth-{depth}-{kind}-bulk.json").write_bytes(bulk)
    decoded = json.loads(body)
    assert decoded == payload
    assert json.loads(bulk)["result"][ORG_ID] == payload
    checks = decoded["guardrails"]["key_policies"][0]["checks"]
    check = checks["pii-detection"]
    assert "configuration_error" not in check
    assert check["enabled"] is True
    assert check["action"] == "block"
    assert check["config"] == config
    assert _nested_leaf(check["config"], depth) == {"leaf": "kept"}
    assert checks[SIBLING["name"]] == _project_key_check(SIBLING)
    projected = payload["guardrails"]["key_policies"][0]["checks"]["pii-detection"]
    _nested_leaf(projected["config"], depth)["leaf"] = "wire-only"
    assert _nested_leaf(config, depth) == {"leaf": "kept"}
    assert raw["config"] is config


@pytest.mark.parametrize("kind", ["object", "array"])
def test_actual_serializer_depth_failure_is_isolated_not_a_depth_policy(kind):
    depth = max(2000, 2 * sys.getrecursionlimit())
    config = _nested_config(depth, kind)
    raw = {
        "name": "custom-static",
        "enabled": True,
        "action": "block",
        "config": config,
    }
    # Establish real transport rejection independently of the projection.
    unprojected = _build_test_payload(_policy([SIBLING]))
    unprojected["guardrails"]["key_policies"][0]["checks"]["custom-static"] = {
        "enabled": True,
        "action": "block",
        "config": config,
    }
    with pytest.raises((ValueError, RecursionError)):
        _client_body(unprojected)
    with pytest.raises((ValueError, RecursionError)):
        _bulk_body(unprojected)
    opaque_sibling = {"name": "opaque-sibling", "config": _nested_config(200)}
    payload = _build_test_payload(_policy([raw, SIBLING, opaque_sibling]))
    checks = json.loads(_client_body(payload))["guardrails"]["key_policies"][0]["checks"]
    assert "configuration_error" not in checks["opaque-sibling"]
    assert checks["opaque-sibling"]["config"] == opaque_sibling["config"]
    assert checks["custom-static"] == {
        "enabled": True,
        "action": "block",
        "confidence_threshold": 0.8,
        "config": {},
        "configuration_error": "invalid_config",
    }
    assert checks[SIBLING["name"]] == _project_key_check(SIBLING)
    assert json.loads(_bulk_body(payload))["result"][ORG_ID] == payload
    assert raw["config"] is config
    assert _nested_leaf(config, depth) == {"leaf": "kept"}


def test_full_payload_context_not_standalone_config_decides_transport():
    # Find the real runtime boundary, rather than pin another guessed constant.
    # This control specifically requires cfg-alone success/full-envelope failure.
    unprojected = _build_test_payload(_policy([SIBLING]))
    checks = unprojected["guardrails"]["key_policies"][0]["checks"]
    for depth in range(129, sys.getrecursionlimit() + 1):
        config = _nested_config(depth)
        checks["custom-static"] = {
            "enabled": True,
            "action": "block",
            "config": config,
        }
        try:
            _client_body(unprojected)
            _bulk_body(unprojected)
        except (ValueError, RecursionError):
            httpx.Request("PUT", "http://synthetic.invalid/", json=config)
            break
    else:
        pytest.fail("no full-envelope serializer boundary exercised")
    payload = _build_test_payload(
        _policy([{"name": "custom-static", "config": config}, SIBLING])
    )
    projected = payload["guardrails"]["key_policies"][0]["checks"]
    assert projected["custom-static"]["configuration_error"] == "invalid_config"
    assert projected["custom-static"]["config"] == {}
    assert projected[SIBLING["name"]] == _project_key_check(SIBLING)
    _client_body(payload)
    _bulk_body(payload)


def test_unrelated_org_error_is_not_hidden_by_selected_config_fallback():
    config = SimpleNamespace(**dict.fromkeys(GatewayOrgConfig.model_fields))
    config.guardrails = {"checks": {}}
    config.model_map = {"broken": []}
    raw = {"name": "custom-static", "config": _nested_config(200)}
    with pytest.raises(ValidationError):
        _build_test_payload(_policy([raw, SIBLING]), config)
    assert _nested_leaf(raw["config"], 200) == {"leaf": "kept"}
    assert "configuration_error" not in raw


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


@pytest.mark.parametrize("kind", ["nested", "cycle", "serializer_failure"])
def test_injected_containers_are_owned_and_checked_in_full_payload(kind):
    depth = 200 if kind == "nested" else max(2000, 2 * sys.getrecursionlimit())
    credential = _nested_config(depth)
    if kind == "cycle":
        credential = {}
        credential["cycle"] = credential
    raw = {"name": "custom-static", "config": {"api_key": "__encrypted__"}}
    policy = _policy([raw, SIBLING])
    policy.encrypted_check_configs = b"synthetic-ciphertext"
    with patch(
        "integrations.services.credentials.CredentialManager.decrypt",
        return_value={"custom-static": {"api_key": credential}},
    ):
        entry = _build_key_policy(ORG_ID, policy, ["synthetic-key"])
        payload = _build_test_payload(policy)
    check = payload["guardrails"]["key_policies"][0]["checks"]["custom-static"]
    if kind == "nested":
        assert "configuration_error" not in check
        assert check["config"]["api_key"] == credential
        # Exercise ownership before Pydantic/HTTPX could incidentally copy it.
        projected = entry["checks"]["custom-static"]["config"]["api_key"]
        _nested_leaf(projected, depth)["leaf"] = "wire-only"
        assert _nested_leaf(credential, depth) == {"leaf": "kept"}
    else:
        assert check["configuration_error"] == "invalid_config"
        assert check["config"] == {}
    assert raw["config"] == {"api_key": "__encrypted__"}
    assert policy.encrypted_check_configs == b"synthetic-ciphertext"
    checks = payload["guardrails"]["key_policies"][0]["checks"]
    assert checks[SIBLING["name"]] == _project_key_check(SIBLING)
    _client_body(payload)
    _bulk_body(payload)


def test_deep_eval_id_conversion_failure_does_not_remove_siblings():
    value = _nested_config(max(2000, 2 * sys.getrecursionlimit()))
    raw = {"name": "custom-static", "config": {"eval_id": value}}
    with pytest.raises(RecursionError):
        str(value)
    entry = _entry([raw, SIBLING])
    assert entry["checks"]["custom-static"]["configuration_error"] == "invalid_config"
    assert entry["checks"]["custom-static"]["config"] == {}
    assert entry["checks"][SIBLING["name"]] == _project_key_check(SIBLING)
    assert raw["config"]["eval_id"] is value


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


def _build_test_payload(policy, config=None):
    if config is None:
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
        return _build_payload(ORG_ID, config)


def _client_body(payload):
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
    return captured[0]


def _bulk_body(payload):
    response = GeneralMethods().success_response({ORG_ID: payload})
    response.data["unbuilt_org_ids"] = []
    return JSONRenderer().render(response.data)


def test_full_payload_and_client_serializer_match_real_go_decoder_fixture():
    policy = _policy(_numeric_checks())
    before = copy.deepcopy(policy.checks)
    payload = _build_test_payload(policy)
    body = _client_body(payload)
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
