"""A guardrail policy's check name is a non-empty string, refused like a check
config that is not an object.

A create or update of a policy whose check name is not a non-empty string
answers 400 naming checks[i].name; nothing is saved or pushed, and later saves
go ahead.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
SET_BUDGET = f"{GATEWAY}/set-budget/"
POLICIES = "/agentcc/guardrail-policies/"

# Check names the gateway can never run; a policy save refuses each one.
MALFORMED_NAMES = {
    "number": 123,
    "list": ["x"],
    "object": {"a": 1},
    "null": None,
    "bool": True,
    "empty": "",
    "blank": "   ",
}

BLOCKLIST = {
    "name": "keyword-blocklist",
    "enabled": True,
    "action": "block",
    "config": {"words": ["blocked-word"]},
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


def _active_org(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True
    )


def _assert_name_refused(response, index):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert f"checks[{index}].name" in json.dumps(response.json())


@pytest.mark.integration
@pytest.mark.api
class TestPolicyCheckNameMustBeAString:
    @pytest.mark.parametrize("kind", sorted(MALFORMED_NAMES))
    def test_create_refuses_the_name_and_later_saves_go_ahead(
        self, auth_client, organization, gateway, kind
    ):
        """The policy save is refused, nothing is pushed,
        and a budget save and a guardrails save both answer 200 after it."""
        active = _active_org(organization)
        response = auth_client.post(
            POLICIES,
            {
                "name": "p-bad-name",
                "checks": [
                    {"name": MALFORMED_NAMES[kind], "enabled": True, "config": {}}
                ],
            },
            format="json",
        )
        _assert_name_refused(response, 0)
        assert not AgentccGuardrailPolicy.no_workspace_objects.filter(
            organization=organization, name="p-bad-name"
        ).exists()
        active.refresh_from_db()
        assert not active.guardrails
        gateway.set_org_config.assert_not_called()

        budget = auth_client.post(
            SET_BUDGET,
            {"level": "org_limit", "config": {"limit": 100, "on_exceed": "warn"}},
            format="json",
        )
        assert budget.status_code == status.HTTP_200_OK, budget.json()
        assert budget.json()["result"]["gateway_synced"] is True

        repair = auth_client.post(
            UPDATE_CONFIG,
            {"guardrails": {"rules": [{**BLOCKLIST, "config": {"words": ["y"]}}]}},
            format="json",
        )
        assert repair.status_code == status.HTTP_200_OK, repair.json()
        assert repair.json()["result"]["gateway_synced"] is True

    @pytest.mark.parametrize("kind", sorted(MALFORMED_NAMES))
    def test_patch_refuses_the_name_and_keeps_the_policy(
        self, auth_client, organization, gateway, kind
    ):
        _active_org(organization)
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization, name="p-patch-name", checks=[BLOCKLIST]
        )
        response = auth_client.patch(
            f"{POLICIES}{policy.id}/",
            {"checks": [BLOCKLIST, {"name": MALFORMED_NAMES[kind], "config": {}}]},
            format="json",
        )
        _assert_name_refused(response, 1)
        policy.refresh_from_db()
        assert policy.checks == [BLOCKLIST]
        gateway.set_org_config.assert_not_called()

    def test_a_check_named_by_a_string_saves_and_is_pushed(
        self, auth_client, organization, gateway
    ):
        _active_org(organization)
        response = auth_client.post(
            POLICIES, {"name": "p-good", "checks": [BLOCKLIST]}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True
        pushed = gateway.set_org_config.call_args.args[1]["guardrails"]
        assert pushed["checks"]["keyword-blocklist"]["config"]["words"] == [
            "blocked-word"
        ]

    def test_the_refusal_has_its_own_code(self, organization):
        from agentcc.serializers.guardrail_policy import (
            AgentccGuardrailPolicySerializer,
        )

        serializer = AgentccGuardrailPolicySerializer(
            data={"name": "p-code", "checks": [{"name": 7}]}
        )
        assert not serializer.is_valid()
        (error,) = serializer.errors["checks"]
        assert error.code == "guardrail_check_name_invalid"
        assert str(error) == "checks[0].name must be a non-empty string"
