"""update-guardrail edits a guardrails section stored as a checks map.

The console's Save & Activate stores guardrails as a checks map, and config
push forwards such a map with every section key beside it, and the gateway
contract refuses `rules` beside a non-empty map (GuardrailConfig,
extra="forbid"). So update-guardrail writes the check's map entry: the
stored entry with every key
it is stored with kept (`_policy` included), and the contract's keys the edit
sent. A section stored as rules is edited in place and stays a rules list.

No value here is a real credential.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from integrations.services.credentials import CredentialManager

URL = "/agentcc/gateways/default/update-guardrail/"
FI_CONFIG = {
    "provider": "futureagi",
    "base_url": "https://evals.example.com",
    "api_key": "literal-key",
    "secret_key": "literal-secret",
    "eval_id": "toxicity",
}
OWN_SECRET = "t-map-guard-key-test-value"


def _make_config(organization, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **fields
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


@contextmanager
def _gateway():
    """The gateway client every push path uses, as a mock that records pushes;
    the payload goes through the real config push and its contract."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _pushed_guardrails(client):
    client.set_org_config.assert_called_once()
    _org_id, payload = client.set_org_config.call_args.args
    return payload["guardrails"]


def _map_org(organization, **extra_checks):
    checks = {
        "x": {"enabled": True, "action": "log", "config": dict(FI_CONFIG)},
        **extra_checks,
    }
    return _make_config(organization, guardrails={"checks": checks})


@pytest.mark.integration
@pytest.mark.api
class TestUpdateGuardrailOnAChecksMap:
    def test_a_new_check_is_added_to_the_map(self, auth_client, organization):
        # A checks-object org: the edit lands in the map.
        _map_org(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {
                    "name": "keyword-blocklist",
                    "config": {
                        "enabled": True,
                        "action": "block",
                        "config": {"words": ["${jndi:"]},
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, False), (2, True)]
        stored = _active(organization).guardrails
        assert "rules" not in stored
        assert stored["checks"]["keyword-blocklist"] == {
            "enabled": True,
            "action": "block",
            "config": {"words": ["${jndi:"]},
        }
        pushed = _pushed_guardrails(gateway)["checks"]
        assert pushed["keyword-blocklist"]["config"] == {"words": ["${jndi:"]}
        assert pushed["x"]["config"]["base_url"] == "https://evals.example.com"

    def test_a_rule_body_writes_only_the_contracts_keys(
        self, auth_client, organization
    ):
        # The Overview's edit dialog sends a rule (threshold, stage, mode).
        _map_org(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {
                    "name": "x",
                    "config": {
                        "enabled": True,
                        "action": "block",
                        "threshold": 0.4,
                        "stage": "pre",
                        "mode": "sync",
                        "config": dict(FI_CONFIG),
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["checks"]["x"] == {
            "enabled": True,
            "action": "block",
            "confidence_threshold": 0.4,
            "config": dict(FI_CONFIG),
        }
        assert _pushed_guardrails(gateway)["checks"]["x"]["action"] == "block"

    def test_a_check_found_by_its_rule_name(self, auth_client, organization):
        # A map keeps a console rule under its catalog name.
        _map_org(
            organization,
            **{"pii-detection": {"enabled": True, "action": "log", "config": {}}},
        )

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {"name": "pii-detector", "config": {"action": "mask"}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        checks = _active(organization).guardrails["checks"]
        assert "pii-detector" not in checks
        assert checks["pii-detection"]["action"] == "mask"
        assert _pushed_guardrails(gateway)["checks"]["pii-detection"]["action"] == (
            "mask"
        )

    def test_a_check_is_switched_off(self, auth_client, organization):
        _map_org(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                URL, {"name": "x", "config": {"enabled": False}}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails["checks"]["x"]
        assert stored == {"enabled": False, "action": "log", "config": dict(FI_CONFIG)}
        assert _pushed_guardrails(gateway)["checks"]["x"]["enabled"] is False

    def test_the_stored_policy_and_sentinel_are_kept_and_injected(
        self, auth_client, organization
    ):
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="__ui_guardrails__",
            checks=[],
            encrypted_check_configs=CredentialManager.encrypt(
                {"lakera-guard": {"api_key": OWN_SECRET}}
            ),
        )
        lakera = {
            "enabled": True,
            "action": "log",
            "config": {
                "provider": "lakera",
                "endpoint": "https://lakera.example",
                "api_key": "__encrypted__",
            },
            "_policy": str(policy.id),
        }
        _map_org(organization, **{"lakera-guard": lakera})

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {"name": "lakera-guard", "config": {"action": "block"}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails["checks"]["lakera-guard"]
        assert stored == {**lakera, "action": "block"}
        assert OWN_SECRET not in json.dumps(stored)
        pushed = _pushed_guardrails(gateway)["checks"]["lakera-guard"]
        assert pushed["config"]["api_key"] == OWN_SECRET
        assert pushed["action"] == "block"

    def test_a_sent_reference_is_refused_named_as_sent(self, auth_client, organization):
        _map_org(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {
                    "name": "y",
                    "config": {
                        "enabled": False,
                        "config": {**FI_CONFIG, "api_key": "${EXAMPLE_TOKEN}"},
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        body = json.dumps(response.json())
        assert "config.config.api_key" in body and "environment reference" in body
        assert "EXAMPLE_TOKEN" not in body
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_a_stored_reference_in_another_check_names_its_field(
        self, auth_client, organization
    ):
        # A switched-on check holding a "${" refuses a guardrail save, and the answer
        # names the field to fix.
        stored_check = {
            "enabled": True,
            "action": "log",
            "config": {**FI_CONFIG, "api_key": "${EXAMPLE_TOKEN}"},
        }
        _make_config(organization, guardrails={"checks": {"x": stored_check}})

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {
                    "name": "keyword-blocklist",
                    "config": {
                        "enabled": True,
                        "action": "block",
                        "config": {"words": ["bad"]},
                    },
                },
                format="json",
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        body = json.dumps(response.json())
        assert "guardrails.checks.x.config.api_key" in body, body
        assert "guardrails.rules" not in body
        assert "EXAMPLE_TOKEN" not in body
        gateway.set_org_config.assert_not_called()

    def test_control_a_rules_section_is_edited_in_place(
        self, auth_client, organization
    ):
        rule = {"name": "x", "enabled": True, "action": "log", "config": FI_CONFIG}
        _make_config(organization, guardrails={"rules": [rule]})

        with _gateway() as gateway:
            response = auth_client.post(
                URL,
                {"name": "x", "config": {**rule, "action": "warn"}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails == {
            "rules": [{**rule, "action": "warn"}]
        }
        assert _pushed_guardrails(gateway)["checks"]["x"]["action"] == "warn"
