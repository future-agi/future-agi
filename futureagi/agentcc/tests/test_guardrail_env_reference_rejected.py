"""Org guardrail config may not hold "${" in a setting the gateway refuses it in.

Org config never resolves a ${NAME}; test_guardrail_env_reference_fields.py
lists the settings the gateway refuses a "${" in and reads that list from the
gateway source.

Django refuses "${" in those strings and nowhere else: a keyword, an
expression, a rule name, a nested value and a map key are
plain text the gateway never resolves, and they save and push. The refusal is
a 400 naming the field, never the value, on every save that sets guardrail
config and on version activation. A save that leaves guardrails as they are
(a budget, an MCP server, routing) is never refused for them.

No variable here is resolved and none names a real secret.
"""

import copy
import json
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from accounts.models.user import OrgApiKey
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import OrgConfigRejected, validate_org_config
from integrations.services.credentials import CredentialManager

# The checks-object shape the console saves.
CLEAN_GUARDRAILS = {
    "checks": {
        "x": {
            "enabled": True,
            "action": "log",
            "config": {
                "provider": "futureagi",
                "base_url": "https://evals.example.com",
                "api_key": "literal-key",
                "secret_key": "literal-secret",
                "eval_id": "toxicity",
            },
        }
    },
}

# A guardrails section with a "${" in futureagi settings the gateway refuses it in.
REFERENCE_GUARDRAILS = copy.deepcopy(CLEAN_GUARDRAILS)
REFERENCE_GUARDRAILS["checks"]["x"]["config"].update(
    {
        "base_url": "https://collector.attacker.example",
        "api_key": "${EXAMPLE_TOKEN}",
        "secret_key": "${EXAMPLE_SECRET}",
    }
)

# A console save of the keyword blocklist (rules-list shape) whose word holds
# "${", and the guardrails section Django pushes for it. The gateway
# test cmd/agentcc/guardrail_org_env_test.go (djangoBlocklistPush) decodes the
# same JSON and shows that it blocks the prompt holding the word.
BLOCKLIST_RULE = {
    "name": "keyword-blocklist",
    "enabled": True,
    "action": "block",
    "threshold": 0.8,
    "config": {"words": ["${jndi:"]},
}
DJANGO_BLOCKLIST_PUSH = json.loads(
    '{"guardrails": {"checks": {"keyword-blocklist": {"enabled": true, "action": "block", '
    '"confidence_threshold": 0.8, "config": {"words": ["${jndi:"]}}}, "fail_open": false, '
    '"pipeline_mode": "parallel"}}'
)

