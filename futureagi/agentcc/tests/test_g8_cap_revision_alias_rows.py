"""Merge published agent aliases against rows saved before alias normalization."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from agentcc.contracts.gateway_admin import AgentsConfig
from agentcc.models.cap_activation import AgentccCapPolicyRevision
from agentcc.services.org_config_writer import write_config_version_with
from agentcc.tests.test_g8_cap_chokepoints import cap_sections
from agentcc.tests.test_g8_cap_revision_boundaries import (
    ORG_PUSH,
    ORG_URL,
    version_count,
)
from agentcc.tests.test_g8_cap_revision_cas import PUSH, URL, active, assert_conflict

LEGACY_AGENTS = {
    "keyBindings": {"key-a": "agent-a", "key-b": "agent-a"},
    "maxRunUSD": 0.1,
    "defaultModel": "m-old",
}
CANONICAL_AGENTS = {
    "key_bindings": {"key-a": "agent-a", "key-b": "agent-a"},
    "max_run_usd": 0.1,
    "default_model": "m-old",
}


def seed_stored_agents(organization, agents, policy="absent"):
    # The updater deliberately preserves the old API/MCP spelling on disk,
    # while still passing the real writer's validation/activation/transaction.
    def updater(config):
        config.routing = {"agents": deepcopy(agents), "fallback_enabled": True}
        if policy != "absent":
            config.budgets = cap_sections()["budgets"]
            config.budgets["admission_caps_enabled"] = policy == "enabled"

    saved, _ = write_config_version_with(organization, updater, push=lambda _: True)
    saved.refresh_from_db()
    assert saved.routing["agents"] == agents
    return saved


def assert_saved_agents(organization, before, expected, response):
    assert response.status_code == 200, response.content
    saved = active(organization)
    # Assert both storage and the effective contract value: a shadowed alias
    # must not survive to undo a deletion on a later write.
    assert saved.routing == {"agents": expected, "fallback_enabled": True}
    assert (
        AgentsConfig.model_validate(saved.routing["agents"]).model_dump(
            exclude_none=True
        )
        == expected
    )
    assert saved.version == before.version + 1
    assert response.json()["result"]["policy_revision"] == saved.budgets.get(
        "policy_revision", 0
    )
    if before.budgets.get("policy_revision"):
        assert saved.budgets["policy_revision"] > before.budgets["policy_revision"]
    else:
        assert not AgentccCapPolicyRevision.objects.filter(
            organization=organization
        ).exists()
    original_routing = deepcopy(before.routing)
    before.refresh_from_db()
    assert before.routing == original_routing, "Historical rows must not be rewritten"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestStoredAgentAliases:
    @pytest.mark.parametrize("policy", ["enabled", "disabled"])
    @pytest.mark.parametrize("field", ["keyBindings", "key_bindings"])
    @pytest.mark.parametrize("operation", ["delete", "merge", "entry-delete"])
    def test_binding_patch_preserves_merge_semantics(
        self, auth_client, organization, policy, field, operation
    ):
        before = seed_stored_agents(organization, LEGACY_AGENTS, policy)
        expected = deepcopy(CANONICAL_AGENTS)
        if operation == "delete":
            value = None
            expected.pop("key_bindings")
        elif operation == "merge":
            value = {"key-c": "agent-a"}
            expected["key_bindings"].update(value)
        else:
            value = {"key-b": None}
            expected["key_bindings"].pop("key-b")
        with patch(PUSH, return_value=True) as push:
            response = auth_client.post(
                URL,
                {
                    "routing": {"agents": {field: value}},
                    "budgets": {"policy_revision": before.budgets["policy_revision"]},
                },
                format="json",
            )
        assert_saved_agents(organization, before, expected, response)
        push.assert_called_once()

    @pytest.mark.parametrize("policy", ["enabled", "disabled"])
    @pytest.mark.parametrize("field", ["maxRunUSD", "maxRunUsd", "max_run_usd"])
    def test_run_ceiling_delete(self, auth_client, organization, policy, field):
        before = seed_stored_agents(organization, LEGACY_AGENTS, policy)
        expected = deepcopy(CANONICAL_AGENTS)
        expected.pop("max_run_usd")
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                URL,
                {
                    "routing": {"agents": {field: None}},
                    "budgets": {"policy_revision": before.budgets["policy_revision"]},
                },
                format="json",
            )
        assert_saved_agents(organization, before, expected, response)

    @pytest.mark.parametrize("stored_field", ["defaultModel", "default_model"])
    @pytest.mark.parametrize("patch_field", ["defaultModel", "default_model"])
    @pytest.mark.parametrize("value", [None, "m-new"])
    def test_caps_off_default_model_aliases(
        self, auth_client, organization, stored_field, patch_field, value
    ):
        # Includes round 2's fix: camelCase edits over canonical stored keys.
        before = seed_stored_agents(organization, {stored_field: "m-old"})
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                URL, {"routing": {"agents": {patch_field: value}}}, format="json"
            )
        expected = {} if value is None else {"default_model": value}
        assert_saved_agents(organization, before, expected, response)

    @pytest.mark.parametrize("policy", ["enabled", "disabled"])
    @pytest.mark.parametrize("revision", [None, 0])
    def test_rejected_edit_leaves_legacy_row_unchanged(
        self, auth_client, organization, policy, revision
    ):
        before = seed_stored_agents(organization, LEGACY_AGENTS, policy)
        count = version_count(organization)
        payload = {"routing": {"agents": {"keyBindings": {"key-b": None}}}}
        if revision is not None:
            payload["budgets"] = {"policy_revision": revision}
        with patch(PUSH, return_value=True) as push:
            response = auth_client.post(URL, payload, format="json")
        assert_conflict(response, before.budgets["policy_revision"])
        assert active(organization).pk == before.pk
        assert active(organization).routing["agents"] == LEGACY_AGENTS
        assert version_count(organization) == count
        push.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [{"cache": {"enabled": False}}, {"routing": {"fallback_enabled": False}}],
    )
    def test_unrelated_patch_does_not_rewrite_agent_aliases(
        self, auth_client, organization, payload
    ):
        before = seed_stored_agents(organization, LEGACY_AGENTS, "enabled")
        with patch(PUSH, return_value=True):
            response = auth_client.post(URL, payload, format="json")
        assert response.status_code == 200, response.content
        saved = active(organization)
        assert saved.routing["agents"] == LEGACY_AGENTS
        assert saved.budgets["policy_revision"] > before.budgets["policy_revision"]

    @pytest.mark.parametrize("policy", ["enabled", "disabled"])
    def test_section_replacement_does_not_merge_old_bindings(
        self, auth_client, organization, policy
    ):
        before = seed_stored_agents(organization, LEGACY_AGENTS, policy)
        with patch(ORG_PUSH, return_value=True):
            response = auth_client.post(
                ORG_URL,
                {
                    "routing": {"agents": {"keyBindings": {"key-c": "agent-a"}}},
                    "budgets": before.budgets,
                },
                format="json",
            )
        assert response.status_code == 200, response.content
        assert active(organization).routing == {
            "agents": {"key_bindings": {"key-c": "agent-a"}}
        }
