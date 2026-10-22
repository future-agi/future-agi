"""Django refuses "${" in the org guardrail fields the gateway checks for an
environment reference, and nowhere else.

The gateway refuses an org check whose api_key, secret_key or base_url (a
futureagi check) or any top-level config string (an external-provider check)
holds "${": it fails closed and makes no call.

Django's gate covers exactly those strings:

- Every path that saves a guardrail check's config refuses "${" in them with
  a 400 that names the field, never the value: POST org-configs in each shape
  (checks map, rules list, checks list), update-config, update-guardrail, and
  a guardrail policy's create and update.
- A save that changes the guardrails section, and an activation, also check
  what they would push (credentials decrypted, enabled checks), so a stored
  value answers 400 there instead of being pushed to a check that refuses
  every request.
- Nothing else is refused: a blocklist word, an expression, a webhook header,
  a nested adapter list, a check name or map key, and tool_calls all keep
  "${" (no gateway ever expands them), and a save that does not change the
  guardrails section (a budget, an MCP server) is never refused, even when
  the stored config holds such a value.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models import Organization  # noqa: F401 (via fixtures)
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.guardrail_credentials import CONSOLE_GUARDRAIL_POLICY
from agentcc.views import gateway as gateway_views
from integrations.services.credentials import CredentialManager

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
UPDATE_GUARDRAIL = f"{GATEWAY}/update-guardrail/"
TOGGLE_GUARDRAIL = f"{GATEWAY}/toggle-guardrail/"
ORG_CONFIGS = "/agentcc/org-configs/"
POLICIES = "/agentcc/guardrail-policies/"
SENTINEL = "__encrypted__"

# An environment reference to a test variable name; not a secret.
ENV_REF = "${GATEWAY_TEST_VAR}"
LITERAL_KEY = "fi-literal-org-key-test-value"
LITERAL_SECRET = "fi-literal-org-secret-test-value"
# A blocklist word that contains "${".
JNDI_WORD = "${jndi:"

# The check the gateway gets for a blocklist holding JNDI_WORD: what
# orgfactory_literal_values_test.go (djangoPushedBlocklist) decodes and runs.
PUSHED_BLOCKLIST = {
    "enabled": True,
    "action": "block",
    "confidence_threshold": 0.8,
    "config": {"words": [JNDI_WORD]},
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


def _pushed_guardrails(gateway, org):
    sent = [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]
    assert sent, "nothing was pushed"
    return sent[-1]["guardrails"]


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _fi_config(**overrides):
    """The inner config of a futureagi check (what the gateway's guardrail
    reads). GuardrailCheck.config is a free dict[str, Any], so these keys are
    accepted by the gateway contract."""
    cfg = {
        "provider": "futureagi",
        "base_url": "https://api.futureagi.com",
        "api_key": LITERAL_KEY,
        "secret_key": LITERAL_SECRET,
        "eval_id": "toxicity",
    }
    cfg.update(overrides)
    return cfg


def _fi_check(**overrides):
    """A futureagi check in the checks-map shape: a GuardrailCheck whose
    nested config carries the credentials."""
    return {"enabled": True, "action": "log", "config": _fi_config(**overrides)}


def _blocklist_rule():
    return {
        "name": "keyword-blocklist",
        "enabled": True,
        "action": "block",
        "config": {"words": [JNDI_WORD]},
    }


def _no_env_ref_stored(org):
    for guardrails in AgentccOrgConfig.all_objects.filter(organization=org).values_list(
        "guardrails", flat=True
    ):
        assert ENV_REF not in json.dumps(guardrails), "an env ref was stored"


def _console_blobs_clean(org):
    for blob in AgentccGuardrailPolicy.no_workspace_objects.filter(
        organization=org
    ).values_list("encrypted_check_configs", flat=True):
        if not blob:
            continue
        try:
            decrypted = CredentialManager.decrypt(bytes(blob))
        except Exception:
            continue
        assert ENV_REF not in json.dumps(decrypted), "an env ref was encrypted"


def _assert_refused(response, path):
    """A 400 that names the field path and never the variable or its value."""
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = json.dumps(response.json())
    assert path in body, body
    assert "GATEWAY_TEST_VAR" not in body


# ---------------------------------------------------------------------------
# Strings the gateway never expands stay free: they save and are pushed as
# written.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestWordsPatternsAndNamesStayFree:
    def test_update_config_blocklist_word_saves_and_is_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [_blocklist_rule()]}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"] == PUSHED_BLOCKLIST

    @pytest.mark.parametrize("shape", ["checks_map", "rules_list"])
    def test_org_configs_blocklist_word_saves_and_is_pushed(
        self, auth_client, organization, gateway, shape
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = _blocklist_rule()
        if shape == "checks_map":
            name = rule.pop("name")
            section = {
                "checks": {name: {**rule, "confidence_threshold": 0.8}},
            }
        else:
            section = {"rules": [rule]}
        response = auth_client.post(ORG_CONFIGS, {"guardrails": section}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == [JNDI_WORD]

    def test_update_guardrail_blocklist_word_saves_and_is_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = _blocklist_rule()
        name = rule.pop("name")
        response = auth_client.post(
            UPDATE_GUARDRAIL, {"name": name, "config": rule}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"] == PUSHED_BLOCKLIST

    def test_policy_blocklist_word_saves_and_is_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(
            POLICIES,
            {"name": "dollar-brace-words", "checks": [_blocklist_rule()]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"] == PUSHED_BLOCKLIST

    def test_policy_patch_blocklist_word_saves_and_is_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="words",
            checks=[{"name": "keyword-blocklist", "config": {"words": ["x"]}}],
        )
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/",
            {"checks": [_blocklist_rule()]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        policy.refresh_from_db()
        assert policy.checks[0]["config"]["words"] == [JNDI_WORD]
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == [JNDI_WORD]

    def test_every_string_the_gateway_never_expands_saves_as_written(
        self, auth_client, organization, gateway
    ):
        """An expression, a webhook header, a check name (the pushed map key),
        tool_calls and an external check's nested list hold "${" and are saved
        and pushed as written."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        expression = 'request.messages.0.content contains "${"'
        rules = [
            _blocklist_rule(),
            {
                "name": "audit-${team}",
                "enabled": True,
                "action": "block",
                "config": {"expression": expression},
            },
            {
                "name": "hook-${env}",
                "enabled": True,
                "action": "log",
                "config": {
                    "url": "https://hooks.example.com/check",
                    "headers": {"X-Template": "${not-expanded}"},
                },
            },
            {
                "name": "lakera-guard",
                "enabled": True,
                "action": "log",
                "config": {
                    "provider": "lakera",
                    "api_key": LITERAL_KEY,
                    "categories": ["${nested-list-value}"],
                },
            },
        ]
        response = auth_client.post(
            UPDATE_CONFIG,
            {
                "guardrails": {
                    "rules": rules,
                    "tool_calls": {"enabled": True, "checks": ["audit-${team}"]},
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["audit-${team}"]["config"]["expression"] == expression
        assert pushed["checks"]["hook-${env}"]["config"]["headers"] == {
            "X-Template": "${not-expanded}"
        }
        assert pushed["checks"]["lakera-guard"]["config"]["categories"] == [
            "${nested-list-value}"
        ]
        assert pushed["tool_calls"]["checks"] == ["audit-${team}"]

    def test_webhook_header_holding_dollar_brace_is_saved_as_written(
        self, auth_client, organization, gateway
    ):
        """No gateway expands a webhook check's url or headers, so the header is
        a literal the webhook receives."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "guardrails": {
                "checks": {
                    "webhook-x": {
                        "enabled": True,
                        "action": "log",
                        "config": {"url": "https://x", "headers": {"X": ENV_REF}},
                    }
                },
            }
        }
        response = auth_client.post(ORG_CONFIGS, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["webhook-x"]["config"]["headers"] == {"X": ENV_REF}


# ---------------------------------------------------------------------------
# A "${" in a field the gateway checks is refused on every save path.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestOrgConfigsRejectsEnvRef:
    @pytest.mark.parametrize("field", ["api_key", "secret_key", "base_url"])
    def test_checks_dict_env_ref_is_rejected(
        self, auth_client, organization, gateway, field
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "guardrails": {
                "checks": {"futureagi-eval": _fi_check(**{field: ENV_REF})},
            }
        }
        response = auth_client.post(ORG_CONFIGS, body, format="json")

        _assert_refused(response, f"config.{field}")
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)
        gateway.set_org_config.assert_not_called()

    def test_rules_list_env_ref_is_rejected(self, auth_client, organization, gateway):
        """The rules-list shape, on a futureagi-eval rule that names no
        provider: the push gives it provider futureagi, so its base_url is a
        field the gateway checks for "${"."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        config = _fi_config(base_url=ENV_REF)
        del config["provider"]
        body = {
            "guardrails": {
                "rules": [
                    {
                        "name": "futureagi-eval",
                        "enabled": True,
                        "action": "block",
                        "config": config,
                    }
                ]
            }
        }
        response = auth_client.post(ORG_CONFIGS, body, format="json")

        _assert_refused(response, "guardrails.rules[0].config.base_url")
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)
        gateway.set_org_config.assert_not_called()

    def test_checks_list_external_top_level_string_is_rejected(
        self, auth_client, organization, gateway
    ):
        """An external-provider check: the gateway refuses any "${" in a
        top-level string, a part-string "${" included."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "guardrails": {
                "checks": [
                    {
                        "name": "lakera-guard",
                        "enabled": True,
                        "config": {
                            "api_key": LITERAL_KEY,
                            "endpoint": "https://${GATEWAY_TEST_VAR}.example.com",
                        },
                    }
                ]
            }
        }
        response = auth_client.post(ORG_CONFIGS, body, format="json")

        _assert_refused(response, "guardrails.checks[0].config.endpoint")
        _no_env_ref_stored(organization)
        gateway.set_org_config.assert_not_called()

    def test_clean_save_pushes(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "guardrails": {
                "checks": {"futureagi-eval": _fi_check()},
            }
        }
        response = auth_client.post(ORG_CONFIGS, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestUpdateConfigRejectsEnvRef:
    def test_env_ref_is_rejected(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "guardrails": {
                "checks": {"futureagi-eval": _fi_check(api_key=ENV_REF)},
            }
        }
        response = auth_client.post(UPDATE_CONFIG, body, format="json")
        _assert_refused(response, "guardrails.checks.futureagi-eval.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert (
            response.json().get("param")
            == "guardrails.checks.futureagi-eval.config.api_key"
        )
        gateway.set_org_config.assert_not_called()
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)

    def test_external_top_level_string_is_rejected(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = {
            "name": "lakera-guard",
            "enabled": True,
            "action": "block",
            "config": {"provider": "lakera", "api_key": ENV_REF},
        }
        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [rule]}}, format="json"
        )
        _assert_refused(response, "guardrails.rules[0].config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        gateway.set_org_config.assert_not_called()
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)

    def test_clean_save_pushes(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {"guardrails": {"checks": {"futureagi-eval": _fi_check()}}}
        response = auth_client.post(UPDATE_CONFIG, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestUpdateGuardrailRejectsEnvRef:
    def test_env_ref_is_rejected(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = {
            "enabled": True,
            "action": "block",
            "config": _fi_config(secret_key=ENV_REF),
        }
        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "futureagi-eval", "config": rule},
            format="json",
        )
        _assert_refused(response, "config.config.secret_key")
        assert response.json().get("code") == "guardrail_env_reference"
        gateway.set_org_config.assert_not_called()
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)

    def test_clean_save_pushes(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = {"enabled": True, "action": "block", "config": _fi_config()}
        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {"name": "futureagi-eval", "config": rule},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailPolicyRejectsEnvRef:
    def test_env_ref_is_rejected(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "name": "p1",
            "checks": [
                {"name": "futureagi-eval", "config": _fi_config(api_key=ENV_REF)}
            ],
        }
        response = auth_client.post(POLICIES, body, format="json")
        _assert_refused(response, "checks[0].config.api_key")
        # No policy row carries the env reference.
        _console_blobs_clean(organization)
        for checks in AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization
        ).values_list("checks", flat=True):
            assert ENV_REF not in json.dumps(checks)
        gateway.set_org_config.assert_not_called()

    def test_patch_env_ref_is_rejected(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="p-patch",
            checks=[{"name": "futureagi-eval", "config": _fi_config()}],
        )
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/",
            {
                "checks": [
                    {
                        "name": "futureagi-eval",
                        "config": _fi_config(base_url=ENV_REF),
                    }
                ]
            },
            format="json",
        )
        _assert_refused(response, "checks[0].config.base_url")
        policy.refresh_from_db()
        assert ENV_REF not in json.dumps(policy.checks)
        _console_blobs_clean(organization)
        gateway.set_org_config.assert_not_called()

    def test_clean_save_creates_and_pushes(self, auth_client, organization, gateway):
        # An active org config, so the policy sync has a version to push.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        body = {
            "name": "p-clean",
            "checks": [{"name": "futureagi-eval", "config": _fi_config()}],
        }
        response = auth_client.post(POLICIES, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="p-clean", deleted=False
        ).exists()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["futureagi-eval"]["config"]["eval_id"] == "toxicity"


# ---------------------------------------------------------------------------
# A stored "${": refused where a save that changes guardrails or an
# activation would push it, never on an unrelated save, and never in a check
# that is switched off.
# ---------------------------------------------------------------------------


def _stored_env_ref_guardrails(org, *, lakera_enabled=True, fi_enabled=True):
    """A guardrails section holding a stored "${": the lakera check's api_key
    is a ${...} held in the console policy's encrypted blob (the row keeps the
    sentinel), and the futureagi-eval rule's base_url is a ${...} kept in the
    row (base_url is not a credential). The gateway refuses both checks."""
    console, _ = AgentccGuardrailPolicy.no_workspace_objects.get_or_create(
        organization=org,
        name=CONSOLE_GUARDRAIL_POLICY,
        deleted=False,
        defaults={
            "checks": [],
            "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
            "is_active": True,
        },
    )
    console.encrypted_check_configs = CredentialManager.encrypt(
        {"lakera-guard": {"api_key": ENV_REF}}
    )
    console.save(update_fields=["encrypted_check_configs"])
    return {
        "rules": [
            {
                "name": "lakera-guard",
                "enabled": lakera_enabled,
                "action": "block",
                "config": {"provider": "lakera", "api_key": SENTINEL},
                "_policy": str(console.id),
            },
            {
                "name": "futureagi-eval",
                "enabled": fi_enabled,
                "action": "block",
                "config": {"base_url": ENV_REF, "eval_id": "toxicity"},
            },
        ]
    }


def _stored_env_ref_active(org, **kwargs):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        guardrails=_stored_env_ref_guardrails(org, **kwargs),
    )


@pytest.mark.integration
@pytest.mark.api
class TestActivationChecksWhatItWouldPush:
    def test_activating_a_version_with_a_stored_env_ref_is_refused(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        stored = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=2,
            is_active=False,
            guardrails=_stored_env_ref_guardrails(organization, fi_enabled=False),
        )
        response = auth_client.post(f"{ORG_CONFIGS}{stored.id}/activate/")

        _assert_refused(response, "guardrails.checks.lakera-guard.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert (
            response.json().get("param")
            == "guardrails.checks.lakera-guard.config.api_key"
        )
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_activating_a_stored_env_ref_base_url_is_refused(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        stored = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=2,
            is_active=False,
            guardrails=_stored_env_ref_guardrails(organization, lakera_enabled=False),
        )
        response = auth_client.post(f"{ORG_CONFIGS}{stored.id}/activate/")

        _assert_refused(response, "guardrails.checks.futureagi-eval.config.base_url")
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_a_stored_env_ref_in_a_switched_off_check_activates(
        self, auth_client, organization, gateway
    ):
        """The gateway never builds a check that is switched off, so it pushes
        nothing that refuses: the activation goes ahead."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        stored = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=2,
            is_active=False,
            guardrails=_stored_env_ref_guardrails(
                organization, lakera_enabled=False, fi_enabled=False
            ),
        )
        response = auth_client.post(f"{ORG_CONFIGS}{stored.id}/activate/")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2
        gateway.set_org_config.assert_called()


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailsChangingSaveChecksWhatItWouldPush:
    """A save that changes guardrails refuses a check it would switch on (or
    newly push) holding a stored "${" in a field the gateway checks. A
    check the save leaves exactly as the active version pushes it is
    not the save's doing, so the save goes ahead: otherwise an org with two
    such checks could never switch either off."""

    def test_update_config_that_switches_an_env_ref_check_on_is_refused(
        self, auth_client, organization, gateway
    ):
        active = _stored_env_ref_active(
            organization, lakera_enabled=False, fi_enabled=False
        )
        lakera = {**active.guardrails["rules"][0], "enabled": True}
        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [lakera]}}, format="json"
        )
        _assert_refused(response, "guardrails.checks.lakera-guard.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_switching_an_env_ref_check_on_is_refused(
        self, auth_client, organization, gateway
    ):
        _stored_env_ref_active(organization, lakera_enabled=False, fi_enabled=False)
        response = auth_client.post(
            TOGGLE_GUARDRAIL, {"name": "lakera-guard", "enabled": True}, format="json"
        )
        _assert_refused(response, "guardrails.checks.lakera-guard.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert (
            response.json().get("param")
            == "guardrails.checks.lakera-guard.config.api_key"
        )
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_switching_env_ref_checks_off_one_by_one_is_never_refused(
        self, auth_client, organization, gateway
    ):
        """Switching a broken check off is how an org stops it refusing
        traffic, so it always saves, even while another one is still on."""
        _stored_env_ref_active(organization)
        for name, version in (("lakera-guard", 2), ("futureagi-eval", 3)):
            response = auth_client.post(
                TOGGLE_GUARDRAIL, {"name": name, "enabled": False}, format="json"
            )
            assert response.status_code == status.HTTP_200_OK, response.json()
            assert _active(organization).version == version
        assert gateway.set_org_config.call_count == 2

    def test_a_guardrails_save_that_leaves_an_env_ref_check_as_it_is_saves(
        self, auth_client, organization, gateway
    ):
        active = _stored_env_ref_active(organization, fi_enabled=False)
        lakera = active.guardrails["rules"][0]
        blocklist = {**_blocklist_rule(), "config": {"words": ["forbidden"]}}
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [lakera, blocklist]}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == ["forbidden"]

    def test_resending_a_stored_env_ref_is_refused(
        self, auth_client, organization, gateway
    ):
        """A value a save sends is checked whatever the active version holds:
        the admin sees the field named and fixes it in the same save."""
        active = _stored_env_ref_active(organization, lakera_enabled=False)
        fi_rule = active.guardrails["rules"][1]
        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [fi_rule]}}, format="json"
        )
        _assert_refused(response, "guardrails.rules[0].config.base_url")
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_a_switched_off_check_is_refused_on_save_too(
        self, auth_client, organization, gateway
    ):
        """A check that is off is saved for later: its checked fields are
        refused when sent, as an enabled check's are."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        rule = {
            "enabled": False,
            "action": "block",
            "config": {"provider": "lakera", "api_key": ENV_REF},
        }
        response = auth_client.post(
            UPDATE_GUARDRAIL, {"name": "lakera-guard", "config": rule}, format="json"
        )
        _assert_refused(response, "config.config.api_key")
        _no_env_ref_stored(organization)
        _console_blobs_clean(organization)
        gateway.set_org_config.assert_not_called()


# name -> (url, body, sections the active version needs besides guardrails)
UNRELATED_SAVES = {
    "set-budget": (
        f"{GATEWAY}/set-budget/",
        {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
        {},
    ),
    "remove-budget": (
        f"{GATEWAY}/remove-budget/",
        {"level": "org_limit"},
        {"budgets": {"org_limit": {"limit": 100}}},
    ),
    "update-mcp-server": (
        f"{GATEWAY}/update-mcp-server/",
        {"server_id": "docs", "config": {"url": "https://mcp.example.com"}},
        {},
    ),
    "update-mcp-guardrails": (
        f"{GATEWAY}/update-mcp-guardrails/",
        {"config": {"enabled": True}},
        {},
    ),
    "update-config-cache": (UPDATE_CONFIG, {"cache": {"enabled": True}}, {}),
}


@pytest.mark.integration
@pytest.mark.api
class TestUnrelatedSaveIsNeverRefused:
    @pytest.mark.parametrize("save", sorted(UNRELATED_SAVES))
    def test_unrelated_save_on_a_stored_env_ref_config_is_not_refused(
        self, auth_client, organization, gateway, save
    ):
        url, body, sections = UNRELATED_SAVES[save]
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails=_stored_env_ref_guardrails(organization),
            **sections,
        )
        response = auth_client.post(url, body, format="json")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2
        gateway.set_org_config.assert_called()


# ---------------------------------------------------------------------------
# Guardrail sections the push cannot transform, and policy check configs
# that are not objects.
# ---------------------------------------------------------------------------


def _blocklist_check(words=("w",)):
    return {
        "name": "keyword-blocklist",
        "enabled": True,
        "config": {"words": list(words)},
    }


def _untransformable_active(org, **guardrails):
    """An active version whose guardrails the push cannot transform: a check
    whose config is a string."""
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        guardrails={
            "enabled": True,
            "checks": [{"name": "broken", "enabled": True, "config": "oops"}],
            **guardrails,
        },
    )


@pytest.mark.integration
@pytest.mark.api
class TestUntransformableActiveVersionHoldsNothing:
    """The held set of the active version is what that version pushes. A
    version the push cannot transform pushes nothing, so it holds nothing:
    the save is checked strictly, and a save that replaces the broken section
    goes ahead."""

    def test_a_save_that_replaces_the_broken_section_saves(
        self, auth_client, organization, gateway
    ):
        _untransformable_active(organization)
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"checks": [_blocklist_check()]}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == ["w"]
        assert "broken" not in pushed["checks"]

    def test_a_stored_env_ref_is_refused_strictly_there(
        self, auth_client, organization, gateway
    ):
        """Nothing is held, so a stored "${" the save would push is refused
        by name (not by the transform's error)."""
        lakera = _stored_env_ref_guardrails(organization)["rules"][0]
        _untransformable_active(organization)
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"checks": [_blocklist_check(), lakera]}},
            format="json",
        )
        _assert_refused(response, "guardrails.checks.lakera-guard.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()


NOT_AN_OBJECT = {"string": "oops", "list": ["a"], "number": 3, "bool": True}


@pytest.mark.integration
@pytest.mark.api
class TestPolicyCheckConfigMustBeAnObject:
    """A guardrail policy's check config is an object; a string, a list, a
    number or a bool is refused with a 400 that names the field."""

    @pytest.mark.parametrize("kind", sorted(NOT_AN_OBJECT))
    def test_create_refuses_a_config_that_is_not_an_object(
        self, auth_client, organization, gateway, kind
    ):
        active = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(
            POLICIES,
            {
                "name": "typo",
                "checks": [{"name": "broken", "config": NOT_AN_OBJECT[kind]}],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert "checks[0].config" in json.dumps(response.json())
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="typo"
        ).exists()
        active.refresh_from_db()
        assert not active.guardrails
        gateway.set_org_config.assert_not_called()

    def test_patch_refuses_a_config_that_is_not_an_object(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="p-patch-shape",
            checks=[_blocklist_check()],
        )
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/",
            {"checks": [_blocklist_check(), {"name": "broken", "config": "oops"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert "checks[1].config" in json.dumps(response.json())
        policy.refresh_from_db()
        assert policy.checks == [_blocklist_check()]
        gateway.set_org_config.assert_not_called()

    def test_a_string_config_is_refused_at_the_policy_save_then_repair(
        self, auth_client, organization, gateway
    ):
        """The string config is refused at the policy save, so the active
        version never holds it and the later guardrails save goes ahead."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        created = auth_client.post(
            POLICIES,
            {"name": "typo", "checks": [{"name": "broken", "config": "oops"}]},
            format="json",
        )
        assert created.status_code == status.HTTP_400_BAD_REQUEST, created.json()
        assert "broken" not in json.dumps(_active(organization).guardrails or {})
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"checks": [_blocklist_check()]}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == ["w"]

    @pytest.mark.parametrize("config", ["absent", "null"])
    def test_a_check_with_no_config_saves_and_is_pushed(
        self, auth_client, organization, gateway, config
    ):
        """A check with no config (absent or null) is pushed with an empty
        one: it is not refused."""
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        check = {"name": "keyword-blocklist", "enabled": True}
        if config == "null":
            check["config"] = None
        response = auth_client.post(
            POLICIES, {"name": "p-no-config", "checks": [check]}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"] == {}


def _env_ref_lakera_only_active(org, **rule_overrides):
    """An active version whose one rule is the env-ref lakera check (its
    api_key a ${...} held in the console blob), switched on."""
    rule = {**_stored_env_ref_guardrails(org)["rules"][0], **rule_overrides}
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, guardrails={"rules": [rule]}
    )
    return rule


@pytest.mark.integration
@pytest.mark.api
class TestHeldExemptionIsPerCheck:
    """A check with a stored "${" is exempt only when the save pushes it
    exactly as the active version does (apart from enabled). A save that keeps
    the held "${" but changes anything else about the check is refused."""

    @pytest.mark.parametrize(
        "change",
        [
            {"config": {"endpoint": "https://collector.attacker.example/v2/guard"}},
            {"config": {"provider": "presidio"}},
            {"action": "log"},
        ],
        ids=["endpoint", "provider", "action"],
    )
    def test_a_save_that_changes_a_held_check_is_refused(
        self, auth_client, organization, gateway, change
    ):
        rule = _env_ref_lakera_only_active(
            organization,
            config={
                "provider": "lakera",
                "api_key": SENTINEL,
                "endpoint": "https://api.lakera.ai/v2/guard",
            },
        )
        changed = {**rule, **{k: v for k, v in change.items() if k != "config"}}
        changed["config"] = {**rule["config"], **change.get("config", {})}
        response = auth_client.post(
            UPDATE_CONFIG, {"guardrails": {"rules": [changed]}}, format="json"
        )
        _assert_refused(response, "guardrails.checks.lakera-guard.config.api_key")
        assert response.json().get("code") == "guardrail_env_reference"
        assert _active(organization).version == 1
        gateway.set_org_config.assert_not_called()

    def test_a_save_that_leaves_the_held_check_exactly_as_it_is_saves(
        self, auth_client, organization, gateway
    ):
        rule = _env_ref_lakera_only_active(organization)
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [rule, _blocklist_check(["forbidden"])]}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway, organization)
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == ["forbidden"]
        assert pushed["checks"]["lakera-guard"]["enabled"] is True


def _pushed_check(gateway, org, name):
    return _pushed_guardrails(gateway, org)["checks"].get(name)


def _policy_with_env_ref_lakera(org, name="vendor"):
    """A guardrail policy whose lakera check's api_key is a ${...} held in
    its encrypted blob, switched on."""
    return AgentccGuardrailPolicy.no_workspace_objects.create(
        organization=org,
        name=name,
        scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
        is_active=True,
        checks=[
            {
                "name": "lakera-guard",
                "enabled": True,
                "action": "block",
                "config": {"provider": "lakera", "api_key": SENTINEL},
            }
        ],
        encrypted_check_configs=CredentialManager.encrypt(
            {"lakera-guard": {"api_key": ENV_REF}}
        ),
    )


def _policy_synced_org(org, *, other_policy=True):
    """An org whose guardrails were last written by guardrail_sync (a checks
    list): one policy with the env-ref lakera check and, unless other_policy
    is False, one with a blocklist."""
    from agentcc.services.guardrail_sync import sync_guardrail_policies

    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )
    vendor = _policy_with_env_ref_lakera(org)
    words = None
    if other_policy:
        words = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=org,
            name="words",
            scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
            is_active=True,
            checks=[_blocklist_check()],
        )
    assert sync_guardrail_policies(org)
    assert isinstance(_active(org).guardrails["checks"], list)
    return vendor, words


@pytest.mark.integration
@pytest.mark.api
class TestSwitchingAnEnvRefCheckOff:
    """What U10 and F5 say about switching a check off, per stored shape. A
    rules-editor save on a policy-written checks list holds until the next
    policy sync, which rewrites the section from the policies."""

    def test_rules_list_the_switch_switches_it_off(
        self, auth_client, organization, gateway
    ):
        _env_ref_lakera_only_active(organization)
        response = auth_client.post(
            TOGGLE_GUARDRAIL, {"name": "lakera-guard", "enabled": False}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False

    def test_checks_map_the_rules_editor_save_switches_it_off(
        self, auth_client, organization, gateway
    ):
        rule = _stored_env_ref_guardrails(organization)["rules"][0]
        check = {k: v for k, v in rule.items() if k != "name"}
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails={"checks": {"lakera-guard": check}},
        )
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"checks": {"lakera-guard": {"enabled": False}}}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False

    @pytest.mark.parametrize("path", ["toggle-guardrail", "update-guardrail"])
    def test_checks_map_the_switch_switches_it_off(
        self, auth_client, organization, gateway, path
    ):
        rule = _stored_env_ref_guardrails(organization)["rules"][0]
        check = {k: v for k, v in rule.items() if k != "name"}
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            guardrails={"checks": {"lakera-guard": check}},
        )
        if path == "toggle-guardrail":
            response = auth_client.post(
                TOGGLE_GUARDRAIL,
                {"name": "lakera-guard", "enabled": False},
                format="json",
            )
        else:
            response = auth_client.post(
                UPDATE_GUARDRAIL,
                {"name": "lakera-guard", "config": {**check, "enabled": False}},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
        stored = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert set(stored.guardrails) == {"checks"}
        assert stored.guardrails["checks"]["lakera-guard"]["enabled"] is False

    @pytest.mark.parametrize("other_policy", [True, False], ids=["two", "only"])
    def test_checks_list_patching_the_policy_switches_it_off_for_good(
        self, auth_client, organization, gateway, other_policy
    ):
        vendor, words = _policy_synced_org(organization, other_policy=other_policy)
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is True
        response = auth_client.patch(
            f"{POLICIES}{vendor.id}/",
            {"checks": [{**vendor.checks[0], "enabled": False}]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
        # A later sync (a policy save) keeps it off.
        later = words or vendor
        response = auth_client.patch(
            f"{POLICIES}{later.id}/", {"description": "touched"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False

    @pytest.mark.parametrize("how", ["deactivate", "delete"])
    def test_checks_list_removing_the_policy_while_another_remains(
        self, auth_client, organization, gateway, how
    ):
        vendor, _ = _policy_synced_org(organization)
        if how == "deactivate":
            response = auth_client.patch(
                f"{POLICIES}{vendor.id}/", {"is_active": False}, format="json"
            )
        else:
            response = auth_client.delete(f"{POLICIES}{vendor.id}/")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _pushed_check(gateway, organization, "lakera-guard") is None

    def test_checks_list_a_rules_editor_save_holds_until_the_next_policy_sync(
        self, auth_client, organization, gateway
    ):
        """The Guardrails page's rules editor saves a policy-written list as
        rules and empties the list (guardrailsForSave): the check is off, but
        the next policy sync rewrites the section from the policies."""
        vendor, words = _policy_synced_org(organization)
        rules = [
            {
                "name": "lakera-guard",
                "stage": "pre",
                "mode": "sync",
                **vendor.checks[0],
                "enabled": False,
                "_policy": str(vendor.id),
            },
            {
                "stage": "pre",
                "mode": "sync",
                **_blocklist_check(),
                "_policy": str(words.id),
            },
        ]
        response = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": rules, "checks": []}},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
        response = auth_client.patch(
            f"{POLICIES}{words.id}/", {"description": "touched"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is True

    @pytest.mark.parametrize("how", ["deactivate", "delete"])
    def test_checks_list_removing_the_last_policy_switches_it_off(
        self, auth_client, organization, gateway, how
    ):
        """With no check left in any active policy, guardrail_sync stores an
        empty checks list and the push sends an empty section, so the gateway
        stops running the check (test_guardrail_section_emptied.py has the
        rest)."""
        vendor, _ = _policy_synced_org(organization, other_policy=False)
        pushes = gateway.set_org_config.call_count
        if how == "deactivate":
            response = auth_client.patch(
                f"{POLICIES}{vendor.id}/", {"is_active": False}, format="json"
            )
        else:
            response = auth_client.delete(f"{POLICIES}{vendor.id}/")
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert gateway.set_org_config.call_count == pushes + 1
        assert _pushed_check(gateway, organization, "lakera-guard") is None
        assert _pushed_guardrails(gateway, organization) == {"checks": {}}
        assert _active(organization).guardrails == {"checks": []}

    @pytest.mark.parametrize("path", ["toggle-guardrail", "update-guardrail"])
    def test_checks_list_the_switch_switches_it_off_for_good(
        self, auth_client, organization, gateway, path
    ):
        """The switch and update-guardrail switch the check off in the policy
        it came from, so a later sync keeps it off
        (test_guardrail_switch_checks_list.py has the rest)."""
        vendor, words = _policy_synced_org(organization)
        if path == "toggle-guardrail":
            response = auth_client.post(
                TOGGLE_GUARDRAIL,
                {"name": "lakera-guard", "enabled": False},
                format="json",
            )
        else:
            response = auth_client.post(
                UPDATE_GUARDRAIL,
                {
                    "name": "lakera-guard",
                    "config": {
                        **{k: v for k, v in vendor.checks[0].items() if k != "name"},
                        "enabled": False,
                    },
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
        response = auth_client.patch(
            f"{POLICIES}{words.id}/", {"description": "touched"}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization, "lakera-guard")["enabled"] is False