# Strings the gateway never resolves: a "${" in each is plain text.
FREE_TEXT_CHECKS = {
    # A keyword-blocklist word and a topic list (built-in checks).
    "keyword-blocklist": {"enabled": True, "config": {"words": ["${jndi:"]}},
    # An expression check's expression.
    "no-templates": {
        "enabled": True,
        "config": {"expression": 'request.messages[0].content contains "${"'},
    },
    # A webhook check's header (the gateway never resolves ${NAME} in a webhook
    # check).
    "my-hook": {
        "enabled": True,
        "config": {
            "url": "https://hooks.example.com/check",
            "headers": {"X-Note": "${not-resolved}"},
        },
    },
    # A futureagi check's other settings.
    "fi-other": {
        "enabled": True,
        "config": {
            "provider": "futureagi",
            "api_key": "literal-key",
            "secret_key": "literal-secret",
            "eval_id": "toxicity",
            "call_type": "${not-a-reference}",
        },
    },
    # An external check's nested rule text and list items.
    "gray": {
        "enabled": True,
        "config": {
            "provider": "grayswan",
            "api_key": "literal-key",
            "rules": {"no-log4shell": "Flag a message that holds ${jndi: lookups"},
            "policy_ids": ["${p}"],
        },
    },
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


def _assert_names_field_not_value(response, field):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = json.dumps(response.json())
    assert field in body, body
    assert "environment reference" in body, body
    assert "EXAMPLE_TOKEN" not in body and "EXAMPLE_SECRET" not in body


@contextmanager
def _gateway():
    """The gateway client every push path uses, as a mock that records pushes."""
    client = MagicMock()
    with patch("agentcc.services.config_push.get_gateway_client", return_value=client):
        yield client


def _pushed_guardrails(client):
    client.set_org_config.assert_called_once()
    _org_id, payload = client.set_org_config.call_args.args
    return payload.get("guardrails")


class TestValidateOrgConfig:
    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_rejects_a_reference_in_the_payload(self, _providers):
        config = AgentccOrgConfig(
            organization_id=uuid.uuid4(), guardrails=REFERENCE_GUARDRAILS
        )

        with pytest.raises(OrgConfigRejected) as rejected:
            validate_org_config(config)

        message = str(rejected.value)
        assert "guardrails.checks.x.config.api_key" in message
        assert "environment reference" in message
        assert "EXAMPLE_TOKEN" not in message

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_accepts_clean_guardrails(self, _providers):
        validate_org_config(
            AgentccOrgConfig(organization_id=uuid.uuid4(), guardrails=CLEAN_GUARDRAILS)
        )

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_a_save_that_keeps_guardrails_skips_the_check(self, _providers):
        # Budget, MCP and routing-policy saves copy guardrails as they are.
        validate_org_config(
            AgentccOrgConfig(
                organization_id=uuid.uuid4(), guardrails=REFERENCE_GUARDRAILS
            ),
            check_guardrails=False,
        )

    @patch("agentcc.services.config_push._assemble_providers", return_value={})
    def test_accepts_text_the_gateway_never_resolves(self, _providers):
        validate_org_config(
            AgentccOrgConfig(
                organization_id=uuid.uuid4(),
                guardrails={"checks": copy.deepcopy(FREE_TEXT_CHECKS)},
            )
        )


@pytest.mark.integration
@pytest.mark.api
class TestEnvReferenceRefusedOnEverySave:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_org_config_create(self, mock_push, auth_client, organization):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": REFERENCE_GUARDRAILS}, format="json"
        )

        _assert_names_field_not_value(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_org_config_create_rules_list(self, mock_push, auth_client, organization):
        # The console's Guardrails tab saves the rules-list shape. A
        # futureagi-eval rule gets provider futureagi from its name.
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/org-configs/",
            {
                "guardrails": {
                    "rules": [
                        {
                            "name": "futureagi-eval",
                            "enabled": True,
                            "action": "block",
                            "config": {
                                "base_url": "https://collector.attacker.example",
                                "api_key": "${EXAMPLE_TOKEN}",
                                "secret_key": "literal-secret",
                                "eval_id": "toxicity",
                            },
                        }
                    ]
                }
            },
            format="json",
        )

        _assert_names_field_not_value(response, "guardrails.rules[0].config.api_key")
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_org_config_create_external_setting(
        self, mock_push, auth_client, organization
    ):
        _make_config(organization)
        guardrails = {
            "checks": {
                "lakera-guard": {
                    "enabled": True,
                    "config": {
                        "provider": "lakera",
                        "endpoint": "https://collector.attacker.example/v2/guard",
                        "api_key": "${EXAMPLE_TOKEN}",
                    },
                }
            }
        }

        response = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": guardrails}, format="json"
        )

        _assert_names_field_not_value(
            response, "guardrails.checks.lakera-guard.config.api_key"
        )
        mock_push.assert_not_called()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config(self, mock_push, auth_client, organization):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"guardrails": REFERENCE_GUARDRAILS},
            format="json",
        )

        _assert_names_field_not_value(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_guardrail(self, mock_push, auth_client, organization):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/gateways/default/update-guardrail/",
            {
                "name": "fi-check",
                "config": {
                    "enabled": True,
                    "action": "log",
                    "config": {
                        "provider": "futureagi",
                        "base_url": "https://collector.attacker.example",
                        "api_key": "${EXAMPLE_TOKEN}",
                        "secret_key": "literal-secret",
                        "eval_id": "toxicity",
                    },
                },
            },
            format="json",
        )

        # The request's own field: update-guardrail's body is {name, config}.
        _assert_names_field_not_value(response, "config.config.api_key")
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_guardrail_futureagi_eval_on_this_platform(
        self, mock_push, auth_client, organization
    ):
        # update-guardrail checks the request body as sent, so the refusal
        # names the request's own field.
        if not OrgApiKey.no_workspace_objects.filter(
            organization=organization, type="system", enabled=True, deleted=False
        ).exists():
            OrgApiKey.no_workspace_objects.create(
                organization=organization,
                type="system",
                api_key=f"t-sys-key-{uuid.uuid4().hex}",
                secret_key="t-sys-secret",
            )
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/gateways/default/update-guardrail/",
            {
                "name": "futureagi-eval",
                "config": {
                    "enabled": True,
                    "action": "log",
                    "config": {
                        "api_key": "${EXAMPLE_TOKEN}",
                        "secret_key": "${EXAMPLE_SECRET}",
                        "eval_id": "toxicity",
                    },
                },
            },
            format="json",
        )

        _assert_names_field_not_value(response, "config.config.api_key")
        assert _versions(organization) == [(1, True)]
        assert "${" not in json.dumps(_active(organization).guardrails)
        mock_push.assert_not_called()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_toggle_guardrail_of_a_check_holding_a_reference(
        self, mock_push, auth_client, organization
    ):
        # Turning a check on or off changes guardrails, so the stored config is
        # checked, and the answer names the field to fix as it is stored.
        # (toggle-guardrail writes the rules list; on a config stored as a
        # checks object it is refused by the contract.)
        rule = {
            "name": "x",
            "enabled": False,
            "action": "block",
            "config": REFERENCE_GUARDRAILS["checks"]["x"]["config"],
        }
        _make_config(organization, guardrails={"rules": [rule]})

        response = auth_client.post(
            "/agentcc/gateways/default/toggle-guardrail/",
            {"name": "x", "enabled": True},
            format="json",
        )

        _assert_names_field_not_value(response, "guardrails.rules[0].config.api_key")
        assert _versions(organization) == [(1, True)]
        mock_push.assert_not_called()

    @patch("agentcc.views.guardrail_policy.sync_guardrail_policies", return_value=True)
    def test_guardrail_policy_create(self, mock_sync, auth_client, organization):
        response = auth_client.post(
            "/agentcc/guardrail-policies/",
            {
                "name": "outbound",
                "scope": "global",
                "mode": "enforce",
                "checks": [
                    {
                        "name": "x",
                        "config": REFERENCE_GUARDRAILS["checks"]["x"]["config"],
                    }
                ],
            },
            format="json",
        )

        _assert_names_field_not_value(response, "checks[0].config.api_key")
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="outbound"
        ).exists()
        mock_sync.assert_not_called()

    @patch("agentcc.views.guardrail_policy.sync_guardrail_policies", return_value=True)
    def test_guardrail_policy_patch(self, mock_sync, auth_client, organization):
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="clean",
            checks=[{"name": "x", "config": {"provider": "futureagi"}}],
        )

        response = auth_client.patch(
            f"/agentcc/guardrail-policies/{policy.id}/",
            {
                "checks": [
                    {
                        "name": "x",
                        "config": REFERENCE_GUARDRAILS["checks"]["x"]["config"],
                    }
                ]
            },
            format="json",
        )

        _assert_names_field_not_value(response, "checks[0].config.api_key")
        policy.refresh_from_db()
        assert policy.checks == [{"name": "x", "config": {"provider": "futureagi"}}]
        assert policy.encrypted_check_configs is None
        mock_sync.assert_not_called()

    @patch("agentcc.views.guardrail_policy.sync_guardrail_policies", return_value=True)
    def test_guardrail_policy_patch_external_endpoint(
        self, mock_sync, auth_client, organization
    ):
        # A policy check named lakera-guard gets provider lakera from its name.
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="clean", checks=[]
        )

        response = auth_client.patch(
            f"/agentcc/guardrail-policies/{policy.id}/",
            {
                "checks": [
                    {
                        "name": "lakera-guard",
                        "config": {
                            "endpoint": "${EXAMPLE_TOKEN}",
                            "api_key": "k",
                        },
                    }
                ]
            },
            format="json",
        )

        _assert_names_field_not_value(response, "checks[0].config.endpoint")
        mock_sync.assert_not_called()

    def test_org_config_create_with_a_reference_stored_encrypted(
        self, auth_client, organization
    ):
        # A "${" stored in the console policy's encrypted keys is
        # shown to the console as "__encrypted__". A save that changes
        # guardrails is checked on the built payload, where config push has
        # decrypted it, and names the field.
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name="__ui_guardrails__",
            checks=[
                {
                    "name": "x",
                    "enabled": True,
                    "action": "log",
                    "config": {
                        **CLEAN_GUARDRAILS["checks"]["x"]["config"],
                        "api_key": "__encrypted__",
                        "secret_key": "__encrypted__",
                    },
                }
            ],
            encrypted_check_configs=CredentialManager.encrypt(
                {"x": {"api_key": "${EXAMPLE_TOKEN}", "secret_key": "s"}}
            ),
        )
        stored = {
            "checks": {
                "x": {
                    **policy.checks[0],
                    "_policy": str(policy.id),
                }
            }
        }
        stored["checks"]["x"].pop("name")
        _make_config(organization, guardrails=stored)
        changed = copy.deepcopy(stored)
        changed["checks"]["x"]["action"] = "block"

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/", {"guardrails": changed}, format="json"
            )

        _assert_names_field_not_value(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, True)]
        gateway.set_org_config.assert_not_called()

    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_activate_of_an_older_version(self, mock_push, auth_client, organization):
        # A stored older version.
        stale = _make_config(
            organization, version=1, is_active=False, guardrails=REFERENCE_GUARDRAILS
        )
        _make_config(organization, version=2, is_active=True)

        response = auth_client.post(f"/agentcc/org-configs/{stale.id}/activate/")

        _assert_names_field_not_value(response, "guardrails.checks.x.config.api_key")
        assert _versions(organization) == [(1, False), (2, True)]
        mock_push.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestTextTheGatewayNeverResolvesSaves:
    """A "${" outside the strings the gateway resolves is plain text, so
    these save."""

    def test_org_config_create_blocklist_word_is_pushed_as_written(
        self, auth_client, organization
    ):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"guardrails": {"rules": [BLOCKLIST_RULE]}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_guardrails(gateway) == DJANGO_BLOCKLIST_PUSH["guardrails"]

    def test_update_config(self, auth_client, organization):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {"guardrails": {"checks": copy.deepcopy(FREE_TEXT_CHECKS)}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway)["checks"]
        assert pushed["keyword-blocklist"]["config"]["words"] == ["${jndi:"]
        assert pushed["gray"]["config"]["rules"] == {
            "no-log4shell": "Flag a message that holds ${jndi: lookups"
        }

    def test_update_guardrail(self, auth_client, organization):
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-guardrail/",
                {"name": "keyword-blocklist", "config": BLOCKLIST_RULE},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_guardrails(gateway) == DJANGO_BLOCKLIST_PUSH["guardrails"]

    def test_toggle_guardrail_name(self, auth_client, organization):
        # A rule name is never resolved either.
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/toggle-guardrail/",
                {"name": "${EXAMPLE_TOKEN}", "enabled": False},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        gateway.set_org_config.assert_called_once()

    def test_guardrail_policy_create_and_patch(self, auth_client, organization):
        _make_config(organization)

        with _gateway() as gateway:
            created = auth_client.post(
                "/agentcc/guardrail-policies/",
                {
                    "name": "blocklist",
                    "scope": "global",
                    "mode": "enforce",
                    "checks": [BLOCKLIST_RULE],
                },
                format="json",
            )
        assert created.status_code == status.HTTP_200_OK, created.json()
        pushed = _pushed_guardrails(gateway)["checks"]
        assert pushed["keyword-blocklist"]["config"]["words"] == ["${jndi:"]

        policy_id = created.json()["result"]["id"]
        rule = {**BLOCKLIST_RULE, "config": {"words": ["${jndi:", "${env:"]}}
        with _gateway() as gateway:
            patched = auth_client.patch(
                f"/agentcc/guardrail-policies/{policy_id}/",
                {"checks": [rule]},
                format="json",
            )
        assert patched.status_code == status.HTTP_200_OK, patched.json()
        pushed = _pushed_guardrails(gateway)["checks"]
        assert pushed["keyword-blocklist"]["config"]["words"] == ["${jndi:", "${env:"]

    def test_activate_of_a_version_with_a_blocklist_word(
        self, auth_client, organization
    ):
        older = _make_config(
            organization,
            version=1,
            is_active=False,
            guardrails={"rules": [BLOCKLIST_RULE]},
        )
        _make_config(organization, version=2, is_active=True)

        with _gateway() as gateway:
            response = auth_client.post(f"/agentcc/org-configs/{older.id}/activate/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, True), (2, False)]
        assert _pushed_guardrails(gateway) == DJANGO_BLOCKLIST_PUSH["guardrails"]


@pytest.mark.integration
@pytest.mark.api
class TestUnrelatedSaveIsNotRefused:
    """An org whose active config holds a "${" in a setting the gateway refuses
    it in: a save that leaves guardrails as they are works, and the gateway
    refuses that one check until it is fixed."""

    @pytest.fixture
    def org_with_reference(self, organization):
        _make_config(
            organization,
            guardrails=copy.deepcopy(REFERENCE_GUARDRAILS),
            mcp={"servers": {"server-a": {"url": "http://mcp/server-a"}}},
            budgets={"org_limit": {"limit": 100, "on_exceed": "warn"}},
        )
        return organization

    @pytest.mark.parametrize(
        "path, body",
        [
            (
                "set-budget",
                {"level": "org_limit", "config": {"limit": 200, "on_exceed": "warn"}},
            ),
            ("remove-budget", {"level": "org_limit"}),
            (
                "update-mcp-server",
                {
                    "server_id": "server-b",
                    "config": {"transport": "http", "url": "http://mcp/server-b"},
                },
            ),
            ("remove-mcp-server", {"server_id": "server-a"}),
            ("update-mcp-guardrails", {"config": {"enabled": True}}),
            ("update-config", {"routing": {"strategy": "round_robin"}}),
        ],
    )
    def test_gateway_save(self, auth_client, org_with_reference, path, body):
        with _gateway() as gateway:
            response = auth_client.post(
                f"/agentcc/gateways/default/{path}/", body, format="json"
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(org_with_reference) == [(1, False), (2, True)]
        assert _active(org_with_reference).guardrails == REFERENCE_GUARDRAILS
        gateway.set_org_config.assert_called_once()

    def test_update_config_that_sends_the_stored_guardrails_back(
        self, auth_client, org_with_reference
    ):
        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/gateways/default/update-config/",
                {
                    "guardrails": copy.deepcopy(REFERENCE_GUARDRAILS),
                    "routing": {"strategy": "round_robin"},
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(org_with_reference).routing == {"strategy": "round_robin"}
        gateway.set_org_config.assert_called_once()

    def test_org_config_create_that_keeps_the_stored_guardrails(
        self, auth_client, org_with_reference
    ):
        # The Fallbacks page saves routing by posting the whole active config
        # back, guardrails included as stored.
        active = auth_client.get("/agentcc/org-configs/active/").json()["result"]

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/",
                {
                    "guardrails": active["guardrails"],
                    "routing": {"strategy": "round_robin"},
                    "mcp": active["mcp"],
                    "budgets": active["budgets"],
                    "change_description": "Updated fallback & reliability config",
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(org_with_reference) == [(1, False), (2, True)]
        assert _active(org_with_reference).routing == {"strategy": "round_robin"}
        gateway.set_org_config.assert_called_once()


@pytest.mark.integration
@pytest.mark.api
class TestCleanGuardrailSaveStillPushes:
    @patch(
        "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway",
        return_value=True,
    )
    def test_org_config_create(self, mock_push, auth_client, organization):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/org-configs/", {"guardrails": CLEAN_GUARDRAILS}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, False), (2, True)]
        mock_push.assert_called_once()

    def test_org_config_create_rules_list(self, auth_client, organization):
        _make_config(organization)
        rule = {
            "name": "x",
            "enabled": True,
            "action": "log",
            "config": CLEAN_GUARDRAILS["checks"]["x"]["config"],
        }

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/org-configs/",
                {"guardrails": {"rules": [rule]}},
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway)["checks"]["x"]["config"]
        assert pushed["api_key"] == "literal-key"
        assert pushed["base_url"] == "https://evals.example.com"

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config(self, mock_push, auth_client, organization):
        _make_config(organization)

        response = auth_client.post(
            "/agentcc/gateways/default/update-config/",
            {"guardrails": CLEAN_GUARDRAILS},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _versions(organization) == [(1, False), (2, True)]
        mock_push.assert_called_once()

    def test_guardrail_policy_create(self, auth_client, organization):
        # An active org config is what the policy sync writes into and pushes;
        # without one it pushes nothing.
        _make_config(organization)

        with _gateway() as gateway:
            response = auth_client.post(
                "/agentcc/guardrail-policies/",
                {
                    "name": "clean",
                    "scope": "global",
                    "mode": "enforce",
                    "checks": [
                        {
                            "name": "x",
                            "config": CLEAN_GUARDRAILS["checks"]["x"]["config"],
                        }
                    ],
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = _pushed_guardrails(gateway)["checks"]["x"]["config"]
        # The policy stores the keys encrypted; the push carries them decrypted.
        assert pushed["api_key"] == "literal-key"
        assert pushed["secret_key"] == "literal-secret"

    def test_guardrail_policy_patch(self, auth_client, organization):
        _make_config(organization)
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="clean", checks=[]
        )

        with _gateway() as gateway:
            response = auth_client.patch(
                f"/agentcc/guardrail-policies/{policy.id}/",
                {
                    "checks": [
                        {
                            "name": "x",
                            "config": CLEAN_GUARDRAILS["checks"]["x"]["config"],
                        }
                    ]
                },
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushed_guardrails(gateway)["checks"]["x"]["config"]
        assert pushed["api_key"] == "literal-key"
