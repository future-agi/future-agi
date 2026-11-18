"""Selected builtin configs: effective-state validation and lossless recovery."""

import copy
from unittest.mock import patch

import pytest

from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import _build_payload
from agentcc.tests.test_guardrail_key_policies_round5 import URL, _key

# These are the leaf constructor names, not views/guardrail_config.py's catalog.
INVALID_CONFIGS = [
    ("pii-detection", {"entities": [42]}),
    ("pii-detection", {"entities": ["email", "EMAIL_ADDRESS"]}),
    ("pii-detection", {"entities": ["not-a-detector"]}),
    ("pii-detection", {"entities": "email"}),
    ("content-moderation", {"categories": [42]}),
    ("content-moderation", {"categories": ["toxicity", "unknown"]}),
    ("content-moderation", {"categories": ["unknown"]}),
    ("secret-detection", {"types": [42]}),
    ("secret-detection", {"types": ["aws_access_key", "unknown"]}),
    ("secret-detection", {"types": ["unknown"]}),
    ("language-detection", {"allowed_scripts": "latin"}),
    ("language-detection", {"allowed_scripts": ["latin", 42]}),
    (
        "topic-restriction",
        {"forbidden_topics": [{"name": "restricted", "keywords": "restricted"}]},
    ),
    (
        "topic-restriction",
        {"allowed_topics": [{"name": "restricted", "keywords": "restricted"}]},
    ),
    ("topic-restriction", {"forbidden_topics": [{"keywords": ["restricted", 42]}]}),
    ("topic-restriction", {"allowed_topics": [42]}),
    ("input-validation", {"deny_patterns": ["["]}),
    ("input-validation", {"require_patterns": [42]}),
    ("input-validation", {"deny_patterns": ["valid", "["]}),
    ("input-validation", {"deny_patterns": ["(?=lookahead)"]}),
    ("input-validation", {"require_patterns": [r"(a)\1"]}),
]
FAMILIES = (
    "pii-detection",
    "content-moderation",
    "secret-detection",
    "language-detection",
    "topic-restriction",
    "input-validation",
)
VALID_CONFIGS = [
    ("pii-detection", {"entities": ["email", "ipv4", "ipv6", "mrn"]}),
    (
        "content-moderation",
        {"categories": ["toxicity", "hate_speech", "violence", "sexual", "self_harm"]},
    ),
    (
        "secret-detection",
        {"types": ["aws_access_key", "openai_key", "password_assign"]},
    ),
    ("language-detection", {"allowed_scripts": ["latin", "Greek"]}),
    (
        "topic-restriction",
        {
            "allowed_topics": [{"name": "support", "keywords": ["help"]}],
            "forbidden_topics": [{"keywords": ["restricted"]}],
        },
    ),
    (
        "input-validation",
        {
            "deny_patterns": [
                r"\p{Greek}+",
                "(?U)a.*b",
                "(?P<word>abc)",
                "(?<word>abc)",
            ],
            "require_patterns": ["^a.*$"],
            "max_message_length": 100,
            "min_message_length": 0,
            "max_total_length": 0,
        },
    ),
    ("pii-detection", {"entities": []}),
    ("content-moderation", {"categories": []}),
    ("secret-detection", {"types": []}),
    ("language-detection", {"allowed_scripts": []}),
    ("topic-restriction", {"allowed_topics": [], "forbidden_topics": []}),
    ("input-validation", {"deny_patterns": [], "require_patterns": []}),
]


def _check(name, config, **overrides):
    return {
        "name": name,
        "action": "block",
        "threshold": 0.5,
        "config": copy.deepcopy(config),
        **overrides,
    }


def _body(key, checks, **overrides):
    return {
        "name": "builtin-r3",
        "scope": "key",
        "applied_keys": [str(key.id)],
        "checks": checks,
        **overrides,
    }


def _stored(org, name, config, **overrides):
    key = _key(org)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org, **_body(key, [_check(name, config)], **overrides)
    )
    org_config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, guardrails={"checks": {}}
    )
    return key, policy, org_config


@pytest.mark.django_db
@pytest.mark.parametrize("name,config", INVALID_CONFIGS)
def test_active_create_rejects_builtin_config_before_save_or_push(
    auth_client, organization, name, config
):
    key = _key(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.post(
            URL, _body(key, [_check(name, config)]), format="json"
        )
    assert response.status_code == 400, response.json()
    assert name in str(response.json())
    assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
        organization=organization
    ).exists()
    client.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("name", FAMILIES)
