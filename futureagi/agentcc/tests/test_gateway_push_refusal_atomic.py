"""A refused push rolls back; a failed sync keeps the requested change.

PostgreSQL rows are required. Patch the push boundary, not the transaction
helpers, so the assertions see real version writes and provider soft-deletes.
The real-placement rows also exercise push_org_config's refusal propagation.
"""

import copy
import json
from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.services.config_push import MCPCredentialPlacementRefused
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.org_config_writer import GATEWAY_SYNC_FAILED_MESSAGE
from integrations.services.credentials import CredentialManager

GATEWAY = "/agentcc/gateways/default"
PUSH = "agentcc.views.gateway.push_org_config"
FIELD = "mcp.servers.atomic.credential.secret.type"
CODE = "mcp_credential_placement_unsupported"
RAW_REASON = "internal-placement-refusal-detail-not-for-the-response"
REFUSAL_MESSAGE = (
    f"The MCP credential configuration at {FIELD} cannot be applied. No changes were "
    "saved. Correct the credential, the server URL or the catalog reference, then "
    "retry."
)
GUARDRAILS = {"enabled": True, "blocked_tools": ["shell"]}
MCP = {
    "servers": {"atomic": {"url": "https://mcp.example.com/mcp", "transport": "http"}}
}
REQUESTS = {
    "reload": {},
    "remove-provider": {"name": "atomic-provider"},
    "update-mcp-guardrails": {"config": GUARDRAILS},
}
CASES = [
    pytest.param(action, has_config, id=f"{action}-{state}")
    for action in REQUESTS
    for has_config, state in ((True, "existing-config"), (False, "first-config"))
]


@pytest.fixture(autouse=True)
def gateway_client():
    # No real gateway I/O, even in the real-placement rows.
    with patch("agentcc.services.config_push.get_gateway_client") as factory:
        yield factory.return_value


def _seed(org, has_config):
    provider = AgentccProviderCredential.no_workspace_objects.create(
        organization=org,
        provider_name="atomic-provider",
        encrypted_credentials=CredentialManager.encrypt(
            {"api_key": "test-atomic-provider-not-real"}
        ),
        base_url="https://provider.example.com/v1",
    )
    if has_config:
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp=copy.deepcopy(MCP),
            # Reload normalizes this in-place: that write must roll back too.
            cost_tracking={},
            managed_models={"enabled": False},
        )
    return provider


def _snapshot(org):
    return {
        "providers": list(
            AgentccProviderCredential.all_objects.filter(organization=org)
            .order_by("pk")
            .values()
        ),
        "configs": list(
            AgentccOrgConfig.all_objects.filter(organization=org)
            .order_by("version")
            .values()
        ),
    }


def _post(client, action):
    return client.post(
        f"{GATEWAY}/{action}/", copy.deepcopy(REQUESTS[action]), format="json"
    )


def _assert_refusal(response):
    body = response.json()
    assert response.status_code == 400, body
    assert body["code"] == CODE
    assert body["param"] == FIELD
    assert body["message"] == REFUSAL_MESSAGE
    assert RAW_REASON not in json.dumps(body)
    assert "gateway_synced" not in (body.get("result") or {})


def _assert_saved(response, org, provider, action, has_config, *, synced):
    assert response.status_code == 200, response.json()
    result = response.json()["result"]
    expected = {"gateway_synced": synced}
    version = 2 if has_config and action == "update-mcp-guardrails" else 1
    if action == "reload":
        expected["status"] = "ok"
    elif action == "remove-provider":
        expected.update(provider=provider.provider_name, action="removed")
    else:
        expected.update(action="updated", version=version)
    if not synced:
        expected["gateway_warning"] = GATEWAY_SYNC_FAILED_MESSAGE
    assert result == expected

    stored = AgentccProviderCredential.all_objects.get(pk=provider.pk)
    assert stored.deleted is (action == "remove-provider")
    assert (stored.deleted_at is not None) is (action == "remove-provider")
    configs = AgentccOrgConfig.all_objects.filter(organization=org)
    assert configs.count() == version
    active = configs.get(is_active=True, deleted=False)
    assert active.version == version
    if action == "update-mcp-guardrails":
        assert active.mcp["guardrails"] == GUARDRAILS
    elif has_config:
        assert active.mcp == MCP
    if version == 2:
        assert not configs.get(version=1).is_active


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action,has_config", CASES)
class TestGatewayPushOutcomes:
    def test_refusal_keeps_database_unchanged(
        self, auth_client, user, action, has_config
    ):
        org = user.organization
        provider = _seed(org, has_config)
        before = _snapshot(org)

        def refuse(org_id, config):
            assert org_id == str(org.pk)
            assert AgentccOrgConfig.all_objects.filter(pk=config.pk).exists()
            if action == "remove-provider":
                # The failure is deliberately AFTER the mutation, not before it.
                assert AgentccProviderCredential.all_objects.get(pk=provider.pk).deleted
            if action == "update-mcp-guardrails":
                assert config.mcp["guardrails"] == GUARDRAILS
            raise MCPCredentialPlacementRefused(FIELD, RAW_REASON)

        with (
            patch(PUSH, side_effect=refuse) as push,
            patch("agentcc.views.gateway.log_config_rejected") as rejected,
        ):
            response = _post(auth_client, action)

        push.assert_called_once()
        # Includes deleted_at, active flags, normalized fields and all versions.
        assert _snapshot(org) == before
        _assert_refusal(response)
        rejected.assert_called_once()
        event, logged_org, error = rejected.call_args.args
        assert event == f"{action.replace('-', '_')}_rejected"
        assert logged_org == org
        assert error.code == CODE
        assert error.param == FIELD

    def test_sync_failure_keeps_saved_change(
        self, auth_client, user, action, has_config
    ):
        org = user.organization
        provider = _seed(org, has_config)
        with patch(PUSH, return_value=False) as push:
            response = _post(auth_client, action)

        push.assert_called_once()
        _assert_saved(response, org, provider, action, has_config, synced=False)

    def test_success_keeps_response_and_saved_change(
        self, auth_client, user, action, has_config
    ):
        org = user.organization
        provider = _seed(org, has_config)
        with patch(PUSH, return_value=True) as push:
            response = _post(auth_client, action)

        push.assert_called_once()
        _assert_saved(response, org, provider, action, has_config, synced=True)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", REQUESTS)
def test_real_placement_refusal_is_atomic(auth_client, user, gateway_client, action):
    org = user.organization
    _seed(org, True)
    before = _snapshot(org)
    assembled = {
        "atomic": {
            "type": "bearer",
            "in": "query",
            "name": "api_key",
            "value": "test-atomic-mcp-not-real",
        }
    }
    with patch(
        "agentcc.services.mcp_credentials.assemble_for_push", return_value=assembled
    ):
        response = _post(auth_client, action)

    assert _snapshot(org) == before
    _assert_refusal(response)
    assert "test-atomic-mcp-not-real" not in json.dumps(response.json())
    gateway_client.set_org_config.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("action", REQUESTS)
def test_gateway_transport_failure_keeps_saved_change(
    auth_client, user, gateway_client, action
):
    org = user.organization
    provider = _seed(org, True)
    gateway_client.set_org_config.side_effect = GatewayClientError("offline fixture")

    response = _post(auth_client, action)

    gateway_client.set_org_config.assert_called_once()
    _assert_saved(response, org, provider, action, True, synced=False)
