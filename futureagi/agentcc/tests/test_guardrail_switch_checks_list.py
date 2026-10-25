"""The Guardrails page's switch and edit work on a section a guardrail policy
sync wrote (a checks list), and what they change survives the next sync.

guardrail_sync rewrites the org's guardrails section from its active global
policies, as a checks list whose entries name their policy (`_policy`), on
every policy create, update, delete, apply or sync. On such a section
toggle-guardrail and update-guardrail change the check in the policy it came
from (a check no policy holds goes into the console's own policy), and the
version they write holds the section that sync writes, so the gateway runs the
change and later syncs keep it. A list no policy wrote is edited as a list.

The rules list and the checks map are covered in test_guardrail_env_reference.py
TestSwitchingAnEnvRefCheckOff and test_guardrail_overview_checks_map.py;
test_every_shape_is_switched_off_and_pushed here runs all three side by side.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.guardrail_credentials import CONSOLE_GUARDRAIL_POLICY
from agentcc.services.guardrail_sync import sync_guardrail_policies
from agentcc.views import gateway as gateway_views
from integrations.services.credentials import CredentialManager

GATEWAY = "/agentcc/gateways/default"
TOGGLE = f"{GATEWAY}/toggle-guardrail/"
UPDATE = f"{GATEWAY}/update-guardrail/"
SET_BUDGET = f"{GATEWAY}/set-budget/"
POLICIES = "/agentcc/guardrail-policies/"
SENTINEL = "__encrypted__"
# Test values, not real credentials.
LAKERA_KEY = "lakera-key-test-value"
NEW_LAKERA_KEY = "lakera-key-two-test-value"
ENV_REF = "${GATEWAY_TEST_VAR}"


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _pushed(gateway, org):
    sent = [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]
    assert sent, "nothing was pushed"
    return sent[-1]["guardrails"]


def _pushed_check(gateway, org, name):
    return _pushed(gateway, org)["checks"].get(name)


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _lakera(org, *, key=LAKERA_KEY, name="vendor", enabled=True):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name=name,
        scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
        is_active=True,
        priority=1,
        checks=[
            {
                "name": "lakera-guard",
                "enabled": enabled,
                "action": "block",
                "threshold": 0.7,
                "config": {"provider": "lakera", "api_key": SENTINEL},
            }
        ],
        encrypted_check_configs=CredentialManager.encrypt(
            {"lakera-guard": {"api_key": key}}
        ),
    )


def _words(org):
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name="words",
        scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
        is_active=True,
        priority=2,
        checks=[
            {
                "name": "keyword-blocklist",
                "enabled": True,
                "action": "block",
                "config": {"words": ["blocked-word"]},
            }
        ],
    )


def _synced(org, **lakera):
    """An org whose section a policy sync wrote: a lakera policy and a
    blocklist policy, both pushed."""
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )
    vendor, words = _lakera(org, **lakera), _words(org)
    assert sync_guardrail_policies(org)
    section = _active(org).guardrails
    assert isinstance(section["checks"], list)
    assert {check["_policy"] for check in section["checks"]} == {
        str(vendor.id),
        str(words.id),
    }
    return vendor, words


def _entry(org, name):
    (entry,) = [c for c in _active(org).guardrails["checks"] if c["name"] == name]
    return entry


def _dialog_edit(entry, **changes):
    """What the Guardrails page's edit dialog sends for a check of a checks
    list (EditGuardrailDialog, not a checks map): the check as listed, its
    `_policy` and the other list keys included, with the edited fields."""
    sent = {key: value for key, value in entry.items() if key != "name"}
    sent.update(changes)
    return sent


def _touch(auth_client, policy):
    """A later policy sync: any policy save rewrites the section."""
    response = auth_client.patch(
        f"{POLICIES}{policy.id}/", {"description": "touched"}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["result"]["gateway_synced"] is True


def _assert_list_section(org):
    section = _active(org).guardrails
    assert isinstance(section.get("checks"), list), section
    assert "rules" not in section, section


@pytest.mark.integration
@pytest.mark.api
class TestTheSwitchOnAPolicyList:
    @pytest.mark.parametrize("enabled", [False, True])
    def test_the_switch_is_pushed_and_survives_the_next_sync(
        self, auth_client, organization, gateway, enabled
    ):
        vendor, words = _synced(organization, enabled=not enabled)
        versions = _active(organization).version

        response = auth_client.post(
            TOGGLE, {"name": "lakera-guard", "enabled": enabled}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["gateway_synced"] is True
        assert result["enabled"] is enabled
        assert result["version"] == _active(organization).version > versions
        assert (
            _pushed_check(gateway, organization, "lakera-guard")["enabled"] is enabled
        )
        # The rest of what the gateway runs is unchanged.
        pushed = _pushed_check(gateway, organization, "lakera-guard")
        assert pushed["config"]["api_key"] == LAKERA_KEY
        assert pushed["action"] == "block"
        assert pushed["confidence_threshold"] == 0.7
        assert _pushed_check(gateway, organization, "keyword-blocklist")["enabled"]
        # Changed where the next sync reads it, in its own policy.
        vendor.refresh_from_db()
        assert vendor.checks[0]["enabled"] is enabled
        assert vendor.checks[0]["config"]["api_key"] == SENTINEL
        _assert_list_section(organization)

        _touch(auth_client, words)
        assert (
            _pushed_check(gateway, organization, "lakera-guard")["enabled"] is enabled
        )

    def test_switching_on_a_check_whose_policy_holds_an_env_reference_is_refused(
        self, auth_client, organization, gateway
    ):
        """As on the other shapes (U10): a check whose stored credential is a
        ${VAR} may be switched off, never on."""
        vendor, _ = _synced(organization, key=ENV_REF, enabled=False)
        pushes = gateway.set_org_config.call_count
        before = (_active(organization).version, copy.deepcopy(vendor.checks))

        response = auth_client.post(
            TOGGLE, {"name": "lakera-guard", "enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_env_reference"
        assert "lakera-guard" in response.json()["message"]
        assert ENV_REF not in str(response.json())
        vendor.refresh_from_db()
        assert (_active(organization).version, vendor.checks) == before
        assert gateway.set_org_config.call_count == pushes

    def test_switching_off_a_check_whose_policy_holds_an_env_reference_works(
        self, auth_client, organization, gateway
    ):
        vendor, words = _synced(organization, key=ENV_REF)
        response = auth_client.post(
            TOGGLE, {"name": "lakera-guard", "enabled": False}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
        _touch(auth_client, words)
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False

    def test_the_gateway_refuses_such_a_check_switched_on_through_the_policy_api(
        self, auth_client, organization, gateway
    ):
        """U10, F5: whichever path switches such a check on, the gateway refuses
        that check without a call."""
        vendor, _ = _synced(organization, key=ENV_REF, enabled=False)
        response = auth_client.patch(
            f"{POLICIES}{vendor.id}/",
            {"checks": [{**vendor.checks[0], "enabled": True}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(gateway, organization, "lakera-guard")
        assert pushed["enabled"] is True
        assert pushed["config"]["api_key"] == ENV_REF

    def test_a_check_no_policy_holds_goes_into_the_console_policy(
        self, auth_client, organization, gateway
    ):
        _, words = _synced(organization)

        response = auth_client.post(
            TOGGLE, {"name": "pii-detector", "enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(gateway, organization, "pii-detection")
        assert pushed["enabled"] is True
        assert pushed["action"] == "block"
        assert pushed["confidence_threshold"] == 0.8
        console = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization, name=CONSOLE_GUARDRAIL_POLICY, deleted=False
        )
        assert [check["name"] for check in console.checks] == ["pii-detector"]
        _assert_list_section(organization)
        _touch(auth_client, words)
        assert _pushed_check(gateway, organization, "pii-detection")["enabled"] is True
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is True


@pytest.mark.integration
@pytest.mark.api
class TestTheEditOnAPolicyList:
    def test_the_dialogs_edit_is_pushed_and_survives_the_next_sync(
        self, auth_client, organization, gateway
    ):
        vendor, words = _synced(organization)
        entry = _entry(organization, "lakera-guard")

        response = auth_client.post(
            UPDATE,
            {
                "name": "lakera-guard",
                "config": _dialog_edit(entry, action="log", threshold=0.4, mode="sync"),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = _pushed_check(gateway, organization, "lakera-guard")
        assert pushed["action"] == "log"
        assert pushed["confidence_threshold"] == 0.4
        assert pushed["enabled"] is True
        # The stored credential is kept: the dialog sends it as the sentinel.
        assert pushed["config"]["api_key"] == LAKERA_KEY
        vendor.refresh_from_db()
        (stored,) = vendor.checks
        assert stored["action"] == "log"
        assert stored["threshold"] == 0.4
        assert not any(key.startswith("_") for key in stored)
        _assert_list_section(organization)

        _touch(auth_client, words)
        pushed = _pushed_check(gateway, organization, "lakera-guard")
        assert (pushed["action"], pushed["confidence_threshold"]) == ("log", 0.4)

    def test_a_new_credential_is_stored_encrypted_in_the_checks_policy(
        self, auth_client, organization, gateway
    ):
        vendor, _ = _synced(organization)
        entry = _entry(organization, "lakera-guard")
        config = {**entry["config"], "api_key": NEW_LAKERA_KEY}

        response = auth_client.post(
            UPDATE,
            {"name": "lakera-guard", "config": _dialog_edit(entry, config=config)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(gateway, organization, "lakera-guard")
        assert pushed["config"]["api_key"] == NEW_LAKERA_KEY
        vendor.refresh_from_db()
        assert vendor.checks[0]["config"]["api_key"] == SENTINEL
        assert CredentialManager.decrypt(bytes(vendor.encrypted_check_configs)) == {
            "lakera-guard": {"api_key": NEW_LAKERA_KEY}
        }
        assert NEW_LAKERA_KEY not in str(_active(organization).guardrails)

    def test_an_env_reference_in_the_edit_is_refused_by_its_field(
        self, auth_client, organization, gateway
    ):
        vendor, _ = _synced(organization)
        entry = _entry(organization, "lakera-guard")
        before = (_active(organization).version, copy.deepcopy(vendor.checks))
        pushes = gateway.set_org_config.call_count

        response = auth_client.post(
            UPDATE,
            {
                "name": "lakera-guard",
                "config": _dialog_edit(
                    entry, config={**entry["config"], "api_key": ENV_REF}
                ),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_env_reference"
        assert response.json()["param"] == "config.config.api_key"
        vendor.refresh_from_db()
        assert (_active(organization).version, vendor.checks) == before
        assert gateway.set_org_config.call_count == pushes

    def test_a_check_config_that_is_not_an_object_is_refused(
        self, auth_client, organization, gateway
    ):
        vendor, _ = _synced(organization)
        entry = _entry(organization, "lakera-guard")
        before = copy.deepcopy(vendor.checks)
        response = auth_client.post(
            UPDATE,
            {"name": "lakera-guard", "config": _dialog_edit(entry, config="oops")},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == "guardrail_check_config_not_an_object"
        vendor.refresh_from_db()
        assert vendor.checks == before


@pytest.mark.integration
@pytest.mark.api
class TestAListNoPolicyWrote:
    """A checks list saved through update-config or POST org-configs (no
    `_policy`): the switch and the edit change it as a list."""

    def _api_list(self, org):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            guardrails={
                "checks": [
                    {
                        "name": "keyword-blocklist",
                        "enabled": True,
                        "action": "block",
                        "config": {"words": ["blocked-word"]},
                    }
                ]
            },
        )

    @pytest.mark.parametrize("path", ["toggle", "update"])
    def test_the_list_is_changed_and_pushed(
        self, auth_client, organization, gateway, path
    ):
        self._api_list(organization)
        if path == "toggle":
            response = auth_client.post(
                TOGGLE, {"name": "keyword-blocklist", "enabled": False}, format="json"
            )
        else:
            response = auth_client.post(
                UPDATE,
                {
                    "name": "keyword-blocklist",
                    "config": {"enabled": False, "action": "warn"},
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_check(gateway, organization, "keyword-blocklist")
        assert pushed["enabled"] is False
        assert pushed["config"]["words"] == ["blocked-word"]
        _assert_list_section(organization)
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).exists()

    def test_a_new_check_is_added_to_the_list(self, auth_client, organization, gateway):
        self._api_list(organization)
        response = auth_client.post(
            TOGGLE, {"name": "pii-detector", "enabled": True}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "pii-detection")["enabled"] is True
        assert _pushed_check(gateway, organization, "keyword-blocklist")["enabled"]
        _assert_list_section(organization)


SHAPES = {
    "rules-list": {
        "rules": [
            {
                "name": "keyword-blocklist",
                "enabled": True,
                "action": "block",
                "config": {"words": ["blocked-word"]},
            }
        ]
    },
    "checks-map": {
        "checks": {
            "keyword-blocklist": {
                "enabled": True,
                "action": "block",
                "config": {"words": ["blocked-word"]},
            }
        }
    },
}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("path", ["toggle", "update"])
@pytest.mark.parametrize("shape", [*sorted(SHAPES), "policy-list"])
def test_every_shape_is_switched_off_and_pushed(
    auth_client, organization, gateway, shape, path
):
    """The three stored shapes side by side: the switch and the edit
    answer 200 and the gateway gets the check switched off."""
    if shape == "policy-list":
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        _words(organization)
        assert sync_guardrail_policies(organization)
    else:
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=copy.deepcopy(SHAPES[shape]),
        )
    if path == "toggle":
        response = auth_client.post(
            TOGGLE, {"name": "keyword-blocklist", "enabled": False}, format="json"
        )
    else:
        response = auth_client.post(
            UPDATE,
            {
                "name": "keyword-blocklist",
                "config": {
                    "enabled": False,
                    "action": "block",
                    "config": {"words": ["blocked-word"]},
                },
            },
            format="json",
        )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["result"]["gateway_synced"] is True
    assert _pushed_check(gateway, organization, "keyword-blocklist")["enabled"] is False
    # A later save that does not touch guardrails keeps it off.
    response = auth_client.post(
        SET_BUDGET,
        {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert _pushed_check(gateway, organization, "keyword-blocklist")["enabled"] is False
