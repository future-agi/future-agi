"""Follow-up refusal rows: provider upsert, budgets and MCP server removal.

PostgreSQL execution is owed. Push doubles run after real writes and inspect
those writes before refusing, so unchanged snapshots prove rollback. The real
placement and transport rows exercise the production push boundary offline.
"""

import copy
import json
from unittest.mock import patch

import pytest

from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.config_push import MCPCredentialPlacementRefused
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.org_config_writer import GATEWAY_SYNC_FAILED_MESSAGE
from agentcc.tests.test_gateway_push_refusal_atomic import (
    CODE,
    FIELD,
    GATEWAY,
    MCP,
    PUSH,
    RAW_REASON,
    _assert_refusal,
    _snapshot,
)
from integrations.services.credentials import CredentialManager

PROVIDER = "sweep-provider"
NEW_KEY = "test-sweep-replacement-not-real"
OLD_KEY = "test-sweep-original-not-real"
BUDGET = {"limit": 200, "alert_threshold": 80, "on_exceed": "warn"}
REQUESTS = {
    "update-provider": {
        "name": PROVIDER,
        "config": {
            "api_key": NEW_KEY,
            "base_url": "https://provider.example.com/v1",
            "display_name": "Updated provider",
            "models": ["sweep-model"],
        },
    },
    "set-budget": {"level": "org_limit", "config": BUDGET},
    "remove-budget": {"level": "org_limit"},
    "remove-mcp-server": {"server_id": "removable"},
}
CASES = [
    pytest.param(action, has_config, has_provider, id=f"{case}-{state}")
    for action, has_provider, case in (
        ("update-provider", True, "provider-update"),
        ("update-provider", False, "provider-create"),
        ("set-budget", True, "set-budget"),
        ("remove-budget", True, "remove-budget"),
        ("remove-mcp-server", True, "remove-mcp-server"),
    )
    for has_config, state in ((True, "existing-config"), (False, "first-config"))
    if has_config or action != "remove-mcp-server"
]
EXISTING_CASES = [case for case in CASES if case.values[1]]


@pytest.fixture(autouse=True)
def gateway_client():
    with patch("agentcc.services.config_push.get_gateway_client") as factory:
        yield factory.return_value


def _seed(user, has_config, has_provider):
    org = user.organization
    if has_provider:
        AgentccProviderCredential.no_workspace_objects.create(
            organization=org,
            provider_name=PROVIDER,
            encrypted_credentials=CredentialManager.encrypt({"api_key": OLD_KEY}),
            base_url="https://provider.example.com/v1",
        )
    if has_config:
        mcp = copy.deepcopy(MCP)
        mcp["servers"]["removable"] = {
            "url": "https://removable.example.com/mcp",
            "transport": "http",
        }
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp=mcp,
            budgets={"org_limit": {"limit": 100, "on_exceed": "warn"}},
            cost_tracking={},
            managed_models={"enabled": False},
        )
        # Removal revokes a real vault row inside the config writer too.
        # A personal row has no shared-credential eviction callback to run.
        AgentccMCPCredential.objects.create(
            organization=org,
            server_id="removable",
            scope=AgentccMCPCredential.Scope.USER,
            user=user,
            kind=AgentccMCPCredential.Kind.BEARER,
            encrypted_secret=encrypt_token("test-sweep-mcp-not-real"),
        )


def _database(org):
    return {
        **_snapshot(org),
        "mcp_credentials": list(
            AgentccMCPCredential.objects.filter(organization=org)
            .order_by("pk")
            .values()
        ),
    }


def _post(client, action):
    return client.post(
        f"{GATEWAY}/{action}/", copy.deepcopy(REQUESTS[action]), format="json"
    )


def _assert_mutation(org, config, action):
    stored = AgentccOrgConfig.all_objects.get(pk=config.pk)
    assert stored.is_active
    if action == "update-provider":
        provider = AgentccProviderCredential.all_objects.get(
            organization=org, provider_name=PROVIDER, deleted=False
        )
        assert CredentialManager.decrypt(bytes(provider.encrypted_credentials)) == {
            "api_key": NEW_KEY
        }
        assert provider.display_name == "Updated provider"
        assert provider.models_list == ["sweep-model"]
    elif action == "set-budget":
        assert stored.budgets["org_limit"] == BUDGET
    elif action == "remove-budget":
        assert "org_limit" not in stored.budgets
    else:
        assert "removable" not in stored.mcp["servers"]
        assert stored.mcp["servers"]["atomic"] == MCP["servers"]["atomic"]
        assert (
            AgentccMCPCredential.objects.get(
                organization=org, server_id="removable"
            ).status
            == AgentccMCPCredential.Status.REVOKED
        )


