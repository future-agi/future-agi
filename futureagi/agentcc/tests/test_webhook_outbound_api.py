"""Coverage for the /agentcc/webhooks/ and /agentcc/webhook-events/ endpoints."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent


def _make_webhook(user, name="hook", **overrides):
    kwargs = {
        "organization": user.organization,
        "name": name,
        "url": "https://example.com/webhook",
        "events": ["request.completed"],
    }
    kwargs.update(overrides)
    return AgentccWebhook.objects.create(**kwargs)


def _make_event(webhook, user, status=None, **overrides):
    kwargs = {
        "organization": user.organization,
        "webhook": webhook,
        "event_type": "request.completed",
        "payload": {"foo": "bar"},
    }
    if status is not None:
        kwargs["status"] = status
    kwargs.update(overrides)
    return AgentccWebhookEvent.objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.api
class TestAgentccWebhookCRUD:
    def test_list_returns_webhooks(self, auth_client, user):
        _make_webhook(user, name="hook-a")
        _make_webhook(user, name="hook-b")

        response = auth_client.get("/agentcc/webhooks/")
        assert response.status_code == 200
        names = {row["name"] for row in response.json()["result"]}
        assert {"hook-a", "hook-b"} <= names

    def test_list_unauthenticated(self, api_client):
        response = api_client.get("/agentcc/webhooks/")
        assert response.status_code in (401, 403)

    def test_retrieve_returns_webhook(self, auth_client, user):
        webhook = _make_webhook(user, name="lookup-me")
        response = auth_client.get(f"/agentcc/webhooks/{webhook.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(webhook.id)

    def test_update_writes_fields(self, auth_client, user):
        webhook = _make_webhook(user, name="mutate-me")

        response = auth_client.put(
            f"/agentcc/webhooks/{webhook.id}/",
            {
                "name": "renamed",
                "url": "https://example.com/renamed",
                "events": ["error.occurred"],
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        webhook.refresh_from_db()
        assert webhook.name == "renamed"
        assert webhook.url == "https://example.com/renamed"
        assert webhook.events == ["error.occurred"]

    def test_partial_update_writes_single_field(self, auth_client, user):
        webhook = _make_webhook(user, name="partial")
        response = auth_client.patch(
            f"/agentcc/webhooks/{webhook.id}/",
            {"description": "now with a description"},
            format="json",
        )
        assert response.status_code == 200
        webhook.refresh_from_db()
        assert webhook.description == "now with a description"

    def test_destroy_soft_deletes_webhook(self, auth_client, user):
        webhook = _make_webhook(user, name="to-delete")
        response = auth_client.delete(f"/agentcc/webhooks/{webhook.id}/")
        assert response.status_code == 200
        webhook.refresh_from_db()
        assert webhook.deleted is True
        assert webhook.deleted_at is not None


@pytest.mark.integration
@pytest.mark.api
class TestAgentccWebhookTest:
    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    @patch("agentcc.views.webhook_outbound.ensure_public_http_url")
    def test_test_send_success_records_delivered_event(
        self, mock_ensure_url, mock_session_builder, auth_client, user
    ):
        webhook = _make_webhook(user, name="test-send")
        mock_response = MagicMock()
        mock_response.status_code = 200
        session = MagicMock()
        session.post.return_value = mock_response
        mock_session_builder.return_value = session

        response = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["status_code"] == 200
        assert response.json()["result"]["success"] is True

        # An event row is written for the test delivery.
        event = AgentccWebhookEvent.objects.get(
            webhook=webhook, event_type="test"
        )
        assert event.status == AgentccWebhookEvent.DELIVERED
        assert event.last_response_code == 200

    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    @patch("agentcc.views.webhook_outbound.ensure_public_http_url")
    def test_test_send_upstream_error_records_failed_event(
        self, mock_ensure_url, mock_session_builder, auth_client, user
    ):
        from requests import RequestException

        webhook = _make_webhook(user, name="test-fail")
        session = MagicMock()
        session.post.side_effect = RequestException("connection refused")
        mock_session_builder.return_value = session

        response = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")

        assert response.status_code == 200, response.json()
        assert response.json()["result"]["success"] is False
        assert "connection refused" in response.json()["result"]["error"]

        event = AgentccWebhookEvent.objects.get(
            webhook=webhook, event_type="test"
        )
        assert event.status == AgentccWebhookEvent.FAILED


@pytest.mark.integration
@pytest.mark.api
class TestAgentccWebhookEvent:
    def test_list_returns_events(self, auth_client, user):
        webhook = _make_webhook(user)
        _make_event(webhook, user)
        _make_event(webhook, user)

        response = auth_client.get("/agentcc/webhook-events/")
        assert response.status_code == 200
        assert len(response.json()["result"]) >= 2

    def test_list_filter_by_status(self, auth_client, user):
        webhook = _make_webhook(user)
        _make_event(webhook, user, status=AgentccWebhookEvent.DELIVERED)
        _make_event(webhook, user, status=AgentccWebhookEvent.FAILED)

        response = auth_client.get(
            "/agentcc/webhook-events/?status=failed"
        )
        assert response.status_code == 200
        rows = response.json()["result"]
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"

    def test_list_filter_by_webhook_id(self, auth_client, user):
        w1 = _make_webhook(user, name="hook-1")
        w2 = _make_webhook(user, name="hook-2")
        _make_event(w1, user)
        _make_event(w2, user)

        response = auth_client.get(
            f"/agentcc/webhook-events/?webhook_id={w1.id}"
        )
        assert response.status_code == 200
        rows = response.json()["result"]
        assert len(rows) == 1

    def test_retrieve_returns_event(self, auth_client, user):
        webhook = _make_webhook(user)
        event = _make_event(webhook, user)

        response = auth_client.get(f"/agentcc/webhook-events/{event.id}/")
        assert response.status_code == 200
        assert response.json()["result"]["id"] == str(event.id)

    def test_retry_resets_failed_event_to_pending(self, auth_client, user):
        webhook = _make_webhook(user)
        event = _make_event(
            webhook,
            user,
            status=AgentccWebhookEvent.FAILED,
            attempts=3,
            last_error="boom",
        )

        response = auth_client.post(
            f"/agentcc/webhook-events/{event.id}/retry/"
        )
        assert response.status_code == 200
        event.refresh_from_db()
        assert event.status == AgentccWebhookEvent.PENDING
        assert event.attempts == 0
        assert event.last_error == ""

    def test_retry_rejects_already_delivered(self, auth_client, user):
        webhook = _make_webhook(user)
        event = _make_event(
            webhook, user, status=AgentccWebhookEvent.DELIVERED
        )

        response = auth_client.post(
            f"/agentcc/webhook-events/{event.id}/retry/"
        )
        assert response.status_code == 400
