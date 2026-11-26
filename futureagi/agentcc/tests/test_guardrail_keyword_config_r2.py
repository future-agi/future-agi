"""Selected keyword policy validation, recovery, and real push serialization."""

import copy
from unittest.mock import patch

import pytest

from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import _build_payload, push_org_config
from agentcc.tests.test_guardrail_key_policies_round5 import URL, _key

INVALID_CONFIGS = [
    {},
    None,
    "malformed-container",
    ["malformed-container"],
    {"words": "restricted"},
    {"words": None},
    {"words": []},
    {"words": [""]},
    {"words": [" \t\n"]},
    {"words": [42]},
    {"words": ["restricted", None]},
    {"words": ["restricted", ""]},
    {"words": ["restricted"], "whole_word": "false"},
    {"words": ["restricted"], "max_matches": 0},
    {"words": ["restricted"], "max_matches": True},
    {"words": ["restricted"], "max_matches": 1.5},
    {"words": ["restricted"], "max_matches": "2"},
    {"words": ["restricted"], "max_matches": 2**63},
    {"words": ["restricted"], "max_matches": 2**63 - 1},
]


def _check(config, **overrides):
    return {
        "name": "keyword-blocklist",
        "enabled": True,
        "action": "block",
        "threshold": 0.5,
        "config": copy.deepcopy(config),
        **overrides,
    }


def _body(key, checks, **overrides):
    return {
        "name": "keyword-r2-synthetic",
        "scope": "key",
        "mode": "enforce",
        "is_active": True,
        "applied_keys": [str(key.id)],
        "checks": checks,
        **overrides,
    }


def _stored(org, config=None, **overrides):
    key = _key(org)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        **_body(
            key,
            [_check({"words": "restricted"} if config is None else config)],
            **overrides,
        ),
    )
    org_config = AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        guardrails={"checks": {}, "fail_open": False},
    )
    return key, policy, org_config


@pytest.mark.django_db
@pytest.mark.parametrize("config", INVALID_CONFIGS)
def test_active_selected_create_rejects_unusable_keyword_config(
    auth_client, organization, config
):
    key = _key(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.post(URL, _body(key, [_check(config)]), format="json")
    assert response.status_code == 400, response.json()
    assert "keyword-blocklist" in str(response.json())
    assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
        organization=organization
    ).exists()
    client.assert_not_called()


@pytest.mark.django_db
def test_missing_config_rejected_but_omitted_enabled_is_effective(
    auth_client, organization
):
    key = _key(organization)
    check = _check({})
    del check["config"]
    del check["enabled"]
    response = auth_client.post(URL, _body(key, [check]), format="json")
    assert response.status_code == 400, response.json()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body", [{"name": "renamed"}, {"is_active": True}, {"mode": "monitor"}]
)
def test_unchanged_bad_checks_cannot_activate_dormant_row(
    auth_client, organization, body
):
    _, policy, config = _stored(organization)
    before = (policy.checks, policy.applied_keys, policy.name, policy.updated_at)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.patch(f"{URL}{policy.id}/", body, format="json")
    assert response.status_code == 400, response.json()
    policy.refresh_from_db()
    assert (
        policy.checks,
        policy.applied_keys,
        policy.name,
        policy.updated_at,
    ) == before
    assert not policy.runs_on_gateway
    assert not _build_payload(str(organization.id), config)["guardrails"].get(
        "key_policies"
    )
    client.assert_not_called()


@pytest.mark.django_db
def test_apply_requires_atomic_correction_with_intended_keys(auth_client, organization):
    original, policy, config = _stored(organization)
    target = _key(organization)
    corrected = [
        _check(
            {
                "words": ["restricted", "two words", "a.b", "wild*", " padded "],
                "whole_word": True,
                "max_matches": 2,
            }
        )
    ]
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        for body in (
            {"key_ids": [str(target.id)]},
            {"key_ids": [str(target.id)], "checks": corrected},
        ):
            response = auth_client.post(f"{URL}{policy.id}/apply/", body, format="json")
            assert response.status_code == 400, response.json()
            assert "PATCH" in str(response.json())
            assert (
                "Check at index 0 (keyword-blocklist): words must be a non-empty array of non-blank strings"
                in str(response.json())
            )
            policy.refresh_from_db()
            assert policy.applied_keys == [str(original.id)]
            assert policy.checks == [_check({"words": "restricted"})]
            assert not policy.runs_on_gateway
        client.assert_not_called()
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {"checks": corrected, "scope": "key", "applied_keys": [str(target.id)]},
            format="json",
        )
        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        policy.refresh_from_db()
        assert policy.checks == corrected
        assert policy.applied_keys == [str(target.id)]
        assert policy.runs_on_gateway
        pushed_org, payload = client.return_value.set_org_config.call_args.args
        assert pushed_org == str(organization.id)
        live = payload["guardrails"]["key_policies"]
        assert len(live) == 1
        assert live[0]["key_ids"] == [target.gateway_key_id]
        assert (
            live[0]["checks"]["keyword-blocklist"]["config"] == corrected[0]["config"]
        )
        assert payload == _build_payload(str(organization.id), config)


