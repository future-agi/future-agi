"""The Guardrails Overview toggles and edits a check of a section stored as a
checks map.

An org's guardrails section may be stored as a checks map
({"checks": {name: check}}), and the console keeps it a map (useConfigPatch.js
checksMapForSave). The config push forwards such a map to the gateway as
stored, so its gateway contract (GuardrailConfig, extra="forbid") refuses any
`rules` beside it. On such a map toggle-guardrail and update-guardrail write
the map: the check's own entry, with only the contract's keys (enabled,
action, confidence_threshold, config), keeping every other key the check is
stored with.
"""

from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import guardrail_credentials
from agentcc.services.config_push import _build_payload
from integrations.services.credentials import CredentialManager

GATEWAY = "/agentcc/gateways/default"
TOGGLE = f"{GATEWAY}/toggle-guardrail/"
UPDATE = f"{GATEWAY}/update-guardrail/"
SENTINEL = "__encrypted__"
NEW_SECRET = "overview-map-clear-text-guard-key-test-value"  # not a real credential

# A data leakage check stored with an action and no threshold, and a DynamoAI
# check stored with neither: the gateway runs it as log, at threshold 0.
LEAKAGE = {"enabled": True, "action": "block"}
DYNAMO = {"enabled": True, "config": {"provider": "dynamoai", "policy_ids": "p1"}}
MAP = {"checks": {"data-leakage-prevention": LEAKAGE, "dynamoai-guard": DYNAMO}}


