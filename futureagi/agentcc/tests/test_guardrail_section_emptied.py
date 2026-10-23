"""A guardrails section that holds no check pushes, as an empty section the
gateway contract accepts, and the gateway stops running the org's old checks.

When an org's last guardrail policy that held a check is removed
(deactivated or deleted), or the rules editor saves a section with every
rule removed (``{"rules": [], "checks": []}``), the push still succeeds and
the gateway stops running the old checks; later saves that do not touch
guardrails (a budget, the cache) and the bulk sync
(``GET /agentcc/org-configs/bulk/``, which the gateway pulls at start-up and
on every reload) keep serving the org.

To that end:

- guardrail_sync stores an empty checks list when no active global policy
  holds a check;
- the push sends any section that holds no check, in any stored shape
  (the empty list sync stores, a ``{"enabled": false, "checks": []}``
  row, an emptied rules list), as ``{"checks": {}}``: a
  section the contract accepts, which the gateway decodes as no org check
  (agentcc-gateway internal/server org_guardrails_cleared_test.go decodes
  EMPTY_PUSHED, the exact section asserted here);
- so the removal is pushed, no later save is refused for it, and the bulk
  sync serves the org.

A section that is valid as stored (section settings with no check container,
or a non-empty rules list, checks list or checks map) is pushed as stored or
rebuilt (``UNCHANGED`` below).
"""

import secrets
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.guardrail_sync import sync_guardrail_policies
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
SET_BUDGET = f"{GATEWAY}/set-budget/"
POLICIES = "/agentcc/guardrail-policies/"
SYNC = f"{POLICIES}sync/"
BULK = "/agentcc/org-configs/bulk/"

# Generated per run: a published literal is refused (agentcc/admin_token.py).
ADMIN_TOKEN = secrets.token_hex(32)

# The section the push sends for a guardrails section that holds no check.
# org_guardrails_cleared_test.go (djangoPushedEmptySection) decodes exactly
# this into the org's tenant config and shows that the removed check does not run.
EMPTY_PUSHED = {"checks": {}}

# What the gateway gets for the one-policy org below before the removal: the
# check that must stop running.
BLOCKLIST_PUSHED = {
    "enabled": True,
    "action": "block",
    "confidence_threshold": 0.8,
    "config": {"words": ["t-forbidden"]},
}

# Saves that do not touch guardrails; each must succeed after the removal.
UNRELATED_SAVES = {
    "set-budget": (
        SET_BUDGET,
        {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
    ),
    "update-config-cache": (UPDATE_CONFIG, {"cache": {"enabled": True}}),
}


@pytest.fixture
def gateway():
    """Every push goes to a mock that records the payload it was sent."""
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


@pytest.fixture
def bulk_client():
    """The gateway's own client for the bulk sync (admin token)."""
    with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", ADMIN_TOKEN):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {ADMIN_TOKEN}")
        yield client


def _pushes(gateway, org):
    return [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]


def _last_pushed_guardrails(gateway, org):
    sent = _pushes(gateway, org)
    assert sent, "nothing was pushed"
    return sent[-1].get("guardrails")


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _pushed_guardrails(config):
    """The guardrails section a push of ``config`` sends (the payload the
    push and the bulk sync build, as the contract dumps it)."""
    return config_push._build_payload(str(config.organization_id), config).get(
        "guardrails"
    )


def _bulk_guardrails(bulk_client, org):
    """The org's guardrails section in the bulk sync, which serves the org."""
    response = bulk_client.get(BULK)
    assert response.status_code == status.HTTP_200_OK, response.json()
    served = response.json()["result"]
    assert str(org.id) in served, "the bulk sync skipped the org"
    return served[str(org.id)].get("guardrails")


