"""A check the console keeps only as stored stays stored and enforced.

The console lists the fourteen partner checks this release does not offer
and the tool permissions check read-only: it has no way to add one, turn one
on or change one (frontend constants/storedOnlyChecks.js). Hiding those
controls changes nothing on the server. A stored one stays in the org's
config byte for byte across any other save, and the config push sends it to
the gateway exactly as before, so a request it blocked is still blocked (the
gateway's own tests prove the block for the config pushed here). Turning one
off is an explicit, confirmed admin action through toggle-guardrail: it
changes that check's `enabled` and nothing else of it, and a Member cannot.
"""

import json
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import pushed_guardrails
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

GATEWAY = "/agentcc/gateways/default"
TOGGLE = f"{GATEWAY}/toggle-guardrail/"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"

PARTNERS = [
    "llama-guard",
    "azure-content-safety",
    "presidio-pii",
    "bedrock-guardrails",
    "hiddenlayer-guard",
    "aporia-guard",
    "pangea-guard",
    "dynamoai-guard",
    "enkrypt-guard",
    "ibm-ai-detector",
    "grayswan-guard",
    "lasso-guard",
    "crowdstrike-aidr",
    "zscaler-guard",
]
STORED_ONLY = [*PARTNERS, "tool-permissions"]

# The gateway builds the tool permissions check from a config whose provider
# is tool_permission (agentcc-gateway orgfactory, toolperm.IsToolPermConfig),
# as the console wrote it; this one denies sk_drop_table.
TOOL = {
    "name": "tool-permissions",
    "stage": "pre",
    "mode": "sync",
    "action": "block",
    "enabled": True,
    "config": {
        "provider": "tool_permission",
        "mode": "denylist",
        "tools": "sk_drop_table",
    },
}
PII = {
    "name": "pii-detector",
    "stage": "pre",
    "mode": "sync",
    "action": "block",
    "threshold": 0.7,
    "enabled": False,
    "config": {},
}


def _partner(name, enabled):
    # No credential: a stored partner check is kept whatever it holds, and a
    # credential would need the org's encrypted console policy.
    return {
        "name": name,
        "stage": "pre",
        "mode": "sync",
        "action": "block",
        "enabled": enabled,
        "config": {"endpoint": f"https://{name}.partner.invalid/check"},
        "note": "a key the console knows nothing of",
    }


RULES = {
    "rules": [
        TOOL,
        *[_partner(name, i % 2 == 0) for i, name in enumerate(PARTNERS)],
        PII,
    ],
    "timeout_ms": 2000,
}


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _make_config(organization, guardrails):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails=guardrails
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _stored_rules(organization):
    return {rule["name"]: rule for rule in _active(organization).guardrails["rules"]}