def _assert_saved(response, org, action, has_config, *, synced):
    assert response.status_code == 200, response.json()
    version = 2 if has_config and action != "update-provider" else 1
    expected = {"gateway_synced": synced}
    if action == "update-provider":
        expected.update(provider=PROVIDER, action="updated")
    elif action == "remove-mcp-server":
        expected.update(server="removable", action="removed", version=version)
    else:
        expected.update(
            budget="org_limit",
            action="set" if action == "set-budget" else "removed",
            version=version,
        )
    if not synced:
        expected["gateway_warning"] = GATEWAY_SYNC_FAILED_MESSAGE
    assert response.json()["result"] == expected
    configs = AgentccOrgConfig.all_objects.filter(organization=org)
    assert configs.count() == version
    config = configs.get(is_active=True, deleted=False)
    assert config.version == version
    if version == 2:
        assert not configs.get(version=1).is_active
    _assert_mutation(org, config, action)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action,has_config,has_provider", CASES)
class TestGatewaySweptPushOutcomes:
    def test_refusal_rolls_back_and_is_typed(
        self, auth_client, user, action, has_config, has_provider
    ):
        _seed(user, has_config, has_provider)
        org = user.organization
        before = _database(org)
        refusal = MCPCredentialPlacementRefused(FIELD, RAW_REASON)

        def refuse(org_id, config):
            assert org_id == str(org.pk)
            _assert_mutation(org, config, action)
            raise refusal

        with (
            patch(PUSH, side_effect=refuse) as push,
            patch("agentcc.views.gateway.log_config_rejected") as rejected,
        ):
            response = _post(auth_client, action)

        push.assert_called_once()
        assert _database(org) == before
        _assert_refusal(response)
        rejected.assert_called_once_with(
            f"{action.replace('-', '_')}_rejected", org, refusal
        )
        assert NEW_KEY not in json.dumps(response.json())
        assert response.json()["code"] == CODE

    def test_false_push_keeps_saved_change(
        self, auth_client, user, action, has_config, has_provider
    ):
        _seed(user, has_config, has_provider)
        with patch(PUSH, return_value=False) as push:
            response = _post(auth_client, action)
        push.assert_called_once()
        _assert_saved(response, user.organization, action, has_config, synced=False)

    def test_success_keeps_saved_change_and_response(
        self, auth_client, user, action, has_config, has_provider
    ):
        _seed(user, has_config, has_provider)
        with patch(PUSH, return_value=True) as push:
            response = _post(auth_client, action)
        push.assert_called_once()
        _assert_saved(response, user.organization, action, has_config, synced=True)

    def test_transport_failure_keeps_saved_change(
        self, auth_client, user, gateway_client, action, has_config, has_provider
    ):
        _seed(user, has_config, has_provider)
        gateway_client.set_org_config.side_effect = GatewayClientError(
            "offline fixture"
        )
        response = _post(auth_client, action)
        gateway_client.set_org_config.assert_called_once()
        _assert_saved(response, user.organization, action, has_config, synced=False)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action,has_config,has_provider", EXISTING_CASES)
def test_real_placement_refusal_rolls_back(
    auth_client, user, gateway_client, action, has_config, has_provider
):
    _seed(user, has_config, has_provider)
    before = _database(user.organization)
    assembled = {
        "atomic": {
            "type": "bearer",
            "in": "query",
            "name": "api_key",
            "value": "test-sweep-unplaceable-not-real",
        }
    }
    with patch(
        "agentcc.services.mcp_credentials.assemble_for_push", return_value=assembled
    ):
        response = _post(auth_client, action)
    assert _database(user.organization) == before
    _assert_refusal(response)
    assert "test-sweep-unplaceable-not-real" not in json.dumps(response.json())
    gateway_client.set_org_config.assert_not_called()