@pytest.mark.parametrize("config", [None, [], "private-malformed-value", False, 0])
def test_active_create_rejects_nonobject_without_echoing_value(
    organization, name, config
):
    key = _key(organization)
    serializer = AgentccGuardrailPolicySerializer(
        data=_body(key, [_check(name, config)]), context={"organization": organization}
    )
    assert not serializer.is_valid()
    assert "private-malformed-value" not in str(serializer.errors)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,config", VALID_CONFIGS + [(name, {}) for name in FAMILIES]
)
def test_valid_constructor_settings_and_defaults_roundtrip(
    auth_client, organization, name, config
):
    key = _key(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.post(
            URL, _body(key, [_check(name, config)]), format="json"
        )
    assert response.status_code == 200, response.json()
    live = client.return_value.set_org_config.call_args.args[1]["guardrails"][
        "key_policies"
    ][0]
    assert live["checks"][name]["config"] == config


@pytest.mark.django_db
@pytest.mark.parametrize("name", FAMILIES)
def test_omitted_config_and_enabled_use_constructor_defaults(
    auth_client, organization, name
):
    key = _key(organization)
    with patch("agentcc.services.config_push.get_gateway_client"):
        response = auth_client.post(
            URL, _body(key, [{"name": name, "action": "block"}]), format="json"
        )
    assert response.status_code == 200, response.json()


@pytest.mark.django_db
@pytest.mark.parametrize("name,config", INVALID_CONFIGS)
@pytest.mark.parametrize("operation", ["rename", "activate", "monitor", "apply"])
def test_unchanged_bad_checks_cannot_become_live_and_old_key_intent_is_atomic(
    auth_client, organization, name, config, operation
):
    original, policy, org_config = _stored(organization, name, config)
    target = _key(organization)
    before = (
        policy.checks,
        policy.name,
        policy.scope,
        policy.applied_keys,
        policy.is_active,
        policy.updated_at,
    )
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        if operation == "apply":
            response = auth_client.post(
                f"{URL}{policy.id}/apply/",
                {"key_ids": [str(target.id)], "checks": [_check(name, {})]},
                format="json",
            )
            assert "PATCH" in str(response.json())
        else:
            body = {
                "rename": {"name": "renamed"},
                "activate": {"is_active": True},
                "monitor": {"mode": "monitor"},
            }[operation]
            response = auth_client.patch(f"{URL}{policy.id}/", body, format="json")
    assert response.status_code == 400, response.json()
    policy.refresh_from_db()
    assert (
        policy.checks,
        policy.name,
        policy.scope,
        policy.applied_keys,
        policy.is_active,
        policy.updated_at,
    ) == before
    assert policy.applied_keys == [str(original.id)]
    assert not policy.runs_on_gateway
    assert not _build_payload(str(organization.id), org_config)["guardrails"].get(
        "key_policies"
    )
    client.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,config",
    INVALID_CONFIGS
    + [(name, raw) for name in FAMILIES for raw in (None, [], False, 0, "malformed")],
)
def test_resubmitted_bad_config_can_deactivate_then_disable_without_losing_raw_value(
    auth_client, organization, name, config
):
    _, policy, org_config = _stored(organization, name, config)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {"is_active": False, "checks": [_check(name, config)]},
            format="json",
        )
        assert response.status_code == 200, response.json()
        response = auth_client.patch(
            f"{URL}{policy.id}/", {"is_active": True}, format="json"
        )
        assert response.status_code == 400, response.json()
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {
                "is_active": True,
                "checks": [
                    _check(name, config, enabled=False),
                    {"name": "custom-static", "config": {"words": "custom-contract"}},
                ],
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        assert client.return_value.set_org_config.called
    policy.refresh_from_db()
    assert policy.checks[0]["config"] == config
    live = _build_payload(str(organization.id), org_config)["guardrails"][
        "key_policies"
    ][0]["checks"]
    assert live[name]["enabled"] is False
    assert live["custom-static"]["enabled"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("name,config", INVALID_CONFIGS)
def test_live_invalid_objects_and_sibling_policies_survive_push(
    organization, name, config
):
    key, policy, org_config = _stored(organization, name, config, runs_on_gateway=True)
    policy.checks.append(
        {"name": "custom-static", "config": {"untouched": ["sibling"]}}
    )
    policy.save()
    sibling = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=organization,
        **_body(
            key,
            [{"name": "secret-detection"}],
            name="valid-sibling",
            runs_on_gateway=True,
        ),
    )
    live = {
        item["id"]: item
        for item in _build_payload(str(organization.id), org_config)["guardrails"][
            "key_policies"
        ]
    }
    assert set(live) == {str(policy.id), str(sibling.id)}
    assert live[str(policy.id)]["checks"][name]["config"] == config
    assert live[str(policy.id)]["checks"]["custom-static"]["config"] == {
        "untouched": ["sibling"]
    }
    policy.refresh_from_db()
    assert policy.checks[0]["config"] == config


@pytest.mark.django_db
@pytest.mark.parametrize("name,config", INVALID_CONFIGS)
@pytest.mark.parametrize("scope", ["global", "project"])
def test_builtin_semantic_validation_is_selected_only(
    organization, name, config, scope
):
    serializer = AgentccGuardrailPolicySerializer(
        data={"name": "legacy-static", "scope": scope, "checks": [_check(name, config)]}
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize("name,config", INVALID_CONFIGS)
def test_effective_selected_serializer_rejects_malformed_builtin(name, config):
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy, data={"checks": [_check(name, config)]}, partial=True
    )
    assert not serializer.is_valid()
    assert name in str(serializer.errors)