def _one_policy_org(org, gateway):
    """An org whose only guardrail policy holds a blocking blocklist, synced:
    the gateway runs that check."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )
    policy = AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name="words",
        scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
        is_active=True,
        checks=[
            {
                "name": "keyword-blocklist",
                "enabled": True,
                "action": "block",
                "config": {"words": ["t-forbidden"]},
            }
        ],
    )
    assert sync_guardrail_policies(org)
    assert _last_pushed_guardrails(gateway, org)["checks"] == {
        "keyword-blocklist": BLOCKLIST_PUSHED
    }
    return policy


def _remove(auth_client, policy, how):
    if how == "deactivate":
        return auth_client.patch(
            f"{POLICIES}{policy.id}/", {"is_active": False}, format="json"
        )
    return auth_client.delete(f"{POLICIES}{policy.id}/")


@pytest.mark.integration
@pytest.mark.api
class TestRemovingTheLastPolicy:
    @pytest.mark.parametrize("how", ["deactivate", "delete"])
    def test_the_removal_is_pushed_as_an_empty_section(
        self, auth_client, organization, gateway, how
    ):
        policy = _one_policy_org(organization, gateway)
        pushes = len(_pushes(gateway, organization))

        response = _remove(auth_client, policy, how)

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert "gateway_warning" not in response.json()["result"]
        assert len(_pushes(gateway, organization)) == pushes + 1
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED
        stored = _active(organization).guardrails
        assert stored == {"checks": []}
        assert _pushed_guardrails(_active(organization)) == EMPTY_PUSHED

    @pytest.mark.parametrize("save", sorted(UNRELATED_SAVES))
    @pytest.mark.parametrize("how", ["deactivate", "delete"])
    def test_no_later_save_is_refused(
        self, auth_client, organization, gateway, how, save
    ):
        policy = _one_policy_org(organization, gateway)
        assert _remove(auth_client, policy, how).status_code == status.HTTP_200_OK

        url, body = UNRELATED_SAVES[save]
        response = auth_client.post(url, body, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED

    @pytest.mark.parametrize("how", ["deactivate", "delete"])
    def test_the_bulk_sync_still_serves_the_org(
        self, auth_client, bulk_client, organization, gateway, how
    ):
        """The gateway pulls each org's config from the bulk sync and drops
        an org that is not in it: after the removal the org is served, with
        the empty section, so its routing, budgets and provider keys stay."""
        policy = _one_policy_org(organization, gateway)
        assert _remove(auth_client, policy, how).status_code == status.HTTP_200_OK

        assert _bulk_guardrails(bulk_client, organization) == EMPTY_PUSHED

    def test_a_sync_with_no_check_left_pushes_an_empty_section(
        self, auth_client, organization, gateway
    ):
        """The same happens on any sync when no active global policy holds a
        check, e.g. one whose checks were all removed by a PATCH, then a
        manual sync."""
        policy = _one_policy_org(organization, gateway)
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/", {"checks": []}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED

        response = auth_client.post(SYNC, {}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED

    def test_a_policy_added_again_is_pushed(self, auth_client, organization, gateway):
        policy = _one_policy_org(organization, gateway)
        assert (
            _remove(auth_client, policy, "deactivate").status_code == status.HTTP_200_OK
        )
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/", {"is_active": True}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization)["checks"] == {
            "keyword-blocklist": BLOCKLIST_PUSHED
        }


# Sections with no check, as stored rows (and the rules
# editor) hold them.
EMPTY_SECTIONS = {
    "earlier-release-sync-row": {"enabled": False, "checks": []},
    "earlier-release-sync-row-enabled": {"enabled": True, "checks": []},
    "sync-row": {"checks": []},
    "emptied-rules-list": {"rules": [], "checks": []},
    "emptied-rules-only": {"rules": []},
    "empty-checks-map": {"checks": {}},
}


@pytest.mark.integration
@pytest.mark.api
class TestASectionWithNoCheck:
    @pytest.mark.parametrize("shape", sorted(EMPTY_SECTIONS))
    @pytest.mark.parametrize("save", sorted(UNRELATED_SAVES))
    def test_an_unrelated_save_on_such_a_row_saves_and_pushes(
        self, auth_client, organization, gateway, shape, save
    ):
        """An org whose active row is stored in that state is not locked: no
        repair save is needed."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=EMPTY_SECTIONS[shape],
        )
        url, body = UNRELATED_SAVES[save]
        response = auth_client.post(url, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED

    @pytest.mark.parametrize("shape", sorted(EMPTY_SECTIONS))
    def test_the_bulk_sync_serves_such_a_row(self, bulk_client, organization, shape):
        """Such a row is served by the bulk sync: no repair save is needed."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=EMPTY_SECTIONS[shape],
        )
        assert _bulk_guardrails(bulk_client, organization) == EMPTY_PUSHED

    def test_the_rules_editor_removing_every_rule_saves_and_pushes(
        self, auth_client, organization, gateway
    ):
        """The rules editor's save once its last rule is deleted: the rules
        list emptied, and a policy-written list emptied with it."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails={
                "rules": [
                    {
                        "name": "keyword-blocklist",
                        "enabled": True,
                        "action": "block",
                        "config": {"words": ["t-forbidden"]},
                    }
                ]
            },
        )
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [], "checks": []}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _last_pushed_guardrails(gateway, organization) == EMPTY_PUSHED


# Sections the push sends as stored or rebuilt. (The guardrails contract here
# has no tool_calls section, so there is no tool-calls-only row.)
UNCHANGED = {
    "settings-only": (
        {"fail_open": True, "pipeline_mode": "sequential"},
        {"fail_open": True, "pipeline_mode": "sequential"},
    ),
    "rules-list": (
        {
            "rules": [
                {"name": "keyword-blocklist", "config": {"words": ["x"]}},
            ],
            "fail_open": True,
        },
        {
            "checks": {
                "keyword-blocklist": {
                    "enabled": True,
                    "action": "block",
                    "confidence_threshold": 0.8,
                    "config": {"words": ["x"]},
                }
            },
            "fail_open": True,
            "pipeline_mode": "parallel",
        },
    ),
    "checks-map": (
        {"checks": {"keyword-blocklist": {"enabled": False}}},
        {"checks": {"keyword-blocklist": {"enabled": False}}},
    ),
}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("shape", sorted(UNCHANGED))
def test_a_valid_section_is_pushed_as_stored_or_rebuilt(organization, shape):
    stored, pushed = UNCHANGED[shape]
    config = AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails=stored
    )
    assert _pushed_guardrails(config) == pushed


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "malformed",
    [
        {"rules": {"keyword-blocklist": {}}},
        {"checks": "keyword-blocklist"},
    ],
    ids=["rules-not-a-list", "checks-a-string"],
)
def test_a_malformed_section_is_still_refused(
    auth_client, organization, gateway, malformed
):
    """Only a section that holds no check is normalised: a rules value that
    is not a list, or checks that are neither a list nor a map, still answer
    400 and are never pushed."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True
    )
    response = auth_client.post(UPDATE_CONFIG, {"guardrails": malformed}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert _active(organization).version == 1
    gateway.set_org_config.assert_not_called()
