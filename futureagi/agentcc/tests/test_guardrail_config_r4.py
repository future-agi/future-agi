"""Defensive selected configuration readiness; no request/provider reproduction."""

import copy
import itertools
import re
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from accounts.models import Organization
from agentcc.guardrail_config_validation import (
    MODERATION_COUNTS,
    SELECTORS,
    builtin_config_error,
    canonical_check_name,
)
from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import _RULE_TO_REGISTRY
from agentcc.tests.test_guardrail_config_transport_r3 import SIBLING, _build
from agentcc.tests.test_guardrail_key_policies_round5 import URL, _key

INJECTION = "prompt-injection"
MODERATION = "content-moderation"


@pytest.mark.parametrize("name", [INJECTION, "injection-detector"])
@pytest.mark.parametrize(
    "config",
    [
        {"categories": [42]},
        {"categories": ["unknown"]},
        {"categories": ["instruction_override", 42]},
        {"categories": None},
        {"sensitivity": None},
        {"sensitivity": 42},
        {"sensitivity": " high "},
        {"sensitivity": "unknown"},
        None,
        [],
    ],
)
def test_injection_original_input_rejected(name, config):
    assert builtin_config_error(name, config, 0.8)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"categories": []},
        {"sensitivity": "HIGH"},
        {"sensitivity": "Medium"},
        {"sensitivity": "low", "categories": sorted(SELECTORS[INJECTION][1])},
        {"future_option": {"untouched": [42]}},
    ],
)
def test_injection_defaults_and_known_options_preserved(config):
    original = copy.deepcopy(config)
    assert builtin_config_error(INJECTION, config, 0.8) is None
    assert config == original


@pytest.mark.parametrize(
    "count",
    [0, -1, 0.5, 50.5, True, None, "3", float("inf"), float("nan"), 2**63, 2**53 + 1],
)
def test_count_must_be_positive_integral_and_representable(count):
    assert builtin_config_error(MODERATION, {"category_threshold": count}, 0)


@pytest.mark.parametrize(
    "config,threshold,valid",
    [
        ({}, 0.8, True),
        ({}, 1, False),
        ({"categories": [], "category_threshold": 3}, 0.8, True),
        ({"categories": ["toxicity"], "category_threshold": 50}, 0.3, False),
        ({"categories": ["toxicity"], "category_threshold": 50}, 0.29, True),
        ({"categories": ["toxicity"], "category_threshold": 1000}, 0.01, True),
        ({"categories": ["toxicity"], "category_threshold": 1000}, 0.015, False),
        ({"categories": ["sexual"], "category_threshold": 20}, 0.65, False),
        ({"categories": ["sexual"], "category_threshold": 20}, 0.64, True),
        # Dominance boost is material: weighted average would be below .9.
        (
            {
                "categories": ["toxicity", "sexual"],
                "category_threshold": 16,
                "weights": {"sexual": 100},
            },
            0.9,
            True,
        ),
        (
            {
                "categories": ["toxicity", "sexual"],
                "category_threshold": 20,
                "weights": {"sexual": 3},
            },
            0.675,
            False,
        ),
        (
            {
                "categories": ["toxicity", "sexual"],
                "category_threshold": 20,
                "weights": {"sexual": 3},
            },
            0.674,
            True,
        ),
        (
            {
                "categories": ["toxicity", "toxicity", "sexual"],
                "category_threshold": 20,
            },
            0.7,
            False,
        ),
        ({"category_threshold": 2**62}, 0, True),
        (
            {"weights": {"toxicity": 0, "sexual": -1, "unused": {"keep": True}}},
            0.8,
            True,
        ),
        ({"weights": {"toxicity": "1"}}, 0.8, False),
        ({"weights": {"toxicity": True}}, 0.8, False),
        ({"weights": {"toxicity": float("inf")}}, 0.8, False),
        ({"weights": {"toxicity": 1e308, "sexual": 1e308}}, 0.8, False),
        ({"weights": None}, 0.8, False),
    ],
)
def test_moderation_constructor_score_boundaries(config, threshold, valid):
    assert (builtin_config_error(MODERATION, config, threshold) is None) is valid