def _member_client(organization):
    member = User.objects.create_user(
        email="stored-only-member@futureagi.com",
        password="testpassword123",
        name="Stored Only Member",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=member,
        organization=organization,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.get(
        organization=organization, is_default=True
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=member,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=member)
    client.set_workspace(workspace)
    return client


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestStoredOnlyChecksAreKeptAndPushed:
    def test_the_push_sends_every_stored_only_check_as_stored(
        self, _push, organization
    ):
        _make_config(organization, RULES)

        pushed = pushed_guardrails(_active(organization))["checks"]

        for name in STORED_ONLY:
            assert name in pushed, name
        assert pushed["tool-permissions"] == {
            "enabled": True,
            "action": "block",
            "confidence_threshold": 0.8,
            "config": {
                "provider": "tool_permission",
                "mode": "denylist",
                "tools": "sk_drop_table",
            },
        }
        for i, name in enumerate(PARTNERS):
            assert pushed[name]["enabled"] is (i % 2 == 0), name
            assert (
                pushed[name]["config"]["endpoint"]
                == f"https://{name}.partner.invalid/check"
            )

    def test_another_section_saved_keeps_the_guardrails_and_their_push(
        self, _push, auth_client, organization
    ):
        _make_config(organization, RULES)
        before = _bytes(_active(organization).guardrails)
        pushed_before = _bytes(pushed_guardrails(_active(organization)))

        response = auth_client.post(
            UPDATE_CONFIG, {"routing": {"strategy": "least_latency"}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2
        assert _bytes(_active(organization).guardrails) == before
        assert _bytes(pushed_guardrails(_active(organization))) == pushed_before

    def test_a_guardrails_save_as_the_console_sends_it_keeps_each_stored_only_rule(
        self, _push, auth_client, organization
    ):
        # The Rules tab's save after the admin switches PII on: the rules
        # list whole, every stored-only rule exactly as stored.
        _make_config(organization, RULES)
        pushed_before = pushed_guardrails(_active(organization))["checks"]
        rules = [
            {**rule, "enabled": True} if rule["name"] == "pii-detector" else rule
            for rule in RULES["rules"]
        ]

        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": rules}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _stored_rules(organization)
        for rule in RULES["rules"][:-1]:
            assert _bytes(stored[rule["name"]]) == _bytes(rule), rule["name"]
        pushed = pushed_guardrails(_active(organization))["checks"]
        for name in STORED_ONLY:
            assert _bytes(pushed[name]) == _bytes(pushed_before[name]), name
        assert pushed["pii-detection"]["enabled"] is True

    @pytest.mark.parametrize("name", ["tool-permissions", "llama-guard"])
    def test_turning_one_off_changes_only_its_enabled(
        self, _push, auth_client, organization, name
    ):
        _make_config(organization, RULES)
        stored_before = _stored_rules(organization)
        pushed_before = pushed_guardrails(_active(organization))["checks"]

        response = auth_client.post(
            TOGGLE, {"name": name, "enabled": False}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _stored_rules(organization)
        assert list(stored) == list(stored_before)
        for rule_name, rule in stored_before.items():
            expected = {**rule, "enabled": False} if rule_name == name else rule
            assert _bytes(stored[rule_name]) == _bytes(expected), rule_name
        pushed = pushed_guardrails(_active(organization))["checks"]
        for check_name, check in pushed_before.items():
            expected = {**check, "enabled": False} if check_name == name else check
            assert _bytes(pushed[check_name]) == _bytes(expected), check_name

    def test_a_member_cannot_turn_one_off(self, _push, user, organization):
        _make_config(organization, RULES)
        before = _bytes(_active(organization).guardrails)

        response = _member_client(organization).post(
            TOGGLE, {"name": "tool-permissions", "enabled": False}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert _active(organization).version == 1
        assert _bytes(_active(organization).guardrails) == before


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestStoredOnlyChecksInAChecksMap:
    MAP = {
        "checks": {
            "tool-permissions": {
                "enabled": True,
                "action": "block",
                "config": {
                    "provider": "tool_permission",
                    "mode": "denylist",
                    "tools": "sk_drop_table",
                },
            },
            "zscaler-guard": {
                "enabled": True,
                "action": "warn",
                "confidence_threshold": 0.4,
                "config": {"provider": "zscaler", "tenant_id": "tenant-7"},
            },
            "pii-detection": {"enabled": False, "action": "block"},
        }
    }

    def test_the_console_patch_for_another_check_keeps_them_and_their_push(
        self, _push, auth_client, organization
    ):
        _make_config(organization, self.MAP)
        pushed_before = pushed_guardrails(_active(organization))["checks"]

        # What the Rules tab sends for a checks map when PII is switched on.
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"checks": {"pii-detection": {"enabled": True}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        checks = _active(organization).guardrails["checks"]
        for name in ("tool-permissions", "zscaler-guard"):
            assert _bytes(checks[name]) == _bytes(self.MAP["checks"][name]), name
        pushed = pushed_guardrails(_active(organization))["checks"]
        for name in ("tool-permissions", "zscaler-guard"):
            assert _bytes(pushed[name]) == _bytes(pushed_before[name]), name

    def test_turning_one_off_changes_only_its_enabled(
        self, _push, auth_client, organization
    ):
        _make_config(organization, self.MAP)

        response = auth_client.post(
            TOGGLE, {"name": "zscaler-guard", "enabled": False}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        expected = json.loads(json.dumps(self.MAP))
        expected["checks"]["zscaler-guard"]["enabled"] = False
        assert _bytes(_active(organization).guardrails) == _bytes(expected)
