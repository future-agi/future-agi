"""
Agentcc Services Tests

Tests for GatewayClient, auth_bridge, and log_ingestion services.
"""

import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import structlog
from django.utils import timezone
from pydantic import ValidationError

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import (
    GatewayClient,
    GatewayClientError,
    _note_missing_admin_token,
    get_gateway_client,
    resolve_gateway_internal_url,
)
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.webhook_delivery import deliver_webhook_events
from tfc.middleware.workspace_context import set_workspace_context


class TestMissingAdminTokenNotice:
    """Logged on import, so in every process that loads the client, one-off
    commands included."""

    @pytest.mark.parametrize(
        "deployment, level, quiet",
        [
            ("", "debug", "warning"),
            ("false", "debug", "warning"),
            ("US", "warning", "debug"),
        ],
    )
    def test_only_cloud_warns(self, monkeypatch, deployment, level, quiet):
        monkeypatch.setenv("CLOUD_DEPLOYMENT", deployment)
        with patch("agentcc.services.gateway_client.logger") as logger:
            _note_missing_admin_token("")

        getattr(logger, level).assert_called_once()
        getattr(logger, quiet).assert_not_called()

    def test_a_set_token_logs_nothing(self, monkeypatch):
        monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
        with patch("agentcc.services.gateway_client.logger") as logger:
            _note_missing_admin_token("admin-token")

        assert logger.method_calls == []


class TestGatewayClient:
    """Tests for GatewayClient HTTP client (mocked HTTP calls)."""

    def test_headers_with_token(self):
        client = GatewayClient("http://localhost:8080", admin_token="secret")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer secret"

    def test_headers_without_token(self):
        client = GatewayClient("http://localhost:8080")
        headers = client._headers()
        assert "Authorization" not in headers

    def test_base_url_trailing_slash_stripped(self):
        client = GatewayClient("http://localhost:8080/")
        assert client.base_url == "http://localhost:8080"

    def test_internal_url_supports_legacy_env_name(self, monkeypatch):
        monkeypatch.delenv("AGENTCC_GATEWAY_INTERNAL_URL", raising=False)
        monkeypatch.setenv("AGENTCC_INTERNAL_URL", "http://legacy-gateway:8080")

        assert resolve_gateway_internal_url() == "http://legacy-gateway:8080"

    def test_internal_url_defaults_to_the_in_network_gateway(self, monkeypatch):
        # Inside a container, localhost:8090 is the container itself; the
        # gateway listens on agentcc-gateway:8080 (8090 is only the port
        # compose publishes on the host).
        monkeypatch.delenv("AGENTCC_GATEWAY_INTERNAL_URL", raising=False)
        monkeypatch.delenv("AGENTCC_INTERNAL_URL", raising=False)
        monkeypatch.delenv("AGENTCC_GATEWAY_URL", raising=False)

        assert resolve_gateway_internal_url() == "http://agentcc-gateway:8080"

    def test_internal_url_honours_an_explicit_gateway_url(self, monkeypatch):
        # Host-side tools reach the gateway on the published port.
        monkeypatch.delenv("AGENTCC_GATEWAY_INTERNAL_URL", raising=False)
        monkeypatch.delenv("AGENTCC_INTERNAL_URL", raising=False)
        monkeypatch.setenv("AGENTCC_GATEWAY_URL", "http://localhost:8090")

        assert resolve_gateway_internal_url() == "http://localhost:8090"

    @patch("agentcc.services.gateway_client.httpx.Client")
    def test_health_check(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"status":"ok"}'
        mock_resp.json.return_value = {"status": "ok"}
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance

        client = GatewayClient("http://localhost:8080", "token")
        result = client.health_check()
        assert result == {"status": "ok"}

    @patch("agentcc.services.gateway_client.httpx.Client")
    def test_request_error_raises(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance

        client = GatewayClient("http://localhost:8080", "token")
        with pytest.raises(GatewayClientError) as exc_info:
            client.health_check()
        assert exc_info.value.status_code == 500

    @patch("agentcc.services.gateway_client.httpx.Client")
    def test_create_key_stringifies_metadata_for_gateway(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"id":"key_1"}'
        mock_resp.json.return_value = {"id": "key_1"}
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance

        client = GatewayClient("http://localhost:8080", "token")
        client.create_key(
            "test-key",
            metadata={
                "updated": True,
                "attempt": 2,
                "labels": ["alpha", "beta"],
                "none": None,
            },
        )

        request_json = mock_client_instance.request.call_args.kwargs["json"]
        assert request_json["metadata"] == {
            "updated": "true",
            "attempt": "2",
            "labels": '["alpha","beta"]',
        }

    @patch("agentcc.services.gateway_client.httpx.Client")
    def test_import_keys_sends_the_contract_shape(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"received":1,"loaded":1}'
        mock_resp.json.return_value = {"received": 1, "loaded": 1}
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance
        key = {
            "id": "key_1",
            "name": "restored",
            "owner": "",
            "key_hash": "a" * 64,
            "key_prefix": "sk-agentcc-a...",
            "models": [],
            "providers": [],
            "metadata": {"org_id": "org-1"},
            "expires_at": None,
        }

        client = GatewayClient("http://localhost:8080", "token")
        assert client.import_keys([key]) == {"received": 1, "loaded": 1}

        request_json = mock_client_instance.request.call_args.kwargs["json"]
        # No expiry is sent as no field at all, as the contract types it.
        assert request_json == {
            "keys": [{k: v for k, v in key.items() if k != "expires_at"}]
        }

        # A field outside the contract, such as the raw key, is never sent.
        mock_client_instance.request.reset_mock()
        with pytest.raises(ValidationError):
            client.import_keys([{**key, "key": "sk-agentcc-raw"}])
        mock_client_instance.request.assert_not_called()

    @patch("agentcc.services.gateway_client.httpx.Client")
    def test_update_key_stringifies_metadata_for_gateway(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"id":"key_1"}'
        mock_resp.json.return_value = {"id": "key_1"}
        mock_client_instance = MagicMock()
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=False)
        mock_client_instance.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client_instance

        client = GatewayClient("http://localhost:8080", "token")
        client.update_key("key_1", metadata={"updated": True})

        request_json = mock_client_instance.request.call_args.kwargs["json"]
        assert request_json["metadata"] == {"updated": "true"}

    @patch(
        "agentcc.services.gateway_client.AGENTCC_GATEWAY_URL",
        "http://localhost:8080",
    )
    @patch(
        "agentcc.services.gateway_client.AGENTCC_GATEWAY_INTERNAL_URL",
        "http://localhost:8080",
    )
    @patch(
        "agentcc.services.gateway_client.AGENTCC_ADMIN_TOKEN",
        "env-token",
    )
    def test_get_gateway_client_factory(self):
        client = get_gateway_client()
        assert isinstance(client, GatewayClient)
        assert client.base_url == "http://localhost:8080"


