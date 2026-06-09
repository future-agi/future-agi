"""
Agentcc Services Tests

Tests for GatewayClient, auth_bridge, and log_ingestion services.
"""

from unittest.mock import MagicMock, patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import (
    GatewayClient,
    GatewayClientError,
    get_gateway_client,
)
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.services.webhook_delivery import deliver_webhook_events
from tfc.middleware.workspace_context import set_workspace_context


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
