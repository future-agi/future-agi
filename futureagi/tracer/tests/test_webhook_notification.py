"""
Webhook notification channel tests.

Tests for creating/updating/duplicating monitors with webhook_url
and for the _send_webhook_notification delivery function.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework import status

from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.utils.monitor import (
    _handle_alert_trigger,
    _send_webhook_notification,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


# ── API Tests ──────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.api
class TestWebhookMonitorAPI:
    """Tests for webhook_url field on /tracer/user-alerts/ endpoints."""

    @pytest.mark.requires_ee
    def test_create_monitor_with_webhook_url(self, auth_client, observe_project):
        """Create a new monitor with a webhook notification URL."""
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Webhook Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
                "webhook_url": "https://example.com/webhook",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="Webhook Alert")
        assert monitor.webhook_url == "https://example.com/webhook"

    @pytest.mark.requires_ee
    def test_create_monitor_without_webhook_url(self, auth_client, observe_project):
        """Monitors created without webhook_url should have it as None."""
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "No Webhook Alert",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "static",
                "critical_threshold_value": 0.15,
                "alert_frequency": 60,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        monitor = UserAlertMonitor.objects.get(name="No Webhook Alert")
        assert monitor.webhook_url is None

    @pytest.mark.requires_ee
    def test_update_monitor_webhook_url(
        self, auth_client, user_alert_monitor
    ):
        """PATCH should update the webhook_url field."""
        response = auth_client.patch(
            f"/tracer/user-alerts/{user_alert_monitor.id}/",
            {"webhook_url": "https://example.com/updated-webhook"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        user_alert_monitor.refresh_from_db()
        assert user_alert_monitor.webhook_url == "https://example.com/updated-webhook"

    @pytest.mark.requires_ee
    def test_duplicate_monitor_copies_webhook_url(
        self, auth_client, user_alert_monitor
    ):
        """Duplicate action should preserve webhook_url."""
        user_alert_monitor.webhook_url = "https://example.com/webhook"
        user_alert_monitor.save(update_fields=["webhook_url"])

        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(user_alert_monitor.id), "name": "Webhook Copy"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        copied = UserAlertMonitor.objects.get(name="Webhook Copy")
        assert copied.webhook_url == "https://example.com/webhook"

    @pytest.mark.requires_ee
    def test_get_monitor_returns_webhook_url(
        self, auth_client, user_alert_monitor
    ):
        """GET details should include webhook_url in response."""
        user_alert_monitor.webhook_url = "https://example.com/webhook"
        user_alert_monitor.save(update_fields=["webhook_url"])

        response = auth_client.get(
            f"/tracer/user-alerts/{user_alert_monitor.id}/details/"
        )
        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert result["webhook_url"] == "https://example.com/webhook"


# ── Unit Tests: _send_webhook_notification ─────────────────────────────


@pytest.mark.unit
class TestSendWebhookNotification:
    """Unit tests for the _send_webhook_notification function."""

    def _make_monitor(self, webhook_url=None):
        """Create a mock monitor object."""
        monitor = MagicMock()
        monitor.id = uuid.uuid4()
        monitor.name = "Test Monitor"
        monitor.webhook_url = webhook_url
        monitor.metric_type = "count_of_errors"
        monitor.project_id = uuid.uuid4()
        monitor.project.name = "Test Project"
        return monitor

    def test_skips_when_no_url(self):
        """Should return immediately when webhook_url is not set."""
        monitor = self._make_monitor(webhook_url=None)
        with patch("tracer.utils.monitor.requests.post") as mock_post:
            _send_webhook_notification(monitor, "test msg", "critical")
            mock_post.assert_not_called()

    def test_skips_when_empty_url(self):
        """Should return immediately when webhook_url is empty string."""
        monitor = self._make_monitor(webhook_url="")
        with patch("tracer.utils.monitor.requests.post") as mock_post:
            _send_webhook_notification(monitor, "test msg", "critical")
            mock_post.assert_not_called()

    @override_settings(APP_URL="app.futureagi.com")
    def test_sends_correct_payload(self):
        """Should POST a JSON payload with required fields."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            _send_webhook_notification(
                monitor, "threshold breached", "critical",
                current_value=0.95, threshold_value=0.5,
            )

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

            assert payload["event"] == "alert.triggered"
            assert payload["alert_type"] == "critical"
            assert payload["monitor"]["id"] == str(monitor.id)
            assert payload["monitor"]["name"] == "Test Monitor"
            assert payload["project"]["id"] == str(monitor.project_id)
            assert payload["metric"] == "count_of_errors"
            assert payload["current_value"] == 0.95
            assert payload["threshold_value"] == 0.5
            assert payload["message"] == "threshold breached"
            assert "timestamp" in payload
            assert payload["dashboard_url"] == "app.futureagi.com/dashboard/alerts"

    def test_sends_correct_headers(self):
        """Should include Content-Type and User-Agent headers."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            _send_webhook_notification(monitor, "msg", "warning")

            call_kwargs = mock_post.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
            assert headers["Content-Type"] == "application/json"
            assert headers["User-Agent"] == "FutureAGI-Alerts/1.0"

    def test_uses_correct_url(self):
        """Should POST to the monitor's webhook_url."""
        url = "https://hooks.example.com/my-webhook"
        monitor = self._make_monitor(webhook_url=url)

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            _send_webhook_notification(monitor, "msg", "critical")

            assert mock_post.call_args[0][0] == url

    def test_uses_timeout(self):
        """Should set a reasonable timeout on the HTTP request."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            _send_webhook_notification(monitor, "msg", "critical")

            call_kwargs = mock_post.call_args
            timeout = call_kwargs.kwargs.get("timeout") or call_kwargs[1].get("timeout")
            assert timeout == 10

    def test_failure_is_logged_not_raised(self):
        """HTTP errors should be logged, not raised."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            # Should NOT raise
            _send_webhook_notification(monitor, "msg", "critical")

    def test_http_error_status_logged_not_raised(self):
        """Non-2xx responses should be caught by raise_for_status."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_resp = MagicMock(status_code=500)
            mock_resp.raise_for_status.side_effect = Exception("500 Server Error")
            mock_post.return_value = mock_resp

            # Should NOT raise
            _send_webhook_notification(monitor, "msg", "critical")

    @override_settings(APP_URL=None)
    def test_empty_dashboard_url_when_no_app_url(self):
        """When APP_URL is not configured, dashboard_url should be empty."""
        monitor = self._make_monitor(webhook_url="https://example.com/webhook")

        with patch("tracer.utils.monitor.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.raise_for_status = MagicMock()

            _send_webhook_notification(monitor, "msg", "critical")

            payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
            assert payload["dashboard_url"] == ""


# ── Unit Tests: _handle_alert_trigger with webhook ────────────────────


@pytest.mark.unit
class TestHandleAlertTriggerWebhook:
    """Tests that _handle_alert_trigger calls webhook alongside email/Slack."""

    @pytest.mark.django_db
    def test_calls_webhook_notification(self, user_alert_monitor):
        """_handle_alert_trigger should call _send_webhook_notification."""
        user_alert_monitor.webhook_url = "https://example.com/webhook"
        user_alert_monitor.save(update_fields=["webhook_url"])

        with (
            patch("tracer.utils.monitor._send_alert_email") as mock_email,
            patch("tracer.utils.monitor._send_slack_notification") as mock_slack,
            patch("tracer.utils.monitor._send_webhook_notification") as mock_webhook,
        ):
            _handle_alert_trigger(
                user_alert_monitor, "test message", "critical",
                current_value=0.9, threshold_value=0.5,
            )

            mock_email.assert_called_once()
            mock_slack.assert_called_once()
            mock_webhook.assert_called_once_with(
                user_alert_monitor, "test message", "critical", 0.9, 0.5,
            )

    @pytest.mark.django_db
    def test_webhook_failure_does_not_block_email(self, user_alert_monitor):
        """A failing webhook should not prevent email from being sent."""
        user_alert_monitor.webhook_url = "https://example.com/webhook"
        user_alert_monitor.save(update_fields=["webhook_url"])

        with (
            patch("tracer.utils.monitor._send_alert_email") as mock_email,
            patch("tracer.utils.monitor._send_slack_notification") as mock_slack,
            patch("tracer.utils.monitor.requests.post") as mock_post,
        ):
            mock_post.side_effect = Exception("Connection refused")

            _handle_alert_trigger(
                user_alert_monitor, "test message", "critical",
            )

            # Email and Slack should still be called despite webhook failure
            mock_email.assert_called_once()
            mock_slack.assert_called_once()

    @pytest.mark.django_db
    def test_creates_log_entry(self, user_alert_monitor):
        """_handle_alert_trigger should create a UserAlertMonitorLog."""
        with (
            patch("tracer.utils.monitor._send_alert_email"),
            patch("tracer.utils.monitor._send_slack_notification"),
            patch("tracer.utils.monitor._send_webhook_notification"),
        ):
            _handle_alert_trigger(
                user_alert_monitor, "log test", "warning",
            )

        log = UserAlertMonitorLog.objects.get(alert=user_alert_monitor)
        assert log.message == "log test"
        assert log.type == "warning"

    @pytest.mark.django_db
    def test_backward_compatible_without_value_kwargs(self, user_alert_monitor):
        """_handle_alert_trigger should work without current_value/threshold_value."""
        with (
            patch("tracer.utils.monitor._send_alert_email"),
            patch("tracer.utils.monitor._send_slack_notification"),
            patch("tracer.utils.monitor._send_webhook_notification") as mock_webhook,
        ):
            # Call without the new kwargs — backward compatible
            _handle_alert_trigger(
                user_alert_monitor, "test message", "critical",
            )

            mock_webhook.assert_called_once_with(
                user_alert_monitor, "test message", "critical", None, None,
            )
