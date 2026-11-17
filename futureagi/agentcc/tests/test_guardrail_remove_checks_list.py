"""RLINE-003: Rules removal must survive a sibling write and policy sync."""

import copy
from unittest.mock import patch

import pytest

from accounts.models import Organization
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.guardrail_sync import sync_guardrail_policies
from agentcc.tests.test_guardrail_switch_checks_list import (
    TOGGLE,
    UPDATE,
    _active,
    _pushed,
    gateway,  # noqa: F401 -- shared mocked gateway fixture
)

TOPIC = {
    "name": "topic-restriction",
    "enabled": True,
    "action": "block",
    "threshold": 0.5,
    "config": {"forbidden_topics": [{"name": "restricted", "keywords": ["forbidden"]}]},
}
LANGUAGE = {
    "name": "language-detection",
    "enabled": True,
    "action": "block",
    "config": {"allowed_scripts": ["latin"]},
}


def _policy(org, name="primary", checks=None, **kwargs):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name=name,
        scope=kwargs.pop("scope", AgentccGuardrailPolicy.SCOPE_GLOBAL),
        is_active=kwargs.pop("is_active", True),
        checks=copy.deepcopy([TOPIC, LANGUAGE] if checks is None else checks),
        **kwargs,
    )


def _start(org):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )
    policy = _policy(org)
    assert sync_guardrail_policies(org)
    return policy


def _remove(client, name="topic-restriction"):
    return client.delete(UPDATE, {"name": name}, format="json")


def _assert_removed(org, gateway):
    checks = _active(org).guardrails["checks"]
    assert not any(c["name"] == "topic-restriction" for c in checks)
    assert "topic-restriction" not in _pushed(gateway, org).get("checks", {})


