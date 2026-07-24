"""
Agentcc API Tests

Tests for Agentcc gateway, API key, request log, and webhook endpoints.
Includes Phase 5.2 tests for advanced filters, search, sessions, and export.
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from agentcc.models import (
    AgentccAPIKey,
    AgentccGuardrailPolicy,
    AgentccOrgConfig,
    AgentccProject,
    AgentccProviderCredential,
    AgentccRequestLog,
)
from integrations.services.credentials import CredentialManager


@pytest.fixture
def gateway_id():
    """Return the virtual gateway ID used by the ViewSet."""
    return "default"


AGENTCC_TEST_ADMIN_TOKEN = "agentcc-admin-secret"


@pytest.mark.integration
@pytest.mark.api
class TestAgentccGatewayAPI:
    """Tests for /agentcc/gateways/ endpoints."""

    def test_list_gateways_authenticated(self, auth_client):
        response = auth_client.get("/agentcc/gateways/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert isinstance(data["result"], list)

    def test_list_gateways_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/gateways/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_gateway(self, auth_client):
        """Gateway creation is disabled — virtual singleton only.

        The ViewSet does not implement ``create`` so DRF returns 405.
        """
        response = auth_client.post(
            "/agentcc/gateways/",
            {
                "name": "new-gateway",
                "base_url": "http://localhost:9090",
                "admin_token": "my-token",
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_create_gateway_missing_name(self, auth_client):
        response = auth_client.post(
            "/agentcc/gateways/",
            {"base_url": "http://localhost:9090"},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_retrieve_gateway(self, auth_client, gateway_id):
        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        # Virtual singleton gateway is always named "Agent Command Center Gateway".
        assert data["result"]["name"] == "Agent Command Center Gateway"

    def test_update_gateway(self, auth_client, gateway_id):
        """Gateway updates are disabled — virtual singleton only."""
        response = auth_client.put(
            f"/agentcc/gateways/{gateway_id}/",
            {"name": "updated-gw", "base_url": "http://localhost:9090"},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_delete_gateway(self, auth_client, gateway_id):
        """Gateway deletion is disabled — virtual singleton only."""
        response = auth_client.delete(f"/agentcc/gateways/{gateway_id}/")
        assert response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_health_check_success(self, mock_get_client, auth_client, gateway_id):
        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "ok"}
        mock_client.provider_health.return_value = {
            "providers": {
                "openai": {"status": "healthy", "models": ["gpt-4", "gpt-3.5-turbo"]},
            }
        }
        mock_get_client.return_value = mock_client

        response = auth_client.post(f"/agentcc/gateways/{gateway_id}/health_check/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        # The gateway view sources providers from AgentccProviderCredential
        # (the local DB), not from the gateway client — no credentials are
        # provisioned in the test fixture, so counts are zero.
        assert data["result"]["status"] == "healthy"
        assert data["result"]["provider_count"] == 0
        assert data["result"]["model_count"] == 0

    def test_health_check_rejects_body_fields(self, auth_client, gateway_id):
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/health_check/",
            {"unexpected": True},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["unexpected"] == ["Unknown field."]

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_health_check_unreachable(self, mock_get_client, auth_client, gateway_id):
        from agentcc.services.gateway_client import GatewayClientError

        mock_client = MagicMock()
        mock_client.health_check.side_effect = GatewayClientError("Connection refused")
        mock_get_client.return_value = mock_client

        response = auth_client.post(f"/agentcc/gateways/{gateway_id}/health_check/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["details"]["status"] == ["unreachable"]
        assert "Connection refused" in data["detail"]

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_get_config(self, mock_get_client, auth_client, gateway_id):
        mock_client = MagicMock()
        mock_client.get_config.return_value = {"providers": ["openai"]}
        mock_get_client.return_value = mock_client

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/config/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # View returns providers as a dict keyed by provider name, sourced
        # from AgentccProviderCredential (empty in this test).
        assert isinstance(data["result"]["providers"], dict)
        assert "gateway" in data["result"]

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_get_providers(self, mock_get_client, auth_client, gateway_id):
        mock_client = MagicMock()
        mock_client.provider_health.return_value = {
            "providers": {"openai": {"status": "healthy"}}
        }
        mock_get_client.return_value = mock_client

        response = auth_client.get(f"/agentcc/gateways/{gateway_id}/providers/")
        assert response.status_code == status.HTTP_200_OK

    @patch("agentcc.views.gateway.push_org_config", return_value=False)
    def test_gateway_provider_action_lifecycle(
        self, mock_push_config, auth_client, gateway_id, organization
    ):
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-provider/",
            {
                "name": "api_gateway_action_provider",
                "config": {
                    "api_key": "sk-gateway-action-secret",
                    "display_name": "Gateway Action Provider",
                    "api_format": "openai",
                    "models": ["gpt-4o-mini"],
                    "default_timeout": 17,
                    "max_concurrent": 3,
                    "conn_pool_size": 5,
                    "base_url": "https://api.example.com/v1",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["provider"] == "api_gateway_action_provider"
        assert result["action"] == "updated"
        assert result["gateway_synced"] is False

        credential = AgentccProviderCredential.no_workspace_objects.get(
            organization=organization,
            provider_name="api_gateway_action_provider",
            deleted=False,
        )
        assert credential.display_name == "Gateway Action Provider"
        assert credential.models_list == ["gpt-4o-mini"]
        assert credential.default_timeout_seconds == 17
        assert CredentialManager.decrypt(credential.encrypted_credentials) == {
            "api_key": "sk-gateway-action-secret"
        }

        remove = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-provider/",
            {"name": "api_gateway_action_provider"},
            format="json",
        )

        assert remove.status_code == status.HTTP_200_OK, remove.json()
        removed = remove.json()["result"]
        assert removed["provider"] == "api_gateway_action_provider"
        assert removed["action"] == "removed"
        credential.refresh_from_db()
        assert credential.deleted is True
        assert credential.deleted_at is not None
        assert mock_push_config.call_count == 2

    @patch("agentcc.views.gateway.push_org_config", return_value=False)
    def test_gateway_guardrail_actions_create_versioned_config(
        self, mock_push_config, auth_client, gateway_id, organization
    ):
        before_count = AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization,
            deleted=False,
        ).count()

        update = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-guardrail/",
            {
                "name": "api_gateway_action_guardrail",
                "config": {
                    "enabled": True,
                    "action": "flag",
                    "stage": "pre",
                    "threshold": 0.42,
                    "mode": "sync",
                    "config": {"source": "api-test"},
                },
            },
            format="json",
        )

        assert update.status_code == status.HTTP_200_OK, update.json()
        update_result = update.json()["result"]
        assert update_result["guardrail"] == "api_gateway_action_guardrail"
        assert update_result["action"] == "updated"
        assert update_result["gateway_synced"] is False

        toggle = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/toggle-guardrail/",
            {"name": "api_gateway_action_guardrail", "enabled": False},
            format="json",
        )

        assert toggle.status_code == status.HTTP_200_OK, toggle.json()
        toggle_result = toggle.json()["result"]
        assert toggle_result["guardrail"] == "api_gateway_action_guardrail"
        assert toggle_result["enabled"] is False

        active_config = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization,
            is_active=True,
            deleted=False,
        )
        rules = active_config.guardrails["rules"]
        rule = next(
            item for item in rules if item["name"] == "api_gateway_action_guardrail"
        )
        assert rule["enabled"] is False
        assert rule["threshold"] == 0.42
        assert (
            AgentccOrgConfig.no_workspace_objects.filter(
                organization=organization,
                deleted=False,
            ).count()
            == before_count + 2
        )
        assert mock_push_config.call_count == 2

    @patch("agentcc.views.gateway.get_gateway_client")
    def test_gateway_batch_and_mcp_guard_paths_do_not_dispatch_unowned_work(
        self, mock_get_client, auth_client, gateway_id
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        empty_submit = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/submit-batch/",
            {"requests": []},
            format="json",
        )
        assert empty_submit.status_code == status.HTTP_400_BAD_REQUEST
        mock_client.submit_batch.assert_not_called()

        missing_batch = auth_client.get(f"/agentcc/gateways/{gateway_id}/get-batch/")
        assert missing_batch.status_code == status.HTTP_400_BAD_REQUEST
        mock_client.get_batch.assert_not_called()

        unknown_batch = auth_client.get(
            f"/agentcc/gateways/{gateway_id}/get-batch/?batch_id=unknown-batch"
        )
        assert unknown_batch.status_code == status.HTTP_404_NOT_FOUND
        mock_client.get_batch.assert_not_called()

        unknown_cancel = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/cancel-batch/",
            {"batch_id": "unknown-batch"},
            format="json",
        )
        assert unknown_cancel.status_code == status.HTTP_404_NOT_FOUND
        mock_client.cancel_batch.assert_not_called()

        no_mcp = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/test-mcp-tool/",
            {"name": "echo", "arguments": {"message": "hello"}},
            format="json",
        )
        assert no_mcp.status_code == status.HTTP_400_BAD_REQUEST
        mock_client.mcp_test_tool.assert_not_called()

    def test_retrieve_gateway_unauthenticated(self, api_client, gateway_id):
        response = api_client.get(f"/agentcc/gateways/{gateway_id}/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_providers_unauthenticated(self, api_client, gateway_id):
        response = api_client.get(f"/agentcc/gateways/{gateway_id}/providers/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_providers_when_gateway_client_unreachable(
        self, auth_client, gateway_id
    ):
        from agentcc.services.gateway_client import GatewayClientError

        with patch("agentcc.views.gateway.get_gateway_client") as mock_get:
            mock_client = MagicMock()
            mock_client.list_providers.side_effect = GatewayClientError("down")
            mock_get.return_value = mock_client

            response = auth_client.get(
                f"/agentcc/gateways/{gateway_id}/providers/"
            )
        # The endpoint handles unreachable upstream gracefully; either falls
        # back to the DB-only view (200) or returns a bad_request (400).
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        )

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_reload_happy_path_pushes_current_config(
        self, mock_push, auth_client, gateway_id, organization
    ):
        # Seed an active org config so _push_current_config has something to push.
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(f"/agentcc/gateways/{gateway_id}/reload/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["gateway_synced"] is True

    def test_reload_unauthenticated(self, api_client, gateway_id):
        response = api_client.post(f"/agentcc/gateways/{gateway_id}/reload/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @patch("agentcc.views.gateway.push_org_config", return_value=False)
    def test_reload_when_gateway_unreachable_reports_warning(
        self, mock_push, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )
        response = auth_client.post(f"/agentcc/gateways/{gateway_id}/reload/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["gateway_synced"] is False
        assert "gateway_warning" in response.json()["result"]

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_provider_standalone_creates_credential(
        self, mock_push, auth_client, gateway_id, organization
    ):
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-provider/",
            {
                "name": "standalone-openai",
                "config": {
                    "api_key": "sk-standalone",
                    "display_name": "Standalone OpenAI",
                    "api_format": "openai",
                    "models": ["gpt-4o"],
                    "default_timeout": 20,
                    "max_concurrent": 4,
                    "conn_pool_size": 6,
                    "base_url": "https://api.openai.com/v1",
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert AgentccProviderCredential.no_workspace_objects.filter(
            organization=organization,
            provider_name="standalone-openai",
            deleted=False,
        ).exists()

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_remove_provider_standalone_soft_deletes(
        self, mock_push, auth_client, gateway_id, organization
    ):
        cred = AgentccProviderCredential.no_workspace_objects.create(
            organization=organization,
            provider_name="to-remove",
            display_name="To Remove",
            encrypted_credentials=b"placeholder",
            api_format="openai",
        )
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-provider/",
            {"name": "to-remove"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        cred.refresh_from_db()
        assert cred.deleted is True

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_update_config_patches_active_row_and_bumps_version(
        self, mock_push, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            routing={"strategy": "round_robin"},
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-config/",
            {"cache": {"enabled": True, "default_ttl": 60}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["version"] == 2  # bump
        assert result["gateway_synced"] is True
        mock_push.assert_called_once()

        new_active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        # Patched field applied, untouched field preserved.
        assert new_active.cache == {"enabled": True, "default_ttl": 60}
        assert new_active.routing == {"strategy": "round_robin"}
        assert new_active.version == 2

    def test_update_config_rejects_unknown_field(
        self, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/update-config/",
            {"not_a_real_field": {"x": 1}},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_set_budget_writes_level_and_bumps_version(
        self, mock_push, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True, budgets={}
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/set-budget/",
            {
                "level": "organization",
                "config": {"limit_usd": 100, "action_mode": "hard"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["budget"] == "organization"
        assert result["action"] == "set"
        assert result["gateway_synced"] is True

        new_active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        # Budget should be present under the "organization" level in some form.
        assert new_active.budgets  # not empty
        assert new_active.version == 2

    def test_set_budget_rejects_missing_fields(
        self, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization, version=1, is_active=True
        )

        # Empty body: reject_unknown_fields=True, so validator rejects.
        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/set-budget/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("agentcc.views.gateway.push_org_config", return_value=True)
    def test_remove_budget_strips_level_and_bumps_version(
        self, mock_push, auth_client, gateway_id, organization
    ):
        AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=1,
            is_active=True,
            budgets={
                "organization": {"limit_usd": 100, "action_mode": "hard"},
                "user": {"limit_usd": 20, "action_mode": "warn"},
            },
        )

        response = auth_client.post(
            f"/agentcc/gateways/{gateway_id}/remove-budget/",
            {"level": "organization"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["budget"] == "organization"
        assert result["action"] == "removed"

        new_active = AgentccOrgConfig.no_workspace_objects.get(
            organization=organization, is_active=True, deleted=False
        )
        assert "organization" not in new_active.budgets
        assert "user" in new_active.budgets  # other levels preserved
        assert new_active.version == 2


@pytest.mark.integration
@pytest.mark.api
class TestAgentccGuardrailPolicyAPI:
    """Tests for /agentcc/guardrail-policies/ generated endpoints."""

    @patch("agentcc.views.guardrail_policy.sync_guardrail_policies", return_value=False)
    def test_guardrail_policy_put_preserves_encrypted_check_secret(
        self, mock_sync, auth_client, organization
    ):
        raw_secret = "sk-guardrail-policy-put-secret"
        check_name = "contract_guardrail_check"
        name = f"contract_guardrail_put_{str(organization.id)[:8]}"

        created = auth_client.post(
            "/agentcc/guardrail-policies/",
            {
                "name": name,
                "description": "Create through generated policy contract test.",
                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                "mode": AgentccGuardrailPolicy.MODE_MONITOR,
                "is_active": False,
                "priority": 501,
                "checks": [
                    {
                        "name": check_name,
                        "type": "regex",
                        "enabled": True,
                        "config": {
                            "pattern": "contract-create",
                            "api_key": raw_secret,
                        },
                    }
                ],
            },
            format="json",
        )

        assert created.status_code == status.HTTP_200_OK, created.json()
        created_result = created.json()["result"]
        assert raw_secret not in json.dumps(created_result)
        assert created_result["checks"][0]["config"]["api_key"] == "__encrypted__"

        policy = AgentccGuardrailPolicy.no_workspace_objects.get(
            id=created_result["id"], organization=organization
        )
        assert policy.checks[0]["config"]["api_key"] == "__encrypted__"
        assert CredentialManager.decrypt(policy.encrypted_check_configs) == {
            check_name: {"api_key": raw_secret}
        }

        updated = auth_client.put(
            f"/agentcc/guardrail-policies/{policy.id}/",
            {
                "name": name,
                "description": "Full PUT update preserves encrypted secret.",
                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                "mode": AgentccGuardrailPolicy.MODE_ENFORCE,
                "is_active": True,
                "priority": 502,
                "applied_keys": [],
                "applied_projects": [],
                "checks": [
                    {
                        "name": check_name,
                        "type": "regex",
                        "enabled": True,
                        "config": {
                            "pattern": "contract-put",
                            "api_key": "__encrypted__",
                        },
                    }
                ],
            },
            format="json",
        )

        assert updated.status_code == status.HTTP_200_OK, updated.json()
        updated_result = updated.json()["result"]
        assert updated_result["mode"] == AgentccGuardrailPolicy.MODE_ENFORCE
        assert updated_result["priority"] == 502
        assert raw_secret not in json.dumps(updated_result)
        assert updated_result["checks"][0]["config"]["api_key"] == "__encrypted__"
        assert updated_result["gateway_synced"] is False

        policy.refresh_from_db()
        assert policy.checks[0]["config"]["pattern"] == "contract-put"
        assert CredentialManager.decrypt(policy.encrypted_check_configs) == {
            check_name: {"api_key": raw_secret}
        }
        assert mock_sync.call_count == 2

    @patch("agentcc.views.guardrail_policy.sync_guardrail_policies", return_value=False)
    def test_guardrail_policy_apply_targets_scoped_api_keys(
        self, mock_sync, auth_client, organization, workspace
    ):
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name=f"contract_guardrail_apply_{str(organization.id)[:8]}",
            description="Apply route contract test.",
            scope=AgentccGuardrailPolicy.SCOPE_GLOBAL,
            mode=AgentccGuardrailPolicy.MODE_MONITOR,
            is_active=True,
            priority=503,
            checks=[],
        )
        api_key = AgentccAPIKey.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            gateway_key_id=f"contract-guardrail-apply-{policy.id}",
            key_prefix="pk-apply",
            key_hash="hash",
            name="contract guardrail apply key",
            owner="api-test",
            status=AgentccAPIKey.ACTIVE,
            allowed_models=[],
            allowed_providers=[],
            metadata={},
        )

        response = auth_client.post(
            f"/agentcc/guardrail-policies/{policy.id}/apply/",
            {"key_ids": [str(api_key.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        assert result["scope"] == AgentccGuardrailPolicy.SCOPE_KEY
        assert result["applied_keys"] == [str(api_key.id)]
        assert result["gateway_synced"] is False
        assert "gateway_warning" in result

        policy.refresh_from_db()
        assert policy.scope == AgentccGuardrailPolicy.SCOPE_KEY
        assert policy.applied_keys == [str(api_key.id)]
        assert mock_sync.call_count == 1


@pytest.mark.integration
@pytest.mark.api
class TestAgentccAPIKeyAPI:
    """Tests for /agentcc/api-keys/ endpoints."""

    def test_list_api_keys_authenticated(self, auth_client):
        response = auth_client.get("/agentcc/api-keys/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True

    def test_list_api_keys_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/api-keys/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    @patch("agentcc.views.api_key.auth_bridge")
    def test_create_api_key(
        self, mock_bridge, auth_client, gateway_id, organization, workspace
    ):
        now = timezone.now()
        # Build a real (unsaved) AgentccAPIKey instance rather than a MagicMock.
        # DRF's PrimaryKeyRelatedField.get_attribute() calls
        # ``instance.serializable_value(source)`` which on a MagicMock returns
        # another MagicMock. The resulting dict then flows into
        # rest_framework.utils.encoders.JSONEncoder.default(), which sees
        # ``hasattr(obj, 'tolist') == True`` on any MagicMock and calls
        # ``obj.tolist()`` → another MagicMock → infinite recursion (hang).
        mock_key = AgentccAPIKey(
            gateway_key_id="gw-key-new",
            key_prefix="pk-new",
            name="new-key",
            owner="",
            status="active",
            allowed_models=[],
            allowed_providers=[],
            metadata={},
            organization=organization,
            workspace=workspace,
        )
        mock_key.created_at = now
        mock_key.updated_at = now

        mock_bridge.provision_key.return_value = (mock_key, "pk-new-full-key-here")

        response = auth_client.post(
            "/agentcc/api-keys/",
            {
                "gateway_id": str(gateway_id),
                "name": "new-key",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["status"] is True
        assert "key" in data["result"]  # Raw key only on creation

    def test_create_api_key_missing_gateway(self, auth_client):
        response = auth_client.post(
            "/agentcc/api-keys/",
            {"name": "test-key"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @patch("agentcc.views.api_key.auth_bridge")
    def test_create_api_key_uses_active_workspace_project(
        self,
        mock_bridge,
        auth_client,
        organization,
        workspace,
        user,
    ):
        project = AgentccProject.no_workspace_objects.create(
            name="api-key-active-project",
            organization=organization,
            workspace=workspace,
        )
        api_key = AgentccAPIKey(
            gateway_key_id="gw-active-project",
            key_prefix="pk-active",
            name="project-key",
            owner="",
            status=AgentccAPIKey.ACTIVE,
            allowed_models=[],
            allowed_providers=[],
            metadata={},
            organization=organization,
            workspace=None,
            project=project,
            user=user,
        )
        now = timezone.now()
        api_key.created_at = now
        api_key.updated_at = now
        mock_bridge.provision_key.return_value = (api_key, "pk-active-full-key")

        response = auth_client.post(
            "/agentcc/api-keys/",
            {
                "name": "project-key",
                "project_id": str(project.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        mock_bridge.provision_key.assert_called_once()
        assert mock_bridge.provision_key.call_args.kwargs["project"] == project

    @patch("agentcc.views.api_key.auth_bridge")
    def test_create_api_key_rejects_same_org_other_workspace_project(
        self,
        mock_bridge,
        auth_client,
        organization,
        workspace,
        user,
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="AgentCC API key hidden workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_project = AgentccProject.no_workspace_objects.create(
            name="api-key-hidden-project",
            organization=organization,
            workspace=other_workspace,
        )

        response = auth_client.post(
            "/agentcc/api-keys/",
            {
                "name": "hidden-project-key",
                "project_id": str(hidden_project.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND, response.json()
        mock_bridge.provision_key.assert_not_called()

    def test_list_api_keys_filter_by_gateway(
        self, auth_client, gateway_id, organization, workspace
    ):
        AgentccAPIKey.objects.create(
            gateway_key_id="gw-k1",
            name="key1",
            organization=organization,
            workspace=workspace,
        )
        response = auth_client.get(f"/agentcc/api-keys/?gateway_id={gateway_id}")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["result"]) >= 1

    def test_revoke_api_key(self, auth_client, gateway_id, organization, workspace):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-revoke",
            name="to-revoke",
            organization=organization,
            workspace=workspace,
        )
        with patch("agentcc.views.api_key.auth_bridge") as mock_bridge:
            mock_bridge.revoke_key.return_value = (key, False)
            response = auth_client.post(f"/agentcc/api-keys/{key.id}/revoke/")
        assert response.status_code == status.HTTP_200_OK

    @patch("agentcc.views.api_key.auth_bridge")
    def test_put_api_key_uses_gateway_update_bridge(
        self, mock_bridge, auth_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-put",
            key_prefix="pk-put",
            name="put-original",
            owner="before",
            organization=organization,
            workspace=workspace,
            allowed_models=["gpt-4o"],
            metadata={"before": True},
        )

        def update_key(api_key, **kwargs):
            for field, value in kwargs.items():
                setattr(api_key, field, value)
            api_key.save(
                update_fields=[*kwargs.keys(), "updated_at"],
            )
            return api_key

        mock_bridge.update_key.side_effect = update_key

        response = auth_client.put(
            f"/agentcc/api-keys/{key.id}/",
            {
                "name": "put-updated",
                "owner": "after",
                "allowed_models": ["gpt-4o-mini"],
                "metadata": {"updated": True},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        mock_bridge.update_key.assert_called_once()
        assert mock_bridge.update_key.call_args.args[0] == key
        assert mock_bridge.update_key.call_args.kwargs == {
            "name": "put-updated",
            "owner": "after",
            "allowed_models": ["gpt-4o-mini"],
            "metadata": {"updated": True},
        }
        key.refresh_from_db()
        assert key.name == "put-updated"
        assert key.owner == "after"
        assert key.allowed_models == ["gpt-4o-mini"]
        assert key.metadata == {"updated": True}
        assert key.status == AgentccAPIKey.ACTIVE

        bad_response = auth_client.put(
            f"/agentcc/api-keys/{key.id}/",
            {"status": AgentccAPIKey.REVOKED},
            format="json",
        )
        assert bad_response.status_code == status.HTTP_400_BAD_REQUEST
        assert mock_bridge.update_key.call_count == 1
        key.refresh_from_db()
        assert key.status == AgentccAPIKey.ACTIVE

    def test_retrieve_api_key_returns_key_metadata(
        self, auth_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-retrieve",
            key_prefix="pk-retrieve",
            name="retrieve-me",
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.get(f"/agentcc/api-keys/{key.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()["result"]
        assert data["id"] == str(key.id)
        assert data["name"] == "retrieve-me"
        assert data["gateway_key_id"] == "gw-retrieve"
        # Raw key is never rehydrated on retrieve; only the prefix survives.
        assert "key" not in data
        assert data["key_prefix"] == "pk-retrieve"

    def test_retrieve_api_key_unauthenticated(self, api_client, organization, workspace):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-retrieve-unauth",
            name="unauth",
            organization=organization,
            workspace=workspace,
        )

        response = api_client.get(f"/agentcc/api-keys/{key.id}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_destroy_api_key_removes_row(
        self, auth_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-destroy",
            name="destroy-me",
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.delete(f"/agentcc/api-keys/{key.id}/")

        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        )
        # DELETE either soft-deletes (deleted=True) or hard-deletes; either way
        # the key must not appear on a subsequent list call.
        list_response = auth_client.get("/agentcc/api-keys/")
        ids = {item["id"] for item in list_response.json()["result"]}
        assert str(key.id) not in ids

    @patch("agentcc.views.api_key.auth_bridge")
    def test_sync_api_keys_calls_bridge_sync_keys(
        self, mock_bridge, auth_client, organization
    ):
        mock_bridge.sync_keys.return_value = 3

        response = auth_client.post("/agentcc/api-keys/sync/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == {"synced": 3}
        mock_bridge.sync_keys.assert_called_once()
        # Sync is scoped to the active-request organization.
        assert (
            mock_bridge.sync_keys.call_args.kwargs["org"].id == organization.id
        )

    def test_sync_api_keys_unauthenticated(self, api_client):
        response = api_client.post("/agentcc/api-keys/sync/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_api_keys_cross_tenant_isolation(
        self, auth_client, api_client, organization, workspace, user
    ):
        from accounts.models.organization import Organization

        # Seed a key belonging to the current org and one to a foreign org.
        AgentccAPIKey.objects.create(
            gateway_key_id="gw-mine",
            name="mine",
            organization=organization,
            workspace=workspace,
        )
        foreign_org = Organization.objects.create(name="API Key Foreign Org")
        AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-not-mine",
            name="not-mine",
            organization=foreign_org,
            workspace=None,
        )

        response = auth_client.get("/agentcc/api-keys/")
        assert response.status_code == status.HTTP_200_OK
        ids = {row["gateway_key_id"] for row in response.json()["result"]}
        assert "gw-mine" in ids
        assert "gw-not-mine" not in ids

    @patch("agentcc.views.api_key.auth_bridge")
    def test_put_api_key_cross_tenant_returns_404(
        self, mock_bridge, auth_client, user, organization
    ):
        from accounts.models.organization import Organization

        foreign_org = Organization.objects.create(name="PUT Foreign Org")
        foreign_key = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-foreign",
            name="foreign",
            organization=foreign_org,
            workspace=None,
        )

        response = auth_client.put(
            f"/agentcc/api-keys/{foreign_key.id}/",
            {"name": "hijacked"},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_bridge.update_key.assert_not_called()

    def test_patch_api_key_updates_name(
        self, auth_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-patch",
            name="before",
            organization=organization,
            workspace=workspace,
        )
        with patch("agentcc.views.api_key.auth_bridge") as mock_bridge:
            def _update(api_key, **kwargs):
                for k, v in kwargs.items():
                    setattr(api_key, k, v)
                api_key.save(update_fields=[*kwargs.keys(), "updated_at"])
                return api_key

            mock_bridge.update_key.side_effect = _update
            response = auth_client.patch(
                f"/agentcc/api-keys/{key.id}/",
                {"name": "after"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        key.refresh_from_db()
        assert key.name == "after"

    def test_revoke_api_key_idempotence(
        self, auth_client, organization, workspace
    ):
        # Revoking an already-revoked key returns 200 and stays revoked;
        # auth_bridge.revoke_key is what enforces the state transition, so
        # the endpoint just re-runs the same flow.
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-revoke-twice",
            name="revoke-twice",
            status=AgentccAPIKey.REVOKED,
            organization=organization,
            workspace=workspace,
        )
        with patch("agentcc.views.api_key.auth_bridge") as mock_bridge:
            mock_bridge.revoke_key.return_value = (key, False)
            response = auth_client.post(f"/agentcc/api-keys/{key.id}/revoke/")

        assert response.status_code == status.HTTP_200_OK
        key.refresh_from_db()
        assert key.status == AgentccAPIKey.REVOKED


@pytest.mark.integration
@pytest.mark.api
class TestAgentccAPIKeyBulkAPI:
    """Tests for the gateway startup /agentcc/api-keys/bulk/ endpoint."""

    def _admin_client(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AGENTCC_TEST_ADMIN_TOKEN}")
        return client

    def test_bulk_api_keys_requires_admin_token(self, db):
        client = APIClient()
        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", AGENTCC_TEST_ADMIN_TOKEN):
            assert client.get("/agentcc/api-keys/bulk/").status_code == (
                status.HTTP_403_FORBIDDEN
            )

            client.credentials(HTTP_AUTHORIZATION="Bearer wrong-token")
            assert client.get("/agentcc/api-keys/bulk/").status_code == (
                status.HTTP_403_FORBIDDEN
            )

    def test_bulk_api_keys_returns_gateway_safe_metadata(
        self,
        organization,
        workspace,
    ):
        active = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-bulk-active",
            key_prefix="pk-bulk",
            key_hash="a" * 64,
            name="bulk-active",
            owner="platform",
            organization=organization,
            workspace=workspace,
            status=AgentccAPIKey.ACTIVE,
            allowed_models=["gpt-4o"],
            allowed_providers=["openai"],
            metadata={
                "source": "api-test",
                "enabled": True,
                "limits": {"rpm": 10},
                "tags": ["startup", "sync"],
                "none": None,
            },
        )
        no_hash = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-bulk-no-hash",
            name="bulk-no-hash",
            organization=organization,
            workspace=workspace,
            status=AgentccAPIKey.ACTIVE,
            key_hash="",
        )
        revoked = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-bulk-revoked",
            name="bulk-revoked",
            organization=organization,
            workspace=workspace,
            status=AgentccAPIKey.REVOKED,
            key_hash="b" * 64,
        )
        deleted = AgentccAPIKey.no_workspace_objects.create(
            gateway_key_id="gw-bulk-deleted",
            name="bulk-deleted",
            organization=organization,
            workspace=workspace,
            status=AgentccAPIKey.ACTIVE,
            key_hash="c" * 64,
            deleted=True,
        )

        with patch("agentcc.permissions.AGENTCC_ADMIN_TOKEN", AGENTCC_TEST_ADMIN_TOKEN):
            response = self._admin_client().get("/agentcc/api-keys/bulk/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        rows = response.json()["result"]
        returned_ids = {row["id"] for row in rows}
        assert active.gateway_key_id in returned_ids
        assert no_hash.gateway_key_id not in returned_ids
        assert revoked.gateway_key_id not in returned_ids
        assert deleted.gateway_key_id not in returned_ids

        row = next(row for row in rows if row["id"] == active.gateway_key_id)
        assert row["key_hash"] == "a" * 64
        assert row["models"] == ["gpt-4o"]
        assert row["providers"] == ["openai"]
        assert row["metadata"] == {
            "source": "api-test",
            "enabled": "true",
            "limits": '{"rpm":10}',
            "tags": '["startup","sync"]',
            "org_id": str(organization.id),
        }


@pytest.mark.integration
@pytest.mark.api
class TestAgentccRequestLogAPI:
    """Tests for /agentcc/request-logs/ endpoints."""

    def test_list_request_logs_authenticated(self, auth_client):
        response = auth_client.get("/agentcc/request-logs/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_request_logs_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/request-logs/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_retrieve_request_log_unknown_id_returns_404(self, auth_client):
        # A random UUID that does not belong to any log row.
        import uuid

        response = auth_client.get(f"/agentcc/request-logs/{uuid.uuid4()}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_request_log_unauthenticated(self, api_client):
        import uuid

        response = api_client.get(f"/agentcc/request-logs/{uuid.uuid4()}/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_list_request_logs_with_data(
        self, auth_client, gateway_id, organization, workspace
    ):
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-100",
            model="gpt-4",
            provider="openai",
            status_code=200,
            latency_ms=120,
        )
        response = auth_client.get("/agentcc/request-logs/")
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_model(self, auth_client, gateway_id, organization, workspace):
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-200",
            model="claude-3",
            provider="anthropic",
            status_code=200,
        )
        response = auth_client.get("/agentcc/request-logs/?model=claude-3")
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_is_error(self, auth_client, gateway_id, organization, workspace):
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-err",
            model="gpt-4",
            is_error=True,
            error_message="rate limited",
            status_code=429,
        )
        response = auth_client.get("/agentcc/request-logs/?is_error=true")
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestWebhookAPI:
    """Tests for /agentcc/webhook/logs/ endpoint."""

    def test_webhook_ingests_logs(self, api_client, gateway_id, organization):
        # The webhook view now requires a shared secret header, and the
        # ingestion pipeline resolves the organization via either an
        # ``auth_key_id`` that maps to a ``AgentccAPIKey`` or ``metadata.org_id``.
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-secret"):
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {
                    "gateway_id": str(gateway_id),
                    "logs": [
                        {
                            "request_id": "wh-req-1",
                            "model": "gpt-4",
                            "provider": "openai",
                            "latency_ms": 200,
                            "input_tokens": 50,
                            "output_tokens": 100,
                            "total_tokens": 150,
                            "cost": 0.003,
                            "status_code": 200,
                            "metadata": {"org_id": str(organization.id)},
                        },
                        {
                            "request_id": "wh-req-2",
                            "model": "claude-3",
                            "provider": "anthropic",
                            "latency_ms": 300,
                            "status_code": 200,
                            "metadata": {"org_id": str(organization.id)},
                        },
                    ],
                },
                format="json",
                HTTP_X_WEBHOOK_SECRET="test-secret",
            )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert data["result"]["ingested"] == 2

        # Verify logs were created in DB
        assert AgentccRequestLog.no_workspace_objects.filter(
            request_id="wh-req-1"
        ).exists()
        assert AgentccRequestLog.no_workspace_objects.filter(
            request_id="wh-req-2"
        ).exists()

    def test_webhook_missing_gateway_id(self, api_client):
        # No ``AGENTCC_WEBHOOK_SECRET`` configured → always 400.
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", ""):
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {"logs": []},
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_webhook_invalid_gateway_id(self, api_client, db):
        # Current webhook view ignores ``gateway_id``; an unknown one still
        # reaches ingestion path. Without a shared secret it returns 400.
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", ""):
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {
                    "gateway_id": "00000000-0000-0000-0000-000000000099",
                    "logs": [],
                },
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_webhook_empty_logs(self, api_client, gateway_id):
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-secret"):
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {
                    "gateway_id": str(gateway_id),
                    "logs": [],
                },
                format="json",
                HTTP_X_WEBHOOK_SECRET="test-secret",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["ingested"] == 0

    def test_webhook_rejects_unknown_body_field(self, api_client):
        response = api_client.post(
            "/agentcc/webhook/logs/",
            {"logs": [], "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_shadow_webhook_rejects_unknown_body_field(self, api_client):
        response = api_client.post(
            "/agentcc/webhook/shadow-results/",
            {"results": [], "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_webhook_with_secret(self, api_client, gateway_id):
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "my-secret"):
            # Without secret — should fail
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {"gateway_id": str(gateway_id), "logs": []},
                format="json",
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST

            # With correct secret — should succeed
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {"gateway_id": str(gateway_id), "logs": []},
                format="json",
                HTTP_X_WEBHOOK_SECRET="my-secret",
            )
            assert response.status_code == status.HTTP_200_OK


# ──────────────────────────────────────────────────────────────
# Phase 5.2: Advanced Filters, Search, Sessions, Export
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def sample_logs(organization, workspace):
    """Create a set of request logs for testing filters/search/sessions."""
    now = timezone.now()
    logs = []
    # Log 1: gpt-4, success, session A
    logs.append(
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-001",
            model="gpt-4",
            provider="openai",
            status_code=200,
            latency_ms=500,
            cost=Decimal("0.003000"),
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            is_error=False,
            cache_hit=False,
            session_id="sess-A",
            user_id="user-1",
            started_at=now,
            request_body={"messages": [{"role": "user", "content": "hello"}]},
            response_body={"choices": [{"message": {"content": "hi"}}]},
        )
    )
    # Log 2: claude-3, error, session A
    logs.append(
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-002",
            model="claude-3",
            provider="anthropic",
            status_code=429,
            latency_ms=50,
            cost=Decimal("0.000000"),
            input_tokens=80,
            output_tokens=0,
            total_tokens=80,
            is_error=True,
            error_message="rate limited",
            cache_hit=False,
            session_id="sess-A",
            user_id="user-1",
            started_at=now,
        )
    )
    # Log 3: gpt-4, success, cache hit, session B
    logs.append(
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-003",
            model="gpt-4",
            provider="openai",
            status_code=200,
            latency_ms=10,
            cost=Decimal("0.000100"),
            input_tokens=50,
            output_tokens=100,
            total_tokens=150,
            is_error=False,
            cache_hit=True,
            session_id="sess-B",
            user_id="user-2",
            guardrail_triggered=True,
            started_at=now,
        )
    )
    # Log 4: gpt-4, success, no session
    logs.append(
        AgentccRequestLog.objects.create(
            organization=organization,
            workspace=workspace,
            request_id="req-004",
            model="gpt-4",
            provider="openai",
            status_code=200,
            latency_ms=1200,
            cost=Decimal("0.010000"),
            input_tokens=500,
            output_tokens=800,
            total_tokens=1300,
            is_error=False,
            cache_hit=False,
            session_id="",
            user_id="user-1",
            started_at=now,
        )
    )
    return logs


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogAdvancedFilters:
    """Tests for Phase 5.2 advanced filter query params."""

    def test_filter_by_multi_model(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?model=gpt-4,claude-3")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 4  # all logs match

    def test_filter_by_single_provider(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?provider=anthropic")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1  # only claude-3 log

    def test_filter_by_cache_hit(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?cache_hit=true")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1  # req-003

    def test_filter_by_guardrail_triggered(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?guardrail_triggered=true")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1

    def test_filter_by_latency_range(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/?min_latency=100&max_latency=600"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1  # req-001 (500ms)

    def test_filter_by_cost_range(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?min_cost=0.005")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1  # req-004 (0.010)

    def test_filter_by_user_id(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?user_id=user-2")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1

    def test_filter_by_session_id(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?session_id=sess-A")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 2

    def test_filter_by_status_code_multi(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?status_code=429")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1

    def test_filter_by_status_code_range(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/?min_status_code=400&max_status_code=499"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["status_code"] == 429

    def test_ordering_by_latency(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/?ordering=-latency_ms")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data["results"]
        assert results[0]["latency_ms"] >= results[-1]["latency_ms"]

    def test_filter_composition(self, auth_client, sample_logs):
        """Multiple filters compose with AND."""
        response = auth_client.get(
            "/agentcc/request-logs/?model=gpt-4&is_error=false&cache_hit=false"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # req-001 and req-004 match
        assert data["count"] == 2


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogDetailView:
    """Tests for Phase 5.2 detail view with body fields."""

    def test_retrieve_includes_bodies(self, auth_client, sample_logs):
        log = sample_logs[0]  # req-001 has request_body and response_body
        response = auth_client.get(f"/agentcc/request-logs/{log.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "request_body" in data["result"]
        assert data["result"]["request_body"]["messages"][0]["content"] == "hello"
        assert "response_body" in data["result"]

    def test_list_excludes_bodies(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # List serializer should NOT include body fields
        first_result = data["results"][0]
        assert "request_body" not in first_result
        assert "response_body" not in first_result


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogSearch:
    """Tests for Phase 5.2 search endpoint."""

    def test_search_by_model_name(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/search/?q=claude")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1

    def test_search_by_error_message(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/search/?q=rate+limited")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] >= 1

    def test_search_by_request_id(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/search/?q=req-003")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1

    def test_search_too_short(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/search/?q=a")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_search_empty(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/search/?q=")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogSessions:
    """Tests for Phase 5.2 session aggregation endpoints."""

    def test_sessions_list(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/sessions/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # 2 sessions: sess-A (2 logs), sess-B (1 log)
        assert data["count"] == 2

    def test_sessions_aggregation(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/sessions/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        sessions = {s["session_id"]: s for s in data["results"]}

        sess_a = sessions["sess-A"]
        assert sess_a["request_count"] == 2
        assert sess_a["error_count"] == 1
        assert "gpt-4" in sess_a["models"]
        assert "claude-3" in sess_a["models"]

        sess_b = sessions["sess-B"]
        assert sess_b["request_count"] == 1
        assert sess_b["error_count"] == 0

    def test_sessions_ordering_by_request_count(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/sessions/?ordering=-request_count"
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["results"][0]["session_id"] == "sess-A"
        assert data["results"][0]["request_count"] == 2

    def test_sessions_apply_log_filters_before_aggregation(
        self, auth_client, sample_logs
    ):
        response = auth_client.get("/agentcc/request-logs/sessions/?provider=anthropic")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["session_id"] == "sess-A"
        assert data["results"][0]["request_count"] == 1
        assert data["results"][0]["providers"] == ["anthropic"]

    def test_session_detail(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/sessions/sess-A/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 2

    def test_session_detail_not_found(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/sessions/nonexistent/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_sessions_excludes_empty_session_id(self, auth_client, sample_logs):
        """Logs with empty session_id should not appear in sessions."""
        response = auth_client.get("/agentcc/request-logs/sessions/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        session_ids = [s["session_id"] for s in data["results"]]
        assert "" not in session_ids


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogExport:
    """Tests for Phase 5.2 export endpoint."""

    def test_export_csv(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/export/?export_format=csv")
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"
        assert "attachment" in response["Content-Disposition"]
        content = b"".join(response.streaming_content).decode()
        lines = content.strip().split("\n")
        assert len(lines) == 5  # header + 4 data rows

    def test_export_json(self, auth_client, sample_logs):
        response = auth_client.get("/agentcc/request-logs/export/?export_format=json")
        assert response.status_code == status.HTTP_200_OK
        assert "ndjson" in response["Content-Type"]
        content = b"".join(response.streaming_content).decode()
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 4  # 4 data rows (no header in JSON)

    def test_export_with_filters(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/export/?export_format=csv&model=claude-3",
        )
        assert response.status_code == status.HTTP_200_OK
        content = b"".join(response.streaming_content).decode()
        lines = content.strip().split("\n")
        assert len(lines) == 2  # header + 1 data row

    def test_export_with_search_param(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/export/?export_format=csv&search=req-003",
        )
        assert response.status_code == status.HTTP_200_OK
        content = b"".join(response.streaming_content).decode()
        lines = content.strip().split("\n")
        assert len(lines) == 2  # header + 1 data row
        assert "req-003" in lines[1]
        assert "req-001" not in content

    def test_export_empty(self, auth_client, sample_logs):
        response = auth_client.get(
            "/agentcc/request-logs/export/?model=nonexistent-model"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_default_csv(self, auth_client, sample_logs):
        """Default format should be CSV."""
        response = auth_client.get("/agentcc/request-logs/export/")
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/csv"


@pytest.mark.integration
@pytest.mark.api
class TestWebhookWithBodies:
    """Tests for webhook ingestion of new Phase 5.2 body fields."""

    def test_webhook_ingests_bodies(self, api_client, gateway_id, organization):
        with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", "test-secret"):
            response = api_client.post(
                "/agentcc/webhook/logs/",
                {
                    "gateway_id": str(gateway_id),
                    "logs": [
                        {
                            "request_id": "body-req-1",
                            "model": "gpt-4",
                            "provider": "openai",
                            "status_code": 200,
                            "latency_ms": 100,
                            "request_body": {
                                "messages": [{"role": "user", "content": "test"}]
                            },
                            "response_body": {
                                "choices": [{"message": {"content": "response"}}]
                            },
                            "request_headers": {
                                "Authorization": "Bearer sk-secret",
                                "Content-Type": "application/json",
                            },
                            "response_headers": {"x-request-id": "abc"},
                            "guardrail_results": [
                                {"name": "pii", "action": "pass", "confidence": 0.98}
                            ],
                            "metadata": {"org_id": str(organization.id)},
                        },
                    ],
                },
                format="json",
                HTTP_X_WEBHOOK_SECRET="test-secret",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["ingested"] == 1

        log = AgentccRequestLog.no_workspace_objects.get(request_id="body-req-1")
        assert log.request_body is not None
        assert log.response_body is not None
        assert log.guardrail_results is not None
        assert len(log.guardrail_results) == 1

        # Check headers stored (keys may be transformed by CamelCase parser)
        headers = log.request_headers
        assert headers is not None
        # At least one header should be sanitized (auth-related)
        sanitized = {k.lower(): v for k, v in headers.items()}
        assert sanitized.get("authorization") == "***"

        assert log.response_headers is not None


# =====================================================================
# Phase 5.3: Analytics Dashboard Tests
# =====================================================================


@pytest.fixture
def analytics_logs(organization, workspace):
    """Create a diverse set of request logs for analytics testing.

    Creates 10 logs across 2 models, 2 providers, with varied costs,
    latencies, error states, and cache hits for robust analytics testing.
    """
    now = timezone.now()
    logs = []
    base_data = [
        # model, provider, status, latency, cost, tokens, is_error, cache_hit, user_id
        ("gpt-4", "openai", 200, 500, "0.003000", 300, False, False, "user-1"),
        ("gpt-4", "openai", 200, 800, "0.005000", 500, False, False, "user-1"),
        ("gpt-4", "openai", 200, 100, "0.000500", 150, False, True, "user-2"),
        ("gpt-4", "openai", 500, 2000, "0.000000", 0, True, False, "user-1"),
        ("claude-3", "anthropic", 200, 300, "0.004000", 400, False, False, "user-2"),
        ("claude-3", "anthropic", 200, 450, "0.006000", 600, False, False, "user-1"),
        ("claude-3", "anthropic", 429, 50, "0.000000", 0, True, False, "user-2"),
        ("claude-3", "anthropic", 200, 200, "0.001000", 100, False, True, "user-3"),
        ("gpt-4", "openai", 200, 600, "0.004000", 350, False, False, "user-3"),
        ("gpt-4", "openai", 200, 150, "0.001000", 200, False, False, "user-2"),
    ]
    for i, (model, provider, sc, lat, cost, tok, err, cache, uid) in enumerate(
        base_data
    ):
        logs.append(
            AgentccRequestLog.objects.create(
                organization=organization,
                workspace=workspace,
                request_id=f"analytics-req-{i:03d}",
                model=model,
                provider=provider,
                status_code=sc,
                latency_ms=lat,
                cost=Decimal(cost),
                input_tokens=tok // 2,
                output_tokens=tok // 2,
                total_tokens=tok,
                is_error=err,
                cache_hit=cache,
                user_id=uid,
                session_id=f"sess-{uid}",
                started_at=now,
                error_message=(
                    "server error"
                    if sc == 500
                    else ("rate limited" if sc == 429 else "")
                ),
                guardrail_triggered=(i == 2),
            )
        )
    return logs


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsOverview:
    """Tests for GET /agentcc/analytics/overview/"""

    def test_overview_returns_kpis(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/overview/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        result = data["result"]

        # Check all KPI fields present
        for field in [
            "total_requests",
            "total_tokens",
            "total_cost",
            "avg_latency_ms",
            "error_rate",
            "cache_hit_rate",
            "p95_latency_ms",
            "active_models",
        ]:
            assert field in result, f"Missing field: {field}"
            assert "value" in result[field]
            assert "trend" in result[field]

        # Verify values
        assert result["total_requests"]["value"] == 10
        assert result["active_models"]["value"] == 2
        assert result["error_rate"]["value"] == 20.0  # 2 errors / 10 total
        assert result["cache_hit_rate"]["value"] == 20.0  # 2 cache hits / 10 total

    def test_overview_empty_data(self, auth_client, gateway_id):
        response = auth_client.get("/agentcc/analytics/overview/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["total_requests"]["value"] == 0
        assert result["error_rate"]["value"] == 0.0

    def test_overview_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/analytics/overview/")
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_overview_with_gateway_filter(
        self, auth_client, analytics_logs, gateway_id
    ):
        response = auth_client.get(
            f"/agentcc/analytics/overview/?gateway_id={gateway_id}"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["total_requests"]["value"] == 10


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsUsageTimeseries:
    """Tests for GET /agentcc/analytics/usage-timeseries/"""

    def test_usage_ungrouped(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/usage-timeseries/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["granularity"] == "hour"
        assert "series" in result
        assert isinstance(result["series"], list)
        # At least one bucket with data
        data_buckets = [s for s in result["series"] if s["request_count"] > 0]
        assert len(data_buckets) >= 1
        # Sum of request counts should equal 10
        total = sum(s["request_count"] for s in result["series"])
        assert total == 10

    def test_usage_grouped_by_model(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/usage-timeseries/?group_by=model"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["group_by"] == "model"
        assert "groups" in result
        assert "gpt-4" in result["groups"]
        assert "claude-3" in result["groups"]

    def test_usage_grouped_by_provider(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/usage-timeseries/?group_by=provider"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "openai" in result["groups"]
        assert "anthropic" in result["groups"]

    def test_usage_with_granularity(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/usage-timeseries/?granularity=day"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["granularity"] == "day"

    def test_usage_invalid_granularity_defaults(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/usage-timeseries/?granularity=invalid"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["granularity"] == "hour"


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsCostBreakdown:
    """Tests for GET /agentcc/analytics/cost-breakdown/"""

    def test_cost_by_model(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/cost-breakdown/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["group_by"] == "model"
        assert "total_cost" in result
        assert "breakdown" in result
        assert len(result["breakdown"]) == 2  # gpt-4 and claude-3

        # Verify breakdown has correct fields
        item = result["breakdown"][0]
        for field in [
            "name",
            "total_cost",
            "percentage",
            "request_count",
            "total_tokens",
            "avg_cost_per_request",
        ]:
            assert field in item, f"Missing field: {field}"

    def test_cost_by_provider(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/cost-breakdown/?group_by=provider"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["group_by"] == "provider"
        names = [b["name"] for b in result["breakdown"]]
        assert "openai" in names
        assert "anthropic" in names

    def test_cost_by_user(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/cost-breakdown/?group_by=user_id"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert len(result["breakdown"]) == 3  # user-1, user-2, user-3

    def test_cost_top_n(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/cost-breakdown/?top_n=1")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        # 1 top model + 1 "Other"
        assert len(result["breakdown"]) == 2
        assert result["breakdown"][1]["name"] == "Other"

    def test_cost_percentages_sum_to_100(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/cost-breakdown/")
        result = response.json()["result"]
        total_pct = sum(b["percentage"] for b in result["breakdown"])
        assert 99.0 <= total_pct <= 101.0  # allow rounding tolerance

    def test_cost_empty_data(self, auth_client, gateway_id):
        response = auth_client.get("/agentcc/analytics/cost-breakdown/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["total_cost"] == "0"
        assert len(result["breakdown"]) == 0


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsLatencyStats:
    """Tests for GET /agentcc/analytics/latency-stats/"""

    def test_latency_returns_summary_and_timeseries(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/latency-stats/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "summary" in result
        assert "timeseries" in result

        summary = result["summary"]
        for field in [
            "avg_ms",
            "min_ms",
            "max_ms",
            "p50_ms",
            "p90_ms",
            "p95_ms",
            "p99_ms",
            "total_requests",
        ]:
            assert field in summary, f"Missing summary field: {field}"

        assert summary["total_requests"] == 10
        assert summary["min_ms"] == 50  # lowest latency in test data
        assert summary["max_ms"] == 2000  # highest latency in test data

    def test_latency_percentiles_ordering(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/latency-stats/")
        summary = response.json()["result"]["summary"]
        assert summary["p50_ms"] <= summary["p90_ms"]
        assert summary["p90_ms"] <= summary["p95_ms"]
        assert summary["p95_ms"] <= summary["p99_ms"]

    def test_latency_timeseries_has_percentiles(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/latency-stats/")
        ts = response.json()["result"]["timeseries"]
        data_buckets = [b for b in ts if b["request_count"] > 0]
        assert len(data_buckets) >= 1
        for field in ["avg_ms", "p50_ms", "p95_ms", "p99_ms"]:
            assert field in data_buckets[0]

    def test_latency_empty_data(self, auth_client, gateway_id):
        response = auth_client.get("/agentcc/analytics/latency-stats/")
        assert response.status_code == status.HTTP_200_OK
        summary = response.json()["result"]["summary"]
        assert summary["total_requests"] == 0
        assert summary["avg_ms"] == 0


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsErrorBreakdown:
    """Tests for GET /agentcc/analytics/error-breakdown/"""

    def test_error_breakdown_by_status_code(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/error-breakdown/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert result["total_requests"] == 10
        assert result["total_errors"] == 2
        assert result["overall_error_rate"] == 20.0
        assert "breakdown" in result
        assert "error_timeseries" in result

        # Check status codes present
        names = [b["name"] for b in result["breakdown"]]
        assert "500" in names
        assert "429" in names

    def test_error_breakdown_by_provider(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/error-breakdown/?group_by=provider"
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        names = [b["name"] for b in result["breakdown"]]
        # Errors: 1 openai (500), 1 anthropic (429)
        assert "openai" in names
        assert "anthropic" in names

    def test_error_breakdown_by_model(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/error-breakdown/?group_by=model")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        names = [b["name"] for b in result["breakdown"]]
        assert "gpt-4" in names
        assert "claude-3" in names

    def test_error_breakdown_has_sample_messages(self, auth_client, analytics_logs):
        response = auth_client.get(
            "/agentcc/analytics/error-breakdown/?group_by=status_code"
        )
        result = response.json()["result"]
        for item in result["breakdown"]:
            assert "sample_error_message" in item

    def test_error_timeseries_has_rates(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/error-breakdown/")
        ts = response.json()["result"]["error_timeseries"]
        data_buckets = [b for b in ts if b["total_count"] > 0]
        assert len(data_buckets) >= 1
        for field in ["error_count", "total_count", "error_rate"]:
            assert field in data_buckets[0]

    def test_error_empty_data(self, auth_client, gateway_id):
        response = auth_client.get("/agentcc/analytics/error-breakdown/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["total_errors"] == 0
        assert result["overall_error_rate"] == 0.0


@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsModelComparison:
    """Tests for GET /agentcc/analytics/model-comparison/"""

    def test_all_models_comparison(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/model-comparison/")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "models" in result
        assert len(result["models"]) == 2  # gpt-4 and claude-3

        for model_data in result["models"]:
            for field in [
                "model",
                "provider",
                "request_count",
                "total_tokens",
                "avg_latency_ms",
                "p50_latency_ms",
                "p95_latency_ms",
                "error_rate",
                "cache_hit_rate",
                "total_cost",
            ]:
                assert field in model_data, f"Missing field: {field}"

    def test_filtered_models_comparison(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/model-comparison/?models=gpt-4")
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert len(result["models"]) == 1
        assert result["models"][0]["model"] == "gpt-4"

    def test_model_metrics_correctness(self, auth_client, analytics_logs):
        response = auth_client.get("/agentcc/analytics/model-comparison/?models=gpt-4")
        result = response.json()["result"]
        gpt4 = result["models"][0]

        # gpt-4 has 6 logs: 5 success + 1 error
        assert gpt4["request_count"] == 6
        assert gpt4["error_rate"] == round(1 / 6 * 100, 2)
        assert gpt4["provider"] == "openai"

    def test_model_comparison_empty(self, auth_client, gateway_id):
        response = auth_client.get("/agentcc/analytics/model-comparison/")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["result"]["models"]) == 0