def _make_config(organization, guardrails):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, guardrails=guardrails
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _pushed_checks(organization):
    """What the push sends the gateway for the active version, through the
    gateway contract (which refuses `rules` beside a checks map)."""
    payload = _build_payload(
        str(organization.id), _active(organization), with_upstream_keys=False
    )
    return payload["guardrails"]["checks"]


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestOverviewOnAChecksMap:
    def test_toggle_switches_the_maps_own_entry(self, _push, auth_client, organization):
        _make_config(organization, MAP)

        response = auth_client.post(
            TOGGLE, {"name": "data-leakage-prevention", "enabled": False}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["version"] == 2
        stored = _active(organization).guardrails
        assert stored == {
            "checks": {
                "data-leakage-prevention": {**LEAKAGE, "enabled": False},
                "dynamoai-guard": DYNAMO,
            }
        }
        assert _pushed_checks(organization)["data-leakage-prevention"] == {
            "enabled": False,
            "action": "block",
        }

    def test_toggle_finds_a_check_stored_under_its_catalog_name(
        self, _push, auth_client, organization
    ):
        # A check may be keyed by its catalog name, with the rule name it was
        # created under in `_originalName`.
        pii = {"enabled": True, "action": "mask", "_originalName": "pii-detector"}
        _make_config(organization, {"checks": {"pii-detection": pii}})

        response = auth_client.post(
            TOGGLE, {"name": "pii-detector", "enabled": False}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {"pii-detection": {**pii, "enabled": False}}
        }

    def test_toggle_adds_a_check_the_map_lacks_with_the_rules_defaults(
        self, _push, auth_client, organization
    ):
        # A rule the Overview switches on for the first time is made with
        # block and 0.8; in a map the check gets the same, written out.
        _make_config(organization, MAP)

        response = auth_client.post(
            TOGGLE, {"name": "secret-detection", "enabled": True}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        assert "rules" not in stored
        assert stored["checks"]["secret-detection"] == {
            "enabled": True,
            "action": "block",
            "confidence_threshold": 0.8,
            "config": {},
        }
        assert stored["checks"]["data-leakage-prevention"] == LEAKAGE
        assert stored["checks"]["dynamoai-guard"] == DYNAMO
        assert _pushed_checks(organization)["secret-detection"]["action"] == "block"

    def test_edit_writes_the_maps_entry_with_the_contracts_keys(
        self, _push, auth_client, organization
    ):
        _make_config(organization, MAP)

        # The Overview's edit dialog, as it sends a rule: the check as listed,
        # its action and threshold, and an execution mode.
        response = auth_client.post(
            UPDATE,
            {
                "name": "dynamoai-guard",
                "config": {
                    **DYNAMO,
                    "name": "dynamoai-guard",
                    "action": "block",
                    "threshold": 0.6,
                    "mode": "sync",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["version"] == 2
        stored = _active(organization).guardrails
        assert stored == {
            "checks": {
                "data-leakage-prevention": LEAKAGE,
                "dynamoai-guard": {
                    **DYNAMO,
                    "action": "block",
                    "confidence_threshold": 0.6,
                },
            }
        }
        assert _pushed_checks(organization)["dynamoai-guard"] == {
            **DYNAMO,
            "action": "block",
            "confidence_threshold": 0.6,
        }

    def test_edit_in_the_maps_shape_changes_only_what_it_sends(
        self, _push, auth_client, organization
    ):
        _make_config(organization, MAP)

        response = auth_client.post(
            UPDATE,
            {
                "name": "data-leakage-prevention",
                "config": {"confidence_threshold": 0.5},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["checks"] == {
            "data-leakage-prevention": {**LEAKAGE, "confidence_threshold": 0.5},
            "dynamoai-guard": DYNAMO,
        }

    def test_edit_keeps_a_stored_credential_and_its_policy(
        self, _push, auth_client, organization
    ):
        console = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name=guardrail_credentials.CONSOLE_GUARDRAIL_POLICY,
            checks=[],
            encrypted_check_configs=CredentialManager.encrypt(
                {"lakera-guard": {"api_key": NEW_SECRET}}
            ),
        )
        lakera = {
            "enabled": True,
            "action": "warn",
            "config": {"provider": "lakera", "api_key": SENTINEL},
            "_policy": str(console.id),
        }
        _make_config(organization, {"checks": {"lakera-guard": lakera}})

        response = auth_client.post(
            UPDATE,
            {"name": "lakera-guard", "config": {**lakera, "action": "block"}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "checks": {"lakera-guard": {**lakera, "action": "block"}}
        }
        pushed = _pushed_checks(organization)["lakera-guard"]
        assert pushed["action"] == "block"
        assert pushed["config"]["api_key"] == NEW_SECRET

    def test_edit_encrypts_a_new_credential(self, _push, auth_client, organization):
        _make_config(organization, MAP)

        response = auth_client.post(
            UPDATE,
            {
                "name": "dynamoai-guard",
                "config": {
                    "config": {**DYNAMO["config"], "api_key": NEW_SECRET},
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails["checks"]["dynamoai-guard"]
        assert stored["config"]["api_key"] == SENTINEL
        console = AgentccGuardrailPolicy.no_workspace_objects.get(
            organization=organization,
            name=guardrail_credentials.CONSOLE_GUARDRAIL_POLICY,
            deleted=False,
        )
        assert stored["_policy"] == str(console.id)
        assert CredentialManager.decrypt(bytes(console.encrypted_check_configs)) == {
            "dynamoai-guard": {"api_key": NEW_SECRET}
        }

    def test_a_section_stored_as_rules_edits_its_rules(
        self, _push, auth_client, organization
    ):
        # Control: the rules shape is unchanged.
        rule = {"name": "pii-detector", "enabled": True, "action": "mask"}
        _make_config(organization, {"rules": [rule]})

        toggled = auth_client.post(
            TOGGLE, {"name": "pii-detector", "enabled": False}, format="json"
        )
        assert toggled.status_code == status.HTTP_200_OK, toggled.json()
        edited = auth_client.post(
            UPDATE,
            {"name": "pii-detector", "config": {**rule, "action": "warn"}},
            format="json",
        )

        assert edited.status_code == status.HTTP_200_OK, edited.json()
        stored = _active(organization).guardrails
        assert stored["rules"] == [{**rule, "action": "warn", "name": "pii-detector"}]
