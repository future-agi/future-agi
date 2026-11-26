"""Catalog provenance, narrow compatibility and the declared regex limitation."""

from pathlib import Path

import pytest

from agentcc.guardrail_config_validation import SELECTORS, builtin_config_error
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import (
    _ORG_CONFIG_EDITOR_CONTEXT,
    AgentccGuardrailPolicySerializer,
)


@pytest.mark.parametrize(
    "name,package,function",
    [
        ("pii-detection", "pii", "defaultPatterns"),
        ("content-moderation", "contentmod", "defaultCategories"),
        ("secret-detection", "secrets", "defaultPatterns"),
    ],
)
def test_selector_catalog_is_pinned_to_original_go_leaf(name, package, function):
    root = Path(__file__).resolve().parents[3]
    source = (
        root / "agentcc-gateway" / "internal" / "guardrails" / package / f"{package}.go"
    ).read_text()
    body = source.split(f"func {function}()", 1)[1].split("\nfunc ", 1)[0]
    catalog = set()
    for line in body.splitlines():
        line = line.strip()
        if line.startswith('"'):
            parts = line.split('"', 2)
            if len(parts) == 3 and parts[2].lstrip().startswith(":"):
                catalog.add(parts[1])
    field, declared = SELECTORS[name]
    assert declared == catalog
    assert builtin_config_error(name, {field: sorted(catalog)}) is None


@pytest.mark.parametrize(
    "name",
    [
        "pii-detection",
        "content-moderation",
        "secret-detection",
        "language-detection",
        "topic-restriction",
        "input-validation",
    ],
)
@pytest.mark.parametrize("scope", ["global", "project"])
def test_nonselected_public_envelope_validation_is_not_relaxed(name, scope):
    serializer = AgentccGuardrailPolicySerializer(
        data={
            "name": "public",
            "scope": scope,
            "checks": [{"name": name, "config": "malformed"}],
        }
    )
    assert not serializer.is_valid()


@pytest.mark.parametrize(
    "scope,name,context",
    [
        ("global", "__ui_guardrails__", {"org_config_editor": True}),
        (
            "global",
            "__legacy_internal__",
            {"org_config_editor": _ORG_CONFIG_EDITOR_CONTEXT},
        ),
        ("key", "__ui_guardrails__", {"org_config_editor": _ORG_CONFIG_EDITOR_CONTEXT}),
    ],
)
def test_compatibility_needs_server_token_exact_identity_and_global_scope(
    scope, name, context
):
    policy = AgentccGuardrailPolicy(name=name, scope=scope)
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={"checks": [{"name": "pii-detection", "threshold": 50}]},
        partial=True,
        context=context,
    )
    assert not serializer.is_valid()


def test_structurally_valid_bad_repetition_is_left_to_authoritative_go_runtime():
    # Deliberate limit: Go regexp rejects a repeat >1000. The bounded Python
    # scanner does not parse repetitions and must not be described as RE2 parity.
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={
            "checks": [
                {"name": "input-validation", "config": {"deny_patterns": ["a{1001}"]}}
            ]
        },
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "name,config",
    [
        ("pii-detector", {"entities": [42]}),
        ("secrets-detector", {"types": [42]}),
    ],
)
def test_existing_push_aliases_receive_the_same_effective_builtin_validation(
    name, config
):
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy, data={"checks": [{"name": name, "config": config}]}, partial=True
    )
    assert not serializer.is_valid()
