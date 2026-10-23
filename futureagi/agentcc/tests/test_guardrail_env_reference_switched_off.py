"""A "${" stored in a switched-off guardrail check refuses neither the
activation of its version nor a save that leaves the check out or sends it
back masked.

The gateway never builds a check that is switched off, so a "${" stored in one
reaches nothing. Activating a version, and a save that changes
guardrails, count only the checks that are switched on for that stored
value, read per shape the way config push sends them: a check in a rules list
or a checks list without "enabled" is on, a check in the checks object without
it is off. Switching such a check on
is refused, naming the field.

What a save sends is still refused whether the check is on or off: a check the
save adds or changes (anything but its "enabled") is checked as it will be
stored. So switching such a check off works, and changing anything else in
it while keeping the "${" does not.

No variable here is resolved and none names a real secret.
"""

import copy
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from integrations.services.credentials import CredentialManager

FI_CONFIG = {
    "provider": "futureagi",
    "base_url": "https://evals.example.com",
    "api_key": "literal-key",
    "secret_key": "literal-secret",
    "eval_id": "toxicity",
}
REFERENCE_CONFIG = {**FI_CONFIG, "api_key": "${EXAMPLE_TOKEN}"}
BLOCKLIST = {"enabled": True, "action": "block", "config": {"words": ["bad"]}}


def _reference_object(enabled):
    """A checks-object section whose check x holds a "${" in api_key; enabled
    None leaves the key out (off in this shape)."""
    check = {"action": "log", "config": dict(REFERENCE_CONFIG)}
    if enabled is not None:
        check["enabled"] = enabled
    return {"checks": {"x": check}}


def _reference_list(key, enabled):
    """The same check in a rules list or a checks list; enabled None leaves
    the key out (on in these shapes)."""
    check = {"name": "x", "action": "log", "config": dict(REFERENCE_CONFIG)}
    if enabled is not None:
        check["enabled"] = enabled
    return {key: [check]}


SWITCHED_OFF = {
    "checks_object_false": lambda: _reference_object(False),
    "checks_object_missing": lambda: _reference_object(None),
    "checks_object_string_false": lambda: _reference_object("false"),
    "rules_false": lambda: _reference_list("rules", False),
    "checks_list_false": lambda: _reference_list("checks", False),
}
SWITCHED_ON = {
    "checks_object_true": (lambda: _reference_object(True), "guardrails.checks.x"),
    "rules_missing": (lambda: _reference_list("rules", None), "guardrails.rules[0]"),
    "checks_list_missing": (
        lambda: _reference_list("checks", None),
        "guardrails.checks[0]",
    ),
}


def _make_config(organization, version=1, is_active=True, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=version, is_active=is_active, **fields
    )


