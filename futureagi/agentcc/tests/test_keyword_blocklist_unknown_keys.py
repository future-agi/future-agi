"""A keyword-blocklist key the check never reads is refused (docs-agentcc-711,
item 1; cloud proof j-20261028t0728-c95dee, record 1-blocklists).

The gateway's keyword-blocklist check reads words, whole_word and
max_matches. On the proof's gateway a check that named a stored blocklist
(config.blocklist_id / blocklist_ids) saved with warnings [], was pushed
verbatim and blocked none of the list's words, while an inline word in
config.words was blocked. Such a key is now refused with 400
keyword_blocklist_unknown_key on the org-config writer (update-config, POST
org-configs, the guardrail actions) and on guardrail policy writes. A key
the stored check already holds with the same value is kept, so an org that
saved one before is not refused on saves that leave it alone. A checks-map
patch removes it with null; replacement arrays (including policy checks)
remove it by omission.

Pushes go to a recording gateway client through the real push code.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
UPDATE_GUARDRAIL = f"{GATEWAY}/update-guardrail/"
ORG_CONFIGS = "/agentcc/org-configs/"
POLICIES = "/agentcc/guardrail-policies/"
CODE = "keyword_blocklist_unknown_key"
MAP_PARAM = "guardrails.checks.keyword-blocklist.config"


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _pushes(gateway, org):
    return [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]


def _pushed_check(gateway, org):
    sent = _pushes(gateway, org)
    assert sent, "nothing was pushed"
    return sent[-1]["guardrails"]["checks"]["keyword-blocklist"]


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _seed(org, config):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        guardrails={
            "checks": {
                "keyword-blocklist": {
                    "enabled": True,
                    "action": "block",
                    "config": config,
                }
            }
        },
    )


def _check_patch(config):
    return {
        "guardrails": {
            "checks": {
                "keyword-blocklist": {
                    "enabled": True,
                    "action": "block",
                    "config": config,
                }
            }
        }
    }


def _refused(response, param):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = response.json()
    assert body["code"] == CODE, body
    assert body["param"] == param, body


@pytest.mark.integration
@pytest.mark.api
class TestUpdateConfig:
    @pytest.mark.parametrize("key", ["blocklist_id", "blocklist_ids", "case_sensitive"])
    def test_an_unknown_key_is_refused_and_nothing_is_saved_or_pushed(
        self, auth_client, organization, gateway, key
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        response = auth_client.post(
            UPDATE_CONFIG,
            _check_patch({"words": ["controlword"], key: "gwp-list"}),
            format="json",
        )

        _refused(response, f"{MAP_PARAM}.{key}")
        assert "config.words" in response.json()["message"]
        assert _active(organization).version == 1
        assert _pushes(gateway, organization) == []

    def test_the_settings_the_check_reads_are_saved_and_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        config = {"words": ["controlword"], "whole_word": True, "max_matches": 2}

        response = auth_client.post(UPDATE_CONFIG, _check_patch(config), format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["warnings"] == []
        assert _pushed_check(gateway, organization)["config"] == config

    def test_a_rules_list_is_checked_too(self, auth_client, organization, gateway):
        response = auth_client.post(
            UPDATE_CONFIG,
            {
                "guardrails": {
                    "rules": [
                        {
                            "name": "keyword-blocklist",
                            "action": "block",
                            "config": {"words": ["w"], "blocklist_id": "b"},
                        }
                    ]
                }
            },
            format="json",
        )

        _refused(response, "guardrails.rules.0.config.blocklist_id")
        assert _pushes(gateway, organization) == []

    def test_post_org_configs_refuses_it_too(self, auth_client, organization, gateway):
        response = auth_client.post(
            ORG_CONFIGS,
            {
                "guardrails": {
                    "rules": [
                        {
                            "name": "keyword-blocklist",
                            "config": {"words": ["w"], "blocklist_id": "b"},
                        }
                    ]
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert CODE in response.content.decode()
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()
        assert _pushes(gateway, organization) == []


@pytest.mark.integration
@pytest.mark.api
class TestAKeyStoredBeforeThisRelease:
    LEGACY = {"words": ["a"], "blocklist_id": "legacy-list"}

    def test_a_save_that_leaves_guardrails_alone_is_not_refused(
        self, auth_client, organization, gateway
    ):
        _seed(organization, dict(self.LEGACY))

        response = auth_client.post(
            UPDATE_CONFIG, {"cache": {"enabled": True}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2

    def test_editing_the_words_keeps_it(self, auth_client, organization, gateway):
        _seed(organization, dict(self.LEGACY))

        response = auth_client.post(
            UPDATE_CONFIG, _check_patch({"words": ["a", "b"]}), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails["checks"]["keyword-blocklist"]
        assert stored["config"] == {"words": ["a", "b"], "blocklist_id": "legacy-list"}

    def test_changing_it_is_refused(self, auth_client, organization, gateway):
        _seed(organization, dict(self.LEGACY))

        response = auth_client.post(
            UPDATE_CONFIG, _check_patch({"blocklist_id": "another"}), format="json"
        )

        _refused(response, f"{MAP_PARAM}.blocklist_id")
        assert _active(organization).version == 1

    def test_null_removes_it(self, auth_client, organization, gateway):
        _seed(organization, dict(self.LEGACY))

        response = auth_client.post(
            UPDATE_CONFIG, _check_patch({"blocklist_id": None}), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization)["config"] == {"words": ["a"]}

    def test_update_guardrail_resaving_the_check_as_stored_is_not_refused(
        self, auth_client, organization, gateway
    ):
        _seed(organization, dict(self.LEGACY))

        response = auth_client.post(
            UPDATE_GUARDRAIL,
            {
                "name": "keyword-blocklist",
                "config": {
                    "enabled": True,
                    "action": "block",
                    "config": {"words": ["a", "c"], "blocklist_id": "legacy-list"},
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()


def _policy_body(config):
    return {
        "name": "words",
        "checks": [
            {
                "name": "keyword-blocklist",
                "action": "block",
                "enabled": True,
                "config": config,
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.api
class TestGuardrailPolicies:
    def test_create_refuses_an_unknown_key(self, auth_client, organization, gateway):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        response = auth_client.post(
            POLICIES,
            _policy_body({"words": ["policyword"], "blocklist_id": "gwp-list"}),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
        assert response.json()["code"] == CODE
        assert "checks.0.config.blocklist_id" in response.json()["message"]
        assert not AgentccGuardrailPolicy.all_objects.filter(
            organization=organization
        ).exists()
        assert _pushes(gateway, organization) == []

    def test_create_with_inline_words_is_pushed(
        self, auth_client, organization, gateway
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        response = auth_client.post(
            POLICIES, _policy_body({"words": ["policyword"]}), format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _pushed_check(gateway, organization)["config"] == {
            "words": ["policyword"]
        }

    @pytest.mark.parametrize("method", ["put", "patch"])
    @pytest.mark.parametrize("rejected_value", ["another-list", None])
    def test_legacy_policy_cleanup_follows_replacement_array_advice(
        self, auth_client, organization, gateway, method, rejected_value
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        legacy = {"words": ["policyword"], "blocklist_id": "legacy-list"}
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, **_policy_body(legacy)
        )
        url = f"{POLICIES}{policy.id}/"
        refused = getattr(auth_client, method)(
            url,
            _policy_body({**legacy, "blocklist_id": rejected_value}),
            format="json",
        )

        assert refused.status_code == 400, refused.json()
        assert refused.json()["code"] == CODE
        message = refused.json()["message"].lower()
        assert "omit the key" in message and "replacement" in message
        policy.refresh_from_db()
        assert policy.checks[0]["config"] == legacy
        gateway.set_org_config.assert_not_called()

        # Policy checks replace the array: follow the error's advice by
        # omitting blocklist_id, not by retaining it with a null value.
        cleaned = {"words": ["policyword"], "whole_word": True}
        response = getattr(auth_client, method)(
            url, _policy_body(cleaned), format="json"
        )

        assert response.status_code == 200, response.json()
        policy.refresh_from_db()
        assert policy.checks[0]["config"] == cleaned
        assert _pushed_check(gateway, organization)["config"] == cleaned
        stored = _active(organization).guardrails["checks"][0]
        assert stored["config"] == cleaned

    def test_update_keeps_a_stored_key_and_refuses_a_new_one(
        self, auth_client, organization, gateway
    ):
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            **_policy_body({"words": ["w"], "blocklist_id": "legacy-list"}),
        )
        url = f"{POLICIES}{policy.id}/"

        kept = auth_client.patch(
            url,
            {
                "checks": _policy_body(
                    {"words": ["w", "x"], "blocklist_id": "legacy-list"}
                )["checks"]
            },
            format="json",
        )
        added = auth_client.patch(
            url,
            {
                "checks": _policy_body({"words": ["w"], "blocklist_ids": ["n"]})[
                    "checks"
                ]
            },
            format="json",
        )

        assert kept.status_code == status.HTTP_200_OK, kept.json()
        assert added.status_code == status.HTTP_400_BAD_REQUEST, added.json()
        assert added.json()["code"] == CODE
        policy.refresh_from_db()
        assert policy.checks[0]["config"] == {
            "words": ["w", "x"],
            "blocklist_id": "legacy-list",
        }
