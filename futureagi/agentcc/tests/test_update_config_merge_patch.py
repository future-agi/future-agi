"""update-config takes the console's RFC 7386 merge patches.

A null deletes a key at any depth below a section, including directly under
it, and an absent key is kept. These are the exact bodies the console writers
(frontend/src/sections/gateway/hooks/useConfigPatch.js) send.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.config_push import _build_payload, _transform_guardrails

URL = "/agentcc/gateways/default/update-config/"

PII_POLICY = str(uuid.uuid4())
MODERATION_POLICY = str(uuid.uuid4())

# The guardrails section as agentcc/services/guardrail_sync.py stores it.
SYNCED_GUARDRAILS = {
    "enabled": True,
    "checks": [
        {
            "name": "pii-detector",
            "enabled": True,
            "action": "block",
            "threshold": 0.6,
            "config": {"entities": ["EMAIL_ADDRESS"]},
            "_policy": PII_POLICY,
            "_policy_name": "PII",
            "_mode": "sync",
            "_scope": "global",
        },
    ],
}

PII_RULE_DISABLED = {
    "name": "pii-detector",
    "stage": "pre",
    "mode": "sync",
    "action": "block",
    "threshold": 0.6,
    "enabled": False,
    "config": {"entities": ["EMAIL_ADDRESS"]},
    "_policy": PII_POLICY,
}


def _make_config(organization, **fields):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, **fields
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


@pytest.mark.integration
@pytest.mark.api
@patch("agentcc.views.gateway.push_org_config", return_value=True)
class TestUpdateConfigMergePatch:
    def test_an_emptied_checks_list_hands_the_section_to_its_rules(
        self, _push, auth_client, organization
    ):
        _make_config(organization, guardrails=SYNCED_GUARDRAILS)

        response = auth_client.post(
            URL,
            {"guardrails": {"checks": [], "rules": [PII_RULE_DISABLED]}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        assert stored == {
            "enabled": True,
            "checks": [],
            "rules": [PII_RULE_DISABLED],
        }
        # The push reads an empty checks list as absent and uses the rules.
        pushed = _transform_guardrails(stored)
        assert pushed["checks"]["pii-detection"]["enabled"] is False
        assert pushed["checks"]["pii-detection"]["confidence_threshold"] == 0.6

    # A client may send a stored checks map cleared key by key; the section
    # then goes to its rules.
    def test_a_checks_map_cleared_key_by_key_hands_the_section_to_its_rules(
        self, _push, auth_client, organization
    ):
        _make_config(
            organization,
            guardrails={
                "checks": {
                    "pii-detection": {
                        "enabled": True,
                        "_originalName": "pii-detector",
                        "_policy": PII_POLICY,
                    }
                }
            },
        )

        response = auth_client.post(
            URL,
            {
                "guardrails": {
                    "checks": {"pii-detection": None},
                    "rules": [PII_RULE_DISABLED],
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        stored = _active(organization).guardrails
        assert stored == {"checks": {}, "rules": [PII_RULE_DISABLED]}
        assert (
            _transform_guardrails(stored)["checks"]["pii-detection"]["enabled"] is False
        )

    def test_a_checks_map_patch_keeps_every_check_as_stored(
        self, _push, auth_client, organization
    ):
        # The console saves an edit of a section stored as a checks map as a
        # patch of that map: here, DynamoAI's policy ids. DynamoAI is stored
        # without an action or a threshold, data leakage without a threshold.
        # The push forwards the map as stored, so the gateway still gets
        # neither (it runs an absent action as log, an absent threshold as 0).
        dynamo = {
            "enabled": True,
            "config": {
                "provider": "dynamoai",
                "text_type": "MODEL_RESPONSE",
                "policy_ids": "p1",
            },
        }
        leakage = {"enabled": True, "action": "block"}
        _make_config(
            organization,
            guardrails={
                "checks": {"dynamoai-guard": dynamo, "data-leakage-prevention": leakage}
            },
        )

        response = auth_client.post(
            URL,
            {
                "guardrails": {
                    "checks": {"dynamoai-guard": {"config": {"policy_ids": "p1, p2"}}}
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        edited = {**dynamo, "config": {**dynamo["config"], "policy_ids": "p1, p2"}}
        stored = _active(organization).guardrails
        assert stored == {
            "checks": {"dynamoai-guard": edited, "data-leakage-prevention": leakage}
        }
        pushed = _transform_guardrails(stored)["checks"]
        assert pushed == {"dynamoai-guard": edited, "data-leakage-prevention": leakage}
        # What the push sends the gateway, through its contract.
        payload = _build_payload(
            str(organization.id), _active(organization), with_upstream_keys=False
        )
        assert payload["guardrails"]["checks"] == {
            "dynamoai-guard": edited,
            "data-leakage-prevention": leakage,
        }

    def test_a_checks_map_check_with_a_provider_of_its_own_is_refused(
        self, _push, auth_client, organization
    ):
        # The gateway contract takes no provider on a check itself, only in
        # its config, so the console moves one there before it saves a map.
        leakage = {"enabled": True, "action": "block"}
        _make_config(
            organization, guardrails={"checks": {"data-leakage-prevention": leakage}}
        )

        response = auth_client.post(
            URL,
            {
                "guardrails": {
                    "checks": {
                        "lakera-guard": {
                            "enabled": True,
                            "provider": "lakera",
                            "config": {"provider": "lakera"},
                        }
                    }
                }
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "lakera-guard.provider" in response.json()["message"]
        assert _active(organization).guardrails == {
            "checks": {"data-leakage-prevention": leakage}
        }

    def test_a_null_directly_under_a_section_deletes_the_key(
        self, _push, auth_client, organization
    ):
        _make_config(
            organization,
            routing={"strategy": "round_robin", "fallback_enabled": True},
            model_map={"fast": "gpt-4o-mini", "smart": "gpt-4o"},
            guardrails=SYNCED_GUARDRAILS,
            managed_models={"enabled": True},
        )

        response = auth_client.post(
            URL,
            {
                "routing": {"fallback_enabled": None},
                "model_map": {"smart": None},
                "guardrails": {"checks": None, "rules": [PII_RULE_DISABLED]},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        active = _active(organization)
        assert active.routing == {"strategy": "round_robin"}
        assert active.model_map == {"fast": "gpt-4o-mini"}
        assert active.guardrails == {"enabled": True, "rules": [PII_RULE_DISABLED]}
        # A section the patch leaves out is kept.
        assert active.managed_models == {"enabled": True}

    def test_a_whole_section_still_may_not_be_null(
        self, _push, auth_client, organization
    ):
        _make_config(organization, model_map={"fast": "gpt-4o-mini"})

        response = auth_client.post(URL, {"model_map": None}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert _active(organization).model_map == {"fast": "gpt-4o-mini"}