@pytest.mark.integration
class TestAuthBridge:
    """Tests for auth_bridge service (mocked gateway calls)."""

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_provision_key(self, mock_get_client, organization, workspace, user):
        mock_client = MagicMock()
        mock_client.create_key.return_value = {
            "id": "gw-new-key-id",
            "key": "pk-full-raw-key-123",
            "key_prefix": "pk-full",
            "name": "my-key",
            "owner": "test",
            "status": "active",
            "models": ["gpt-4"],
            "providers": ["openai"],
            "created_at": "2026-02-23T00:00:00Z",
        }
        mock_get_client.return_value = mock_client

        api_key, raw_key = auth_bridge.provision_key(
            name="my-key",
            owner="test",
            user=user,
            models=["gpt-4"],
            providers=["openai"],
        )

        assert isinstance(api_key, AgentccAPIKey)
        assert api_key.gateway_key_id == "gw-new-key-id"
        assert api_key.key_prefix == "pk-full"
        assert api_key.name == "my-key"
        assert raw_key == "pk-full-raw-key-123"

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_provision_key_does_not_attach_workspace_from_other_org(
        self, mock_get_client, organization, workspace, user, db
    ):
        other_org = Organization.objects.create(name="Other Organization")
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=other_org,
            is_default=True,
            is_active=True,
            created_by=user,
        )

        mock_client = MagicMock()
        mock_client.create_key.return_value = {
            "id": "gw-cross-org-key-id",
            "key": "pk-cross-org-raw-key-123",
            "key_prefix": "pk-cross-org",
            "name": "cross-org-key",
            "owner": "test",
            "status": "active",
            "models": [],
            "providers": [],
            "created_at": "2026-02-23T00:00:00Z",
        }
        mock_get_client.return_value = mock_client

        set_workspace_context(
            workspace=other_workspace,
            organization=organization,
            user=user,
        )

        api_key, _ = auth_bridge.provision_key(
            name="cross-org-key",
            owner="test",
            user=user,
            organization=organization,
        )

        assert api_key.organization_id == organization.id
        assert api_key.workspace_id is None

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_revoke_key(self, mock_get_client, organization, workspace):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-to-revoke",
            name="revoke-me",
            organization=organization,
            workspace=workspace,
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result, gateway_failed = auth_bridge.revoke_key(key)
        assert result.status == AgentccAPIKey.REVOKED
        assert gateway_failed is False
        mock_client.revoke_key.assert_called_once_with("gw-to-revoke")

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_revoke_key_gateway_unreachable(
        self, mock_get_client, organization, workspace
    ):
        key = AgentccAPIKey.objects.create(
            gateway_key_id="gw-unreachable",
            name="revoke-unreachable",
            organization=organization,
            workspace=workspace,
        )
        mock_client = MagicMock()
        mock_client.revoke_key.side_effect = GatewayClientError("unreachable")
        mock_get_client.return_value = mock_client

        # Should still mark as revoked locally
        result, gateway_failed = auth_bridge.revoke_key(key)
        assert result.status == AgentccAPIKey.REVOKED
        assert gateway_failed is True