def test_catalogs_and_aliases_match_actual_gateway_sources():
    root = Path(__file__).resolve().parents[3] / "agentcc-gateway/internal/guardrails"
    injection = (
        (root / "injection/injection.go")
        .read_text()
        .split("func defaultDetectors()", 1)[1]
    )
    assert (
        set(re.findall(r'category:\s*"([a-z_]+)"', injection))
        == SELECTORS[INJECTION][1]
    )
    moderation = (
        (root / "contentmod/contentmod.go")
        .read_text()
        .split("func defaultCategories()", 1)[1]
    )
    counts = {}
    for name, body in re.findall(r'"([a-z_]+)":\s*\{(.*?)\}', moderation, re.S):
        counts[name] = len(re.findall(r'^\s*"', body, re.M))
    assert counts == MODERATION_COUNTS
    for name, canonical in _RULE_TO_REGISTRY.items():
        assert canonical_check_name(name) == canonical


def _serializer(checks, *, active=True, scope="key", data=None):
    policy = AgentccGuardrailPolicy(
        scope=scope,
        checks=copy.deepcopy(checks),
        applied_keys=["existing"],
        is_active=active,
    )
    return AgentccGuardrailPolicySerializer(
        policy, data=data if data is not None else {"checks": checks}, partial=True
    )


@pytest.mark.parametrize("field", ["threshold", "confidence_threshold"])
def test_effective_threshold_is_used_when_checks_are_unchanged(field):
    checks = [
        {
            "name": MODERATION,
            field: 0.3,
            "config": {"categories": ["toxicity"], "category_threshold": 50},
        }
    ]
    assert not _serializer(checks, data={"mode": "monitor"}).is_valid()
    checks[0][field] = 0.29
    assert _serializer(checks, data={"mode": "monitor"}).is_valid()


@pytest.mark.parametrize(
    "name,config",
    [(INJECTION, {"categories": [42]}), (MODERATION, {"category_threshold": 50.5})],
)
def test_invalid_selected_values_recover_without_broadening_legacy_contract(
    name, config
):
    checks = [{"name": name, "config": config}]
    assert not _serializer(checks).is_valid()
    assert _serializer(checks, data={"is_active": False, "checks": checks}).is_valid()
    assert _serializer([{**checks[0], "enabled": False}]).is_valid()
    for scope in ("global", "project"):
        assert _serializer(checks, scope=scope).is_valid()
    assert _serializer([{"name": "custom-static", "config": config}]).is_valid()


PAIRS = [
    ("pii-detector", "pii-detection"),
    ("pii-detection", "pii-detection"),
    ("injection-detector", INJECTION),
    ("secrets-detector", "secret-detection"),
    ("custom-static", "custom-static"),
]


@pytest.mark.parametrize("names", PAIRS)
@pytest.mark.parametrize("enabled", list(itertools.product([True, False], repeat=2)))
@pytest.mark.parametrize("reverse", [False, True])
def test_active_collisions_rejected_in_either_order_and_inactive_keeps_raw(
    names, enabled, reverse
):
    checks = [
        {"name": name, "enabled": on, "config": {"extra": index}, "action": action}
        for index, (name, on, action) in enumerate(
            zip(names, enabled, ["block", "log"], strict=False)
        )
    ]
    if reverse:
        checks.reverse()
    serializer = _serializer(checks)
    assert not serializer.is_valid()
    assert "Duplicate canonical" in str(serializer.errors)
    assert _serializer(checks, data={"is_active": False, "checks": checks}).is_valid()
    assert _serializer(checks, scope="global").is_valid()


@pytest.mark.parametrize("names", PAIRS[:4])
@pytest.mark.parametrize("actions", [("block", "log"), ("warn", "log"), ("log", "log")])
@pytest.mark.parametrize("enabled", list(itertools.product([True, False], repeat=2)))
def test_live_collision_marker_is_order_independent_and_siblings_survive(
    names, actions, enabled
):
    rows = [
        {"name": name, "enabled": on, "action": action, "config": {"extra": index}}
        for index, (name, on, action) in enumerate(
            zip(names, enabled, actions, strict=False)
        )
    ]
    canonical = canonical_check_name(names[0])
    first = _build(rows + [SIBLING])["checks"]
    assert first == _build(list(reversed(rows)) + [SIBLING])["checks"]
    marker = first[canonical]
    assert marker["configuration_error"] == "invalid_config"
    assert marker["config"] == {}
    assert marker["enabled"] is any(enabled)
    intents = {action for action, on in zip(actions, enabled, strict=False) if on}
    assert marker["action"] == (
        "block" if "block" in intents else "warn" if "warn" in intents else "log"
    )
    assert first["custom-static"]["config"] == SIBLING["config"]


