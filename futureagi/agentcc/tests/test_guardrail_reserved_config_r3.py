"""Reserved policy scope must not become a public key-policy escape hatch."""

import copy
from unittest.mock import patch

import pytest

from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.tests.test_guardrail_key_policies_round5 import URL, _key

BAD_CHECKS = [{"name": "secret-detection", "action": "block", "threshold": 50}]
GOOD_CHECKS = [{"name": "secret-detection", "action": "block", "threshold": 0.5}]


def _snapshot(org):
    """Include all row fields, version/activation, assignment and gateway intent."""
    return copy.deepcopy(
        {
            "policies": list(
                AgentccGuardrailPolicy.no_workspace_objects.filter(organization=org)
                .order_by("id")
                .values()
            ),
            "keys": list(
                AgentccAPIKey.no_workspace_objects.filter(organization=org)
                .order_by("id")
                .values()
            ),
            "configs": list(
                AgentccOrgConfig.no_workspace_objects.filter(organization=org)
                .order_by("id")
                .values()
            ),
        }
    )


def _setup(org, *, scope="global", name="__ui_guardrails__", active=True):
    key = _key(org)
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name=name,
        scope=scope,
        checks=BAD_CHECKS,
        applied_keys=[str(key.id)] if scope == "key" else [],
        is_active=active,
        runs_on_gateway=False,
    )
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=7,
        is_active=True,
        guardrails={
            "checks": {
                "secret-detection": {
                    "enabled": True,
                    "action": "block",
                    "confidence_threshold": 50,
                }
            }
        },
    )
    return key, policy


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["__ui_guardrails__", "__legacy_internal__"])
@pytest.mark.parametrize(
    "operation", ["apply", "scope", "rename-and-repair", "rename-only"]
)
def test_reserved_global_refusal_is_atomic_even_with_corrected_checks(
    auth_client, organization, name, operation
):
    key, policy = _setup(organization, name=name)
    before = _snapshot(organization)
    with (
        patch("agentcc.services.config_push.get_gateway_client") as gateway,
        patch("agentcc.views.guardrail_policy.push_org_config") as push,
    ):
        if operation == "apply":
            response = auth_client.post(
                f"{URL}{policy.id}/apply/",
                {"key_ids": [str(key.id)], "checks": GOOD_CHECKS},
                format="json",
            )
        else:
            body = {"scope": "key", "applied_keys": [str(key.id)]}
            if operation == "rename-and-repair":
                body.update(name="ordinary-name", checks=GOOD_CHECKS)
            elif operation == "rename-only":
                body = {"name": "ordinary-name"}
            response = auth_client.patch(f"{URL}{policy.id}/", body, format="json")
    assert response.status_code == 400, response.json()
    assert "reserved" in str(response.json()).lower()
    assert _snapshot(organization) == before
    gateway.assert_not_called()
    push.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["__ui_guardrails__", "__legacy_internal__"])
@pytest.mark.parametrize(
    "change",
    [
        {"name": "repaired-name"},
        {"is_active": True},
        {"mode": "monitor"},
        {"checks": BAD_CHECKS},
    ],
)
def test_already_key_reserved_rows_get_effective_validation(
    auth_client, organization, name, change
):
    _, policy = _setup(organization, name=name, scope="key")
    before = _snapshot(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as gateway:
        response = auth_client.patch(f"{URL}{policy.id}/", change, format="json")
    assert response.status_code == 400, response.json()
    assert "threshold" in str(response.json())
    assert _snapshot(organization) == before
    gateway.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["__ui_guardrails__", "__legacy_internal__"])
def test_already_key_reserved_can_deactivate_then_repair_and_activate(
    auth_client, organization, name
):
    key, policy = _setup(organization, name=name, scope="key")
    with patch("agentcc.services.config_push.get_gateway_client"):
        response = auth_client.patch(
            f"{URL}{policy.id}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 200, response.json()
        policy.refresh_from_db()
        assert not policy.is_active
        assert policy.applied_keys == [str(key.id)]
        response = auth_client.patch(
            f"{URL}{policy.id}/",
            {"is_active": True, "checks": GOOD_CHECKS},
            format="json",
        )
        assert response.status_code == 200, response.json()
        policy.refresh_from_db()
        assert policy.is_active and policy.runs_on_gateway


@pytest.mark.django_db
@pytest.mark.parametrize(
    "flag", ["org_config_editor", "_org_config_editor", "is_org_config_row"]
)
def test_request_flags_cannot_grant_org_editor_compatibility(
    auth_client, organization, flag
):
    _, policy = _setup(organization)
    before = _snapshot(organization)
    with patch("agentcc.services.config_push.get_gateway_client") as gateway:
        response = auth_client.patch(
            f"{URL}{policy.id}/", {"checks": BAD_CHECKS, flag: True}, format="json"
        )
    assert response.status_code == 400, response.json()
    assert _snapshot(organization) == before
    gateway.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,scope",
    [
        ("__ui_guardrails__", "global"),
        ("__legacy_internal__", "global"),
        ("__ui_guardrails__", "key"),
    ],
)
def test_name_alone_never_grants_serializer_exemption(organization, name, scope):
    _, policy = _setup(organization, name=name, scope=scope)
    serializer = AgentccGuardrailPolicySerializer(
        policy, data={"checks": BAD_CHECKS}, partial=True
    )
    assert not serializer.is_valid()


@pytest.mark.django_db
def test_genuine_org_editor_keeps_threshold_50_and_global_sync(
    auth_client, organization
):
    _, policy = _setup(organization)
    guardrails = {
        "checks": {
            "secret-detection": {
                "enabled": True,
                "action": "block",
                "confidence_threshold": 50,
            }
        }
    }
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": guardrails}, format="json"
        )
    assert response.status_code == 200, response.json()
    policy.refresh_from_db()
    assert policy.scope == "global"
    assert policy.applied_keys == []
    assert not policy.runs_on_gateway
    assert policy.checks[0]["confidence_threshold"] == 50
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )
    assert active.version == 8
    assert active.guardrails["checks"]["secret-detection"]["confidence_threshold"] == 50
    push.assert_called_once()


@pytest.mark.parametrize("scope", ["global", "key"])
def test_reserved_name_without_server_context_has_no_compatibility_exemption(scope):
    policy = AgentccGuardrailPolicy(name="__ui_guardrails__", scope=scope)
    serializer = AgentccGuardrailPolicySerializer(
        policy, data={"checks": BAD_CHECKS}, partial=True
    )
    assert not serializer.is_valid()


def test_original_reserved_identity_cannot_be_renamed_into_key_scope():
    policy = AgentccGuardrailPolicy(
        name="__ui_guardrails__", scope="global", applied_keys=["existing-key"]
    )
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={"name": "public", "scope": "key", "checks": GOOD_CHECKS},
        partial=True,
    )
    assert not serializer.is_valid()


@pytest.mark.django_db
def test_org_editor_does_not_exempt_legacy_already_key_internal_row(
    auth_client, organization
):
    _, policy = _setup(organization, scope="key")
    before = _snapshot(organization)
    with patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
    ) as push:
        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "guardrails": {
                    "checks": {
                        "secret-detection": {
                            "action": "block",
                            "confidence_threshold": 50,
                        }
                    }
                }
            },
            format="json",
        )
    assert response.status_code == 400, response.json()
    assert _snapshot(organization) == before
    push.assert_not_called()