class TestAuthBridgeKeyIdsAndSync:
    """Gateway key IDs that repeat across restarts, and Sync restoring keys."""

    @staticmethod
    def _gateway_key(key_id, raw_key):
        return {
            "id": key_id,
            "key": raw_key,
            "key_prefix": raw_key[:12] + "...",
            "name": "new-key",
            "owner": "",
            "status": "active",
            "models": [],
            "providers": [],
        }

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_provision_key_never_overwrites_another_keys_row(
        self, mock_get_client, organization, workspace, user
    ):
        older = AgentccAPIKey.objects.create(
            gateway_key_id="key_2",
            name="older-key",
            organization=organization,
            workspace=workspace,
            key_hash="a" * 64,
            key_prefix="sk-agentcc-a...",
        )
        mock_client = MagicMock()
        # A gateway whose ID counter restarted hands out key_2 again.
        mock_client.create_key.return_value = self._gateway_key(
            "key_2", "sk-agentcc-b" + "0" * 47
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(auth_bridge.GatewayKeyIdCollision):
            auth_bridge.provision_key(name="new-key", user=user)

        older.refresh_from_db()
        assert older.name == "older-key"
        assert older.key_hash == "a" * 64
        assert (
            AgentccAPIKey.no_workspace_objects.filter(gateway_key_id="key_2").count()
            == 1
        )
        # The key the gateway just minted is discarded, not left orphaned.
        mock_client.revoke_key.assert_called_once_with("key_2")

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_provision_key_completes_a_row_a_racing_sync_stored(
        self, mock_get_client, organization, workspace, user
    ):
        raw_key = "sk-agentcc-c" + "0" * 47
        AgentccAPIKey.objects.create(
            gateway_key_id="key_raced",
            name="new-key",
            organization=organization,
            workspace=workspace,
            key_prefix=raw_key[:12] + "...",
        )
        mock_client = MagicMock()
        mock_client.create_key.return_value = self._gateway_key("key_raced", raw_key)
        mock_get_client.return_value = mock_client

        api_key, _ = auth_bridge.provision_key(name="new-key", user=user)

        assert api_key.gateway_key_id == "key_raced"
        assert api_key.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
        mock_client.revoke_key.assert_not_called()

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_pushes_keys_the_gateway_lost(
        self, mock_get_client, organization, workspace
    ):
        lost = AgentccAPIKey.objects.create(
            gateway_key_id="key_lost",
            name="lost-on-restart",
            organization=organization,
            workspace=workspace,
            key_hash="d" * 64,
            key_prefix="sk-agentcc-d...",
            allowed_models=["mock-gw"],
        )
        AgentccAPIKey.objects.create(
            gateway_key_id="key_expired",
            name="expired",
            organization=organization,
            workspace=workspace,
            key_hash="e" * 64,
            expires_at=timezone.now() - timedelta(days=1),
        )
        AgentccAPIKey.objects.create(
            gateway_key_id="key_revoked",
            name="revoked",
            organization=organization,
            workspace=workspace,
            key_hash="f" * 64,
            status=AgentccAPIKey.REVOKED,
        )
        AgentccAPIKey.objects.create(
            gateway_key_id="key_no_hash",
            name="imported-without-hash",
            organization=organization,
            workspace=workspace,
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {"data": []}
        mock_client.import_keys.return_value = {"received": 1, "loaded": 1}
        mock_get_client.return_value = mock_client

        with structlog.testing.capture_logs() as logs:
            synced = auth_bridge.sync_keys(org=organization)

        assert synced == 1
        (unrecoverable,) = [
            log for log in logs if log["event"] == "sync_keys_unrecoverable"
        ]
        assert unrecoverable["count"] == 1  # key_no_hash
        (pushed,), _ = mock_client.import_keys.call_args
        assert [k["id"] for k in pushed] == ["key_lost"]
        assert pushed[0]["key_hash"] == lost.key_hash
        assert pushed[0]["key_prefix"] == "sk-agentcc-d..."
        assert pushed[0]["models"] == ["mock-gw"]
        assert pushed[0]["metadata"]["org_id"] == str(organization.id)
        # What goes over the wire must be JSON, never the raw key.
        json.dumps(pushed)
        assert "key" not in pushed[0]

    @pytest.mark.parametrize("status_code", [404, 405])
    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_still_works_with_a_gateway_that_cannot_import_keys(
        self, mock_get_client, status_code, organization, workspace
    ):
        # A gateway older than POST /-/keys/sync: Sync reports the keys it
        # could not restore instead of failing.
        AgentccAPIKey.objects.create(
            gateway_key_id="key_lost",
            name="lost-on-restart",
            organization=organization,
            workspace=workspace,
            key_hash="d" * 64,
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {"data": []}
        mock_client.import_keys.side_effect = GatewayClientError(
            f"Gateway returned {status_code}: not found", status_code=status_code
        )
        mock_get_client.return_value = mock_client

        with structlog.testing.capture_logs() as logs:
            synced = auth_bridge.sync_keys(org=organization)

        assert synced == 0
        (warning,) = [
            log for log in logs if log["event"] == "sync_keys_missing_from_gateway"
        ]
        assert warning["log_level"] == "warning"
        assert warning["missing"] == 1

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_fails_when_the_gateway_refuses_the_import(
        self, mock_get_client, organization, workspace
    ):
        AgentccAPIKey.objects.create(
            gateway_key_id="key_lost",
            name="lost-on-restart",
            organization=organization,
            workspace=workspace,
            key_hash="d" * 64,
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {"data": []}
        mock_client.import_keys.side_effect = GatewayClientError(
            "Gateway returned 500: boom", status_code=500
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(GatewayClientError):
            auth_bridge.sync_keys(org=organization)

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_revokes_on_the_gateway_what_django_revoked(
        self, mock_get_client, organization, workspace
    ):
        AgentccAPIKey.objects.create(
            gateway_key_id="key_missed_revoke",
            name="revoked-while-gateway-down",
            organization=organization,
            workspace=workspace,
            key_hash="1" * 64,
            key_prefix="sk-agentcc-1...",
            status=AgentccAPIKey.REVOKED,
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {
            "data": [
                {
                    "id": "key_missed_revoke",
                    "key_prefix": "sk-agentcc-1...",
                    "status": "active",
                    "metadata": {"org_id": str(organization.id)},
                }
            ]
        }
        mock_get_client.return_value = mock_client

        auth_bridge.sync_keys(org=organization)

        mock_client.revoke_key.assert_called_once_with("key_missed_revoke")
        mock_client.import_keys.assert_not_called()

    @patch("agentcc.services.auth_bridge.get_gateway_client")
    def test_sync_leaves_a_row_alone_when_its_id_was_reissued(
        self, mock_get_client, organization, workspace
    ):
        other_org = Organization.objects.create(name="Other Organization")
        row = AgentccAPIKey.objects.create(
            gateway_key_id="key_2",
            name="other-orgs-key",
            organization=other_org,
            workspace=None,
            key_hash="2" * 64,
            key_prefix="sk-agentcc-2...",
        )
        mock_client = MagicMock()
        mock_client.list_keys.return_value = {
            "data": [
                {
                    "id": "key_2",
                    "key_prefix": "sk-agentcc-9...",
                    "status": "active",
                    "metadata": {"org_id": str(organization.id)},
                }
            ]
        }
        mock_get_client.return_value = mock_client

        auth_bridge.sync_keys(org=organization)

        row.refresh_from_db()
        assert row.organization_id == other_org.id
        assert row.name == "other-orgs-key"


@pytest.mark.integration
class TestLogIngestion:
    """Tests for log_ingestion service."""

    def test_ingest_request_logs(self, organization):
        # Create an API key so _resolve_org can find the org
        AgentccAPIKey.objects.create(
            gateway_key_id="test-key-for-ingest",
            name="ingest-key",
            organization=organization,
        )
        logs = [
            {
                "request_id": "ingest-1",
                "model": "gpt-4",
                "provider": "openai",
                "latency_ms": 100,
                "input_tokens": 50,
                "output_tokens": 25,
                "total_tokens": 75,
                "cost": 0.001,
                "status_code": 200,
                "auth_key_id": "test-key-for-ingest",
            },
            {
                "request_id": "ingest-2",
                "model": "claude-3",
                "provider": "anthropic",
                "latency_ms": 200,
                "status_code": 200,
                "is_stream": True,
                "auth_key_id": "test-key-for-ingest",
            },
        ]
        count = ingest_request_logs(logs)
        assert count == 2

    def test_ingest_empty_logs(self):
        count = ingest_request_logs([])
        assert count == 0

    @patch("agentcc.services.webhook_delivery.deliver_webhook_events")
    def test_ingest_creates_request_completed_webhook_events(
        self, mock_deliver_webhook_events, organization
    ):
        AgentccAPIKey.objects.create(
            gateway_key_id="test-key-for-webhooks",
            name="webhook-key",
            organization=organization,
        )
        webhook = AgentccWebhook.objects.create(
            organization=organization,
            name="req-complete",
            url="https://example.com/webhook",
            events=["request.completed"],
            is_active=True,
        )

        logs = [
            {
                "request_id": "wh-dispatch-1",
                "model": "gpt-4",
                "provider": "openai",
                "status_code": 200,
                "auth_key_id": "test-key-for-webhooks",
            }
        ]

        count = ingest_request_logs(logs)

        assert count == 1
        assert (
            AgentccWebhookEvent.no_workspace_objects.filter(
                webhook=webhook,
                event_type="request.completed",
                status=AgentccWebhookEvent.PENDING,
            ).count()
            == 1
        )
        mock_deliver_webhook_events.assert_called_once_with(
            org_id=organization.id,
            limit=100,
        )

    @patch("agentcc.services.webhook_delivery.deliver_webhook_events")
    def test_ingest_maps_error_and_guardrail_events(
        self, mock_deliver_webhook_events, organization
    ):
        AgentccAPIKey.objects.create(
            gateway_key_id="test-key-for-event-map",
            name="event-map-key",
            organization=organization,
        )
        AgentccWebhook.objects.create(
            organization=organization,
            name="all-events",
            url="https://example.com/events",
            events=["request.completed", "error.occurred", "guardrail.triggered"],
            is_active=True,
        )

        logs = [
            {
                "request_id": "wh-dispatch-2",
                "model": "gpt-4",
                "provider": "openai",
                "status_code": 429,
                "is_error": True,
                "guardrail_triggered": True,
                "auth_key_id": "test-key-for-event-map",
            }
        ]

        count = ingest_request_logs(logs)

        assert count == 1
        event_types = set(
            AgentccWebhookEvent.no_workspace_objects.filter(
                payload__request_id="wh-dispatch-2"
            ).values_list("event_type", flat=True)
        )
        assert event_types == {
            "request.completed",
            "error.occurred",
            "guardrail.triggered",
        }
        assert mock_deliver_webhook_events.call_count == 1


@pytest.mark.integration
class TestWebhookDelivery:
    @patch("agentcc.services.webhook_delivery.build_ssrf_safe_session")
    def test_retryable_failure_stays_pending(self, mock_build_session, organization):
        webhook = AgentccWebhook.objects.create(
            organization=organization,
            name="retry-webhook",
            url="https://example.com/fail",
            events=["request.completed"],
            is_active=True,
        )
        event = AgentccWebhookEvent.objects.create(
            organization=organization,
            webhook=webhook,
            event_type="request.completed",
            payload={"event": "request.completed", "request_id": "retry-1"},
            status=AgentccWebhookEvent.PENDING,
            attempts=0,
            max_attempts=5,
        )

        http = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "server error"
        http.post.return_value = resp
        mock_build_session.return_value = http

        result = deliver_webhook_events(event_ids=[event.id], limit=10)

        event.refresh_from_db()
        assert result["delivered"] == 0
        assert result["failed"] == 1
        assert event.status == AgentccWebhookEvent.PENDING
        assert event.attempts == 1
        assert event.next_retry_at is not None
