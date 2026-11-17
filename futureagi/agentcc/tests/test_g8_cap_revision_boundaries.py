"""Cap CAS boundaries: legacy budget actions, routing edits and write receipts."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from agentcc.models.cap_activation import AgentccCapPolicyRevision
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests.test_g8_cap_chokepoints import cap_sections
from agentcc.tests.test_g8_cap_revision_cas import PUSH, URL, active, assert_conflict

ORG_URL = "/agentcc/org-configs/"
ORG_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
CAP_LEVELS = [
    "admission_caps_enabled",
    "admissionCapsEnabled",
    "agents",
    "key_ids",
    "keyIds",
    "policy_revision",
    "policyRevision",
]
ROUTING_FIELDS = [
    "key_bindings",
    "keyBindings",
    "max_run_usd",
    "maxRunUsd",
    "maxRunUSD",
]


def seed(auth_client, organization, policy):
    if policy == "absent":
        return
    sections = cap_sections()
    sections["budgets"]["admission_caps_enabled"] = policy == "enabled"
    sections["routing"]["agents"]["max_run_usd"] = 0.1
    with patch(PUSH, return_value=True):
        response = auth_client.post(URL, sections, format="json")
    assert response.status_code == 200, response.content
    return active(organization)


def version_count(organization):
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=organization
    ).count()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestLegacyBudgetCapBoundary:
    @pytest.mark.parametrize("policy", ["absent", "enabled", "disabled"])
    @pytest.mark.parametrize("action", ["set-budget", "remove-budget"])
    @pytest.mark.parametrize("level", CAP_LEVELS)
    def test_cap_levels_are_refused(
        self, auth_client, organization, policy, action, level
    ):
        saved = seed(auth_client, organization, policy)
        count = version_count(organization)
        payload = {"level": level}
        if action == "set-budget":
            # The legacy request requires an object, even for scalar cap levels.
            # Reject the level before trying to interpret this object as a cap.
            payload["config"] = {"agent-a": {"limit": 9, "period": "daily"}}
        with patch(PUSH, return_value=True) as push:
            response = auth_client.post(
                f"/agentcc/gateways/default/{action}/", payload, format="json"
            )
        assert response.status_code == 400, response.content
        assert "update-config" in response.content.decode()
        assert "budgets.policy_revision" in response.content.decode()
        assert version_count(organization) == count
        push.assert_not_called()
        if saved:
            assert active(organization).pk == saved.pk
            assert active(organization).budgets == saved.budgets
        else:
            assert not AgentccCapPolicyRevision.objects.filter(
                organization=organization
            ).exists()

    @pytest.mark.parametrize("level", ["org", "organization", "teams"])
    def test_non_cap_levels_still_work(self, auth_client, organization, level):
        seed(auth_client, organization, "enabled")
        value = {"limit": 2, "period": "daily"}
        if level == "teams":
            value = {"engineering": value}
        canonical = level
        revision = active(organization).budgets["policy_revision"]
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                "/agentcc/gateways/default/set-budget/",
                {"level": level, "config": value},
                format="json",
            )
            assert response.status_code == 200, response.content
            assert active(organization).budgets[canonical] == value
            assert active(organization).budgets["policy_revision"] > revision
            response = auth_client.post(
                "/agentcc/gateways/default/remove-budget/",
                {"level": level},
                format="json",
            )
            assert response.status_code == 200, response.content
        assert canonical not in active(organization).budgets
        assert active(organization).budgets["admission_caps_enabled"] is True


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestRoutingCapRevision:
    @pytest.mark.parametrize("url", [URL, ORG_URL])
    @pytest.mark.parametrize("policy", ["enabled", "disabled"])
    @pytest.mark.parametrize("field", ROUTING_FIELDS)
    def test_routing_edit_requires_current_revision(
        self, auth_client, organization, url, policy, field
    ):
        saved = seed(auth_client, organization, policy)
        revision = saved.budgets["policy_revision"]
        value = {"key-a": "agent-z"} if field.startswith("key") else 0.2
        payload = {"routing": {"agents": {field: value}}}
        with (
            patch(PUSH, return_value=True) as push,
            patch(ORG_PUSH, return_value=True) as org_push,
        ):
            assert_conflict(auth_client.post(url, payload, format="json"), revision)
            assert active(organization).pk == saved.pk
            push.assert_not_called()
            org_push.assert_not_called()
            # POST replaces named sections: preserve the full budget envelope.
            payload["budgets"] = deepcopy(saved.budgets) if url == ORG_URL else {}
            payload["budgets"]["policy_revision"] = revision - 1
            assert_conflict(auth_client.post(url, payload, format="json"), revision)
            payload["budgets"]["policy_revision"] = revision
            response = auth_client.post(url, payload, format="json")
            assert response.status_code == 200, response.content
        stored = active(organization)
        canonical = "key_bindings" if field.startswith("key") else "max_run_usd"
        assert stored.routing["agents"][canonical] == value
        assert stored.budgets["policy_revision"] > revision

    @pytest.mark.parametrize("url", [URL, ORG_URL])
    @pytest.mark.parametrize(
        "routing",
        [
            {"agents": None},
            {"agents": {"key_bindings": None}},
            {"agents": {"max_run_usd": None}},
        ],
    )
    def test_routing_deletion_requires_revision(
        self, auth_client, organization, url, routing
    ):
        saved = seed(auth_client, organization, "disabled")
        with patch(PUSH, return_value=True), patch(ORG_PUSH, return_value=True):
            response = auth_client.post(url, {"routing": routing}, format="json")
            assert_conflict(response, saved.budgets["policy_revision"])
            assert active(organization).pk == saved.pk
            response = auth_client.post(
                url, {"routing": routing, "budgets": saved.budgets}, format="json"
            )
        assert response.status_code == 200, response.content
        assert (
            active(organization).budgets["policy_revision"]
            > saved.budgets["policy_revision"]
        )

    @pytest.mark.parametrize(
        "routing",
        [None, {}, {"agents": {}}, {"agents": {"default_model": "test-model"}}],
    )
    def test_routing_replacement_requires_revision(
        self, auth_client, organization, routing
    ):
        saved = seed(auth_client, organization, "disabled")
        with patch(ORG_PUSH, return_value=True):
            response = auth_client.post(ORG_URL, {"routing": routing}, format="json")
            assert_conflict(response, saved.budgets["policy_revision"])
            assert active(organization).pk == saved.pk
            response = auth_client.post(
                ORG_URL, {"routing": routing, "budgets": saved.budgets}, format="json"
            )
        assert response.status_code == 200, response.content
        assert (
            active(organization).budgets["policy_revision"]
            > saved.budgets["policy_revision"]
        )

    @pytest.mark.parametrize("field", ROUTING_FIELDS)
    def test_no_policy_needs_no_revision(self, auth_client, organization, field):
        value = {"key-a": "agent-a"} if field.startswith("key") else 0.2
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                URL, {"routing": {"agents": {field: value}}}, format="json"
            )
        assert response.status_code == 200, response.content
        assert not active(organization).budgets.get("policy_revision")

    def test_non_cap_routing_merge_needs_no_revision(self, auth_client, organization):
        saved = seed(auth_client, organization, "enabled")
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                URL,
                {"routing": {"agents": {"default_model": "test-model"}}},
                format="json",
            )
        assert response.status_code == 200, response.content
        assert (
            active(organization).routing["agents"]["key_bindings"]
            == saved.routing["agents"]["key_bindings"]
        )

    def test_cleared_policy_retains_routing_fence(self, auth_client, organization):
        saved = seed(auth_client, organization, "disabled")
        with patch(ORG_PUSH, return_value=True):
            response = auth_client.post(
                ORG_URL,
                {"budgets": {"policy_revision": saved.budgets["policy_revision"]}},
                format="json",
            )
        assert response.status_code == 200, response.content
        revision = active(organization).budgets["policy_revision"]
        with patch(PUSH, return_value=True):
            response = auth_client.post(
                URL, {"routing": {"agents": {"max_run_usd": 0.2}}}, format="json"
            )
        assert_conflict(response, revision)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestConfigWriteRevisionReceipt:
    @pytest.mark.parametrize("url", [URL, ORG_URL])
    @pytest.mark.parametrize("policy", ["absent", "enabled", "disabled"])
    @pytest.mark.parametrize("synced", [True, False])
    def test_success_returns_saved_revision(
        self, auth_client, organization, url, policy, synced
    ):
        saved = seed(auth_client, organization, policy)
        before = saved.budgets["policy_revision"] if saved else 0
        with patch(PUSH, return_value=synced), patch(ORG_PUSH, return_value=synced):
            response = auth_client.post(
                url, {"cache": {"enabled": False}}, format="json"
            )
            assert response.status_code == 200, response.content
            result = response.json()["result"]
            revision = active(organization).budgets.get("policy_revision", 0)
            assert result["policy_revision"] == revision
            assert result["gateway_synced"] is synced
            assert revision > before if saved else revision == 0
            payload = cap_sections()
            payload["budgets"]["policy_revision"] = result["policy_revision"]
            response = auth_client.post(url, payload, format="json")
            assert response.status_code == 200, response.content
            assert (
                response.json()["result"]["policy_revision"]
                == active(organization).budgets["policy_revision"]
            )
            assert response.json()["result"]["policy_revision"] > revision

    @pytest.mark.parametrize("url", [URL, ORG_URL])
    def test_declared_200_revision(self, url):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        swagger = json.loads((root / "api_contracts/openapi/swagger.json").read_text())
        path = url.replace("default", "{id}")
        response = swagger["paths"][path]["post"]["responses"]["200"]["schema"]

        def resolve(schema):
            return (
                swagger["definitions"][schema["$ref"].rsplit("/", 1)[-1]]
                if "$ref" in schema
                else schema
            )

        result = resolve(resolve(response)["properties"]["result"])
        assert "policy_revision" in result["required"]
        assert result["properties"]["policy_revision"]["type"] == "integer"
        assert result["properties"]["policy_revision"]["minimum"] == 0