@pytest.mark.integration
@pytest.mark.api
class TestRemovePolicyCheck:
    @pytest.mark.parametrize("sibling_write", ["toggle", "edit"])
    def test_removed_topic_stays_removed_after_sibling_write_and_sync(
        self, auth_client, organization, gateway, sibling_write
    ):
        policy = _start(organization)
        config = _active(organization)
        config.guardrails["timeout_ms"] = 3000
        config.save(update_fields=["guardrails"])

        response = _remove(auth_client)

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert response.json()["result"]["version"] > config.version
        policy.refresh_from_db()
        assert policy.checks == [LANGUAGE]
        assert _active(organization).guardrails["timeout_ms"] == 3000
        _assert_removed(organization, gateway)
        if sibling_write == "toggle":
            response = auth_client.post(
                TOGGLE, {"name": "language-detection", "enabled": False}, format="json"
            )
        else:
            response = auth_client.post(
                UPDATE,
                {"name": "language-detection", "config": {"enabled": False}},
                format="json",
            )
        assert response.status_code == 200, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        _assert_removed(organization, gateway)
        assert _pushed(gateway, organization)["checks"]["language-detection"]["enabled"] is False
        assert sync_guardrail_policies(organization)
        _assert_removed(organization, gateway)

    def test_removes_shadowed_sources_and_aliases_without_touching_inactive_policies(
        self, auth_client, organization, gateway
    ):
        _start(organization)
        # Both spellings feed the same gateway check, even across policies.
        sources = [
            _policy(organization, "shadow", [{"name": "pii-detector", "enabled": True}]),
            _policy(organization, "winner", [{"name": "pii-detector", "enabled": True}], priority=20),
            _policy(organization, "alias", [{"name": "pii-detection", "enabled": True}]),
        ]
        inactive = _policy(
            organization, "inactive", [{"name": "pii-detector"}], is_active=False
        )
        scoped = _policy(
            organization, "scoped", [{"name": "pii-detector"}], scope=AgentccGuardrailPolicy.SCOPE_PROJECT
        )
        assert sync_guardrail_policies(organization)
        response = _remove(auth_client, "pii-detector")
        assert response.status_code == 200, response.json()
        for policy in sources:
            policy.refresh_from_db()
            assert policy.checks == []
        for policy in [inactive, scoped]:
            policy.refresh_from_db()
            assert policy.checks == [{"name": "pii-detector"}]
        assert sync_guardrail_policies(organization)
        assert "pii-detection" not in _pushed(gateway, organization)["checks"]
        assert _pushed(gateway, organization)["checks"]["topic-restriction"]["enabled"] is True

    def test_removing_the_last_check_pushes_an_empty_section_and_is_idempotent(
        self, auth_client, organization, gateway
    ):
        policy = _start(organization)
        assert _remove(auth_client).status_code == 200
        response = _remove(auth_client, "language-detection")
        assert response.status_code == 200, response.json()
        assert _active(organization).guardrails == {"checks": []}
        assert _pushed(gateway, organization) == {"checks": {}}
        policy.refresh_from_db()
        assert policy.checks == []
        assert _remove(auth_client, "language-detection").status_code == 200
        assert sync_guardrail_policies(organization)
        assert _pushed(gateway, organization) == {"checks": {}}

    def test_validation_failure_rolls_back_policy_and_config_together(
        self, auth_client, organization, gateway
    ):
        policy = _start(organization)
        before = _active(organization).version
        pushes = gateway.set_org_config.call_count
        with patch.object(
            config_push, "validate_org_config", side_effect=config_push.OrgConfigRejected("rejected")
        ):
            response = _remove(auth_client)
        assert response.status_code == 400, response.json()
        policy.refresh_from_db()
        assert policy.checks == [TOPIC, LANGUAGE]
        assert _active(organization).version == before
        assert gateway.set_org_config.call_count == pushes

    def test_failed_push_reports_saved_but_not_applied_without_restoring_policy(
        self, auth_client, organization, gateway
    ):
        policy = _start(organization)
        gateway.set_org_config.side_effect = RuntimeError("gateway unavailable")
        response = _remove(auth_client)
        assert response.status_code == 200, response.json()
        result = response.json()["result"]
        assert result["gateway_synced"] is False
        assert result["gateway_warning"]
        policy.refresh_from_db()
        assert policy.checks == [LANGUAGE]

    def test_plain_checks_list_removal_preserves_siblings_without_creating_policies(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails={"enabled": True, "timeout_ms": 3000, "checks": [TOPIC, LANGUAGE]},
        )
        response = _remove(auth_client)
        assert response.status_code == 200, response.json()
        assert _active(organization).guardrails == {
            "enabled": True, "timeout_ms": 3000, "checks": [LANGUAGE]
        }
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(organization=organization).exists()

    def test_removal_never_changes_another_organizations_policy(
        self, auth_client, organization, gateway
    ):
        _start(organization)
        other = _policy(Organization.objects.create(name="Other removal organization"))
        response = _remove(auth_client)
        assert response.status_code == 200, response.json()
        other.refresh_from_db()
        assert other.checks == [TOPIC, LANGUAGE]
        assert all(call.args[0] == str(organization.id) for call in gateway.set_org_config.call_args_list)

    def test_removal_requires_gateway_admin_before_any_write(
        self, auth_client, organization, gateway
    ):
        policy = _start(organization)
        before = _active(organization).version
        pushes = gateway.set_org_config.call_count
        with patch("agentcc.permissions.is_gateway_admin", return_value=False):
            response = _remove(auth_client)
        assert response.status_code == 403, response.json()
        policy.refresh_from_db()
        assert policy.checks == [TOPIC, LANGUAGE]
        assert _active(organization).version == before
        assert gateway.set_org_config.call_count == pushes

    def test_rejects_bad_delete_body_without_changing_the_policy(
        self, auth_client, organization, gateway
    ):
        policy = _start(organization)
        response = auth_client.delete(
            UPDATE, {"name": "topic-restriction", "organization": "other"}, format="json"
        )
        assert response.status_code == 400, response.json()
        policy.refresh_from_db()
        assert policy.checks == [TOPIC, LANGUAGE]