@pytest.mark.django_db
@pytest.mark.parametrize("config", INVALID_CONFIGS)
def test_deactivate_resubmitted_bad_keyword_checks_and_disable_remain_usable(
    auth_client, organization, config
):
    _, policy, org_config = _stored(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {"checks": [_check(config)], "is_active": False},
            format="json",
        )
        assert response.status_code == 200, response.json()
        policy.refresh_from_db()
        assert not policy.is_active
        assert not client.return_value.set_org_config.call_args.args[1][
            "guardrails"
        ].get("key_policies")
        rejected = auth_client.patch(
            f"{URL}{policy.id}/", {"is_active": True}, format="json"
        )
        assert rejected.status_code == 400, rejected.json()
        policy.refresh_from_db()
        assert not policy.is_active
        disabled = auth_client.patch(
            f"{URL}{policy.id}/",
            {
                "is_active": True,
                "checks": [
                    _check(config, enabled=False),
                    {"name": "secret-detection", "enabled": True, "action": "block"},
                ],
            },
            format="json",
        )
        assert disabled.status_code == 200, disabled.json()
        policy.refresh_from_db()
        assert policy.is_active
        live = _build_payload(str(organization.id), org_config)["guardrails"][
            "key_policies"
        ]
        assert len(live) == 1
        assert live[0]["checks"]["keyword-blocklist"]["enabled"] is False
        assert live[0]["checks"]["secret-detection"]["enabled"] is True
        assert policy.checks[0]["config"] == config


@pytest.mark.django_db
def test_project_dormancy_custom_checks_and_reserved_org_editor_unchanged(organization):
    key, policy, _ = _stored(organization, scope="project", applied_keys=[])
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={"name": "project-rename"},
        partial=True,
        context={"organization": organization},
    )
    assert serializer.is_valid(), serializer.errors
    reserved = AgentccGuardrailPolicy(
        organization=organization, name="__ui_guardrails__", scope="global"
    )
    serializer = AgentccGuardrailPolicySerializer(
        reserved, data={"checks": [_check({"words": "legacy"})]}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    custom = _check({"words": "custom-contract"}, name="deployed-custom-check")
    serializer = AgentccGuardrailPolicySerializer(
        data=_body(key, [custom]), context={"organization": organization}
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_failed_corrective_patch_preserves_assignment_checks_and_activation(
    auth_client, organization
):
    _, policy, _ = _stored(organization)
    from accounts.models import Organization

    foreign = _key(Organization.objects.create(name="Other synthetic org"))
    before = (policy.checks, policy.applied_keys, policy.runs_on_gateway)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {
                "checks": [_check({"words": ["restricted"]})],
                "scope": "key",
                "applied_keys": [str(foreign.id)],
            },
            format="json",
        )
    assert response.status_code == 400, response.json()
    policy.refresh_from_db()
    assert (policy.checks, policy.applied_keys, policy.runs_on_gateway) == before
    client.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_config", [{"words": "restricted"}, "malformed-container", [1]]
)
def test_legacy_live_bad_row_reaches_real_push_serialization(organization, bad_config):
    key, policy, config = _stored(organization, config=bad_config, runs_on_gateway=True)
    with patch("agentcc.services.config_push.get_gateway_client") as client:
        assert push_org_config(str(organization.id), config)
    payload = client.return_value.set_org_config.call_args.args[1]
    live = payload["guardrails"]["key_policies"][0]
    assert live["key_ids"] == [key.gateway_key_id]
    assert live["id"] == str(policy.id)
    expected = bad_config if isinstance(bad_config, dict) else {}
    assert live["checks"]["keyword-blocklist"]["config"] == expected
    # Runtime rejection of this exact supported payload belongs to the HTTP
    # config-push regression in guardrail_blocklist_config_r2_test.go.