@pytest.mark.parametrize("owned", [True, False])
def test_collision_rejection_and_key_ownership_do_not_mutate_instance(owned):
    original, target = str(uuid.uuid4()), str(uuid.uuid4())
    organization = object()
    policy = AgentccGuardrailPolicy(
        scope="key",
        applied_keys=[original],
        checks=[{"name": "pii-detection"}],
        encrypted_check_configs=b"synthetic-old-ciphertext",
    )
    before = copy.deepcopy(
        (
            policy.checks,
            policy.applied_keys,
            policy.encrypted_check_configs,
            policy.runs_on_gateway,
        )
    )
    data = {
        "applied_keys": [target],
        "checks": [
            {
                "name": "pii-detector",
                "config": {"entities": ["email"], "api_key": "synthetic-new-value"},
            },
            {"name": "pii-detection", "config": {"entities": ["ssn"]}},
        ],
    }
    with (
        patch(
            "agentcc.serializers.guardrail_policy.AgentccAPIKey.no_workspace_objects.filter"
        ) as query,
        patch("agentcc.serializers.guardrail_policy._encrypt_secrets") as encrypt,
    ):
        query.return_value.values_list.return_value = [target] if owned else []
        serializer = AgentccGuardrailPolicySerializer(
            policy, data=data, partial=True, context={"organization": organization}
        )
        assert not serializer.is_valid()
        assert ("checks" if owned else "applied_keys") in serializer.errors
        query.assert_called_once_with(
            organization=organization, deleted=False, id__in=[target]
        )
        encrypt.assert_not_called()
    assert (
        policy.checks,
        policy.applied_keys,
        policy.encrypted_check_configs,
        policy.runs_on_gateway,
    ) == before


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["patch", "apply"])
@pytest.mark.parametrize("foreign", [False, True])
def test_rejected_collision_update_keeps_keys_checks_credentials_and_gateway_state(
    auth_client, organization, operation, foreign
):
    original = _key(organization)
    target = _key(
        Organization.objects.create(name="Foreign synthetic org")
        if foreign
        else organization
    )
    org_config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        guardrails={"checks": {"custom-static": {"enabled": True}}},
    )
    old_guards = copy.deepcopy(org_config.guardrails)
    checks = [
        {"name": "pii-detector", "config": {"entities": ["email"]}},
        {"name": "pii-detection", "config": {"entities": ["ssn"]}},
    ]
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        name="collision-recovery",
        scope="key",
        applied_keys=[str(original.id)],
        checks=checks,
        encrypted_check_configs=b"synthetic-ciphertext",
    )
    before = (
        copy.deepcopy(policy.checks),
        policy.applied_keys,
        bytes(policy.encrypted_check_configs),
        policy.runs_on_gateway,
        policy.updated_at,
    )
    with (
        patch("agentcc.services.config_push.get_gateway_client") as client,
        patch("agentcc.serializers.guardrail_policy._encrypt_secrets") as encrypt,
    ):
        if operation == "apply":
            response = auth_client.post(
                f"{URL}{policy.id}/apply/", {"key_ids": [str(target.id)]}, format="json"
            )
        else:
            response = auth_client.patch(
                f"{URL}{policy.id}/",
                {"applied_keys": [str(target.id)], "checks": checks},
                format="json",
            )
        assert response.status_code == 400
        client.assert_not_called()
        encrypt.assert_not_called()
    policy.refresh_from_db()
    assert (
        policy.checks,
        policy.applied_keys,
        bytes(policy.encrypted_check_configs),
        policy.runs_on_gateway,
        policy.updated_at,
    ) == before
    org_config.refresh_from_db()
    assert org_config.guardrails == old_guards