def _versions(organization):
    return list(
        AgentccOrgConfig.no_workspace_objects.filter(organization=organization)
        .order_by("version")
        .values_list("version", "is_active")
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


@contextmanager
def _gateway():
    """The gateway client every push path uses, as a mock that records pushes."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _pushed_checks(client):
    client.set_org_config.assert_called_once()
    _org_id, payload = client.set_org_config.call_args.args
    return payload["guardrails"]["checks"]


def _assert_refused(response, field):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = json.dumps(response.json())
    assert field in body, body
    assert "environment reference" in body, body
    assert "EXAMPLE_TOKEN" not in body, body


@pytest.mark.integration
@pytest.mark.api
class TestActivation:
    @pytest.mark.parametrize("shape", sorted(SWITCHED_OFF))
    def test_a_version_whose_reference_is_in_a_switched_off_check_activates(
        self, auth_client, organization, shape
    ):
        # A stored version whose only reference is in a switched-off check.
        older = _make_config(
            organization, version=1, is_active=False, guardrails=SWITCHED_OFF[shape]()
        )
        _make_config(organization, version=2, is_active=True)

        with _gateway() as gateway:
            response = auth_client.post(f"/agentcc/org-configs/{older.id}/activate/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, True), (2, False)]
        # The check reaches the gateway switched off, which never builds it.
        pushed = _pushed_checks(gateway)["x"]
        assert pushed.get("enabled") is not True

    @pytest.mark.parametrize("shape", sorted(SWITCHED_ON))
    def test_a_version_with_the_check_switched_on_is_still_refused(
        self, auth_client, organization, shape
    ):
        make, path = SWITCHED_ON[shape]
        older = _make_config(
            organization, version=1, is_active=False, guardrails=make()
        )
        _make_config(organization, version=2, is_active=True)

        with _gateway() as gateway:
            response = auth_client.post(f"/agentcc/org-configs/{older.id}/activate/")

        _assert_refused(response, f"{path}.config.api_key")
        assert _versions(organization) == [(1, False), (2, True)]
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailSaveBesideASwitchedOffReferenceCheck:
    def test_update_config_adds_another_check(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_object(False))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": {"checks": {"keyword-blocklist": BLOCKLIST}}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert set(_pushed_checks(gateway)) == {"x", "keyword-blocklist"}

    def test_update_guardrail_of_another_check(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_list("rules", False))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-guardrail/",
                {"name": "keyword-blocklist", "config": BLOCKLIST},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_checks(gateway)["keyword-blocklist"]["enabled"] is True

    def test_toggle_of_another_check(self, auth_client, organization):
        guardrails = _reference_list("rules", False)
        guardrails["rules"].append({"name": "keyword-blocklist", **BLOCKLIST})
        _make_config(organization, guardrails=guardrails)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/toggle-guardrail/",
                {"name": "keyword-blocklist", "enabled": False},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_checks(gateway)["keyword-blocklist"]["enabled"] is False

    def test_org_config_save_with_the_reference_held_encrypted(
        self, auth_client, organization
    ):
        # The console shows a stored credential as "__encrypted__" and sends it
        # back so; the "${" lives in the console policy's blob.
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="__ui_guardrails__",
            checks=[],
            encrypted_check_configs=CredentialManager.encrypt(
                {"x": {"api_key": "${EXAMPLE_TOKEN}"}}
            ),
        )
        held = {
            "enabled": False,
            "action": "log",
            "config": {**FI_CONFIG, "api_key": "__encrypted__"},
            "_policy": str(policy.id),
        }
        _make_config(organization, guardrails={"checks": {"x": held}})

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/",
                {
                    "guardrails": {
                        "checks": {
                            "x": copy.deepcopy(held),
                            "keyword-blocklist": BLOCKLIST,
                        }
                    }
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_checks(gateway)
        assert pushed["x"]["enabled"] is False
        assert pushed["keyword-blocklist"]["enabled"] is True

    def test_a_switched_on_reference_check_refuses_the_save(
        self, auth_client, organization
    ):
        # An org whose check holding a "${" is switched on gets the 400 on a
        # guardrail save until the field is fixed or switched off.
        _make_config(organization, guardrails=_reference_object(True))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": {"checks": {"keyword-blocklist": BLOCKLIST}}},
                format="json",
            )

        _assert_refused(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestSwitchingAReferenceCheck:
    def test_update_config_switches_it_off(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_object(True))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": {"checks": {"x": {"enabled": False}}}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["checks"]["x"]["enabled"] is False
        assert _pushed_checks(gateway)["x"]["enabled"] is False

    def test_toggle_switches_it_off(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_list("rules", True))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/toggle-guardrail/",
                {"name": "x", "enabled": False},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_checks(gateway)["x"]["enabled"] is False

    def test_update_config_switching_it_on_is_refused(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_object(False))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": {"checks": {"x": {"enabled": True}}}},
                format="json",
            )

        _assert_refused(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_update_guardrail_switches_a_map_check_off(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_object(True))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-guardrail/",
                {"name": "x", "config": {"enabled": False}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).guardrails["checks"]["x"]["enabled"] is False
        assert _pushed_checks(gateway)["x"]["enabled"] is False


@pytest.mark.integration
@pytest.mark.api
class TestFixingAReferenceCheck:
    """The other ways to fix such a check, and what Save & Activate still
    refuses (controls)."""

    def test_save_and_activate_with_the_value_itself(self, auth_client, organization):
        _make_config(organization, guardrails=_reference_object(True))
        fixed = _reference_object(True)
        fixed["checks"]["x"]["config"]["api_key"] = "t-real-key-test-value"

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/", {"guardrails": fixed}, format="json"
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails["checks"]["x"]
        assert stored["config"]["api_key"] == "__encrypted__"
        assert _pushed_checks(gateway)["x"]["config"]["api_key"] == (
            "t-real-key-test-value"
        )

    def test_a_policy_patch_with_the_value_itself(self, auth_client, organization):
        _make_config(organization)
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="reference-policy",
            checks=[
                {
                    "name": "x",
                    "enabled": True,
                    "action": "log",
                    "config": {
                        **FI_CONFIG,
                        "base_url": "${EXAMPLE_TOKEN}",
                        "api_key": "__encrypted__",
                    },
                }
            ],
            encrypted_check_configs=CredentialManager.encrypt(
                {"x": {"api_key": "literal-key"}}
            ),
        )

        with _gateway() as gateway:
            response = auth_client.patch(
                f"/agentcc/guardrail-policies/{policy.id}/",
                {
                    "checks": [
                        {
                            "name": "x",
                            "enabled": True,
                            "action": "log",
                            "config": {**FI_CONFIG, "api_key": "__encrypted__"},
                        }
                    ]
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_checks(gateway)["x"]["config"]
        assert pushed["base_url"] == FI_CONFIG["base_url"]
        assert pushed["api_key"] == "literal-key"

    def test_save_and_activate_re_sending_a_clear_text_reference_switched_off(
        self, auth_client, organization
    ):
        # Save & Activate checks what it sends, on or off.
        _make_config(organization, guardrails=_reference_object(False))
        sent = _reference_object(False)
        sent["checks"]["keyword-blocklist"] = BLOCKLIST

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/", {"guardrails": sent}, format="json"
            )

        _assert_refused(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestWhatASaveSendsIsCheckedOnOrOff:
    """What a save sends is refused whether its check is on or off."""

    @pytest.mark.parametrize(
        "guardrails, field",
        [
            (_reference_object(False), "guardrails.checks.x.config.api_key"),
            (_reference_object(None), "guardrails.checks.x.config.api_key"),
            (_reference_list("rules", False), "guardrails.rules[0].config.api_key"),
        ],
        ids=["checks_object_off", "checks_object_missing", "rules_off"],
    )
    def test_update_config_sending_a_new_switched_off_check(
        self, auth_client, organization, guardrails, field
    ):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": guardrails},
                format="json",
            )

        _assert_refused(response, field)
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    @pytest.mark.parametrize("enabled", [False, True])
    def test_update_config_changing_a_check_that_keeps_its_reference(
        self, auth_client, organization, enabled
    ):
        # A save that switches the check off and changes its endpoint is
        # refused for the stored "${".
        _make_config(organization, guardrails=_reference_object(enabled))

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {
                    "guardrails": {
                        "checks": {
                            "x": {
                                "enabled": False,
                                "config": {
                                    "base_url": "https://collector.attacker.example"
                                },
                            }
                        }
                    }
                },
                format="json",
            )

        _assert_refused(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    def test_update_guardrail_sending_a_switched_off_check(
        self, auth_client, organization
    ):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-guardrail/",
                {
                    "name": "x",
                    "config": {
                        "enabled": False,
                        "action": "log",
                        "config": dict(REFERENCE_CONFIG),
                    },
                },
                format="json",
            )

        _assert_refused(response, "config.config.api_key")
        gateway.set_org_config.assert_not_called()

    def test_org_config_create_sending_a_switched_off_check(
        self, auth_client, organization
    ):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"guardrails": _reference_object(False)},
                format="json",
            )

        _assert_refused(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()
