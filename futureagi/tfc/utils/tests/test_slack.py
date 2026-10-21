"""
Unit tests for Slack notification utilities.

Run with: pytest tfc/utils/tests/test_slack.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from structlog.testing import capture_logs

WEBHOOK = "https://hooks.slack.test/T/B/C"


class TestSendCriticalSlackNotification:
    """Tests for send_critical_slack_notification function."""

    @pytest.mark.unit
    @patch("tfc.utils.slack.os.getenv")
    def test_skips_notification_in_local_environment(self, mock_getenv):
        """Test that notifications are skipped in local environment."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "local"

        result = send_critical_slack_notification("Test message")

        assert result is True  # Returns True but doesn't send

    @pytest.mark.unit
    @patch("tfc.utils.slack.os.getenv")
    def test_skips_notification_in_test_environment(self, mock_getenv):
        """Test that notifications are skipped in test environment."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "test"

        result = send_critical_slack_notification("Test message")

        assert result is True  # Returns True but doesn't send

    @pytest.mark.unit
    @override_settings(ERROR_LOGS_WEBHOOK=WEBHOOK)
    @patch("tfc.utils.slack.WebhookClient")
    @patch("tfc.utils.slack.os.getenv")
    def test_sends_notification_in_production(self, mock_getenv, mock_webhook_client):
        """Test that notifications are sent in production environment."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "production"
        mock_client_instance = MagicMock()
        mock_client_instance.send.return_value = MagicMock(status_code=200)
        mock_webhook_client.return_value = mock_client_instance

        result = send_critical_slack_notification("Test critical message")

        assert result is True
        mock_client_instance.send.assert_called_once()
        call_args = mock_client_instance.send.call_args
        assert "Test critical message" in call_args.kwargs["text"]
        assert "production" in call_args.kwargs["text"]

    @pytest.mark.unit
    @override_settings(ERROR_LOGS_WEBHOOK=WEBHOOK)
    @patch("tfc.utils.slack.WebhookClient")
    @patch("tfc.utils.slack.os.getenv")
    def test_returns_false_on_failed_send(self, mock_getenv, mock_webhook_client):
        """Test that function returns False when Slack API returns error."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "production"
        mock_client_instance = MagicMock()
        mock_client_instance.send.return_value = MagicMock(status_code=500)
        mock_webhook_client.return_value = mock_client_instance

        result = send_critical_slack_notification("Test message")

        assert result is False

    @pytest.mark.unit
    @override_settings(ERROR_LOGS_WEBHOOK=WEBHOOK)
    @patch("tfc.utils.slack.WebhookClient")
    @patch("tfc.utils.slack.os.getenv")
    def test_returns_false_on_exception(self, mock_getenv, mock_webhook_client):
        """Test that function returns False when exception occurs."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "production"
        mock_webhook_client.side_effect = Exception("Connection error")

        result = send_critical_slack_notification("Test message")

        assert result is False

    @pytest.mark.unit
    @override_settings(ERROR_LOGS_WEBHOOK=WEBHOOK)
    @patch("tfc.utils.slack.WebhookClient")
    @patch("tfc.utils.slack.os.getenv")
    def test_message_includes_environment(self, mock_getenv, mock_webhook_client):
        """Test that the message includes environment information."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "staging"
        mock_client_instance = MagicMock()
        mock_client_instance.send.return_value = MagicMock(status_code=200)
        mock_webhook_client.return_value = mock_client_instance

        send_critical_slack_notification("Alert message")

        call_args = mock_client_instance.send.call_args
        assert "*Environment:* staging" in call_args.kwargs["text"]

    @pytest.mark.unit
    @override_settings(ERROR_LOGS_WEBHOOK="")
    @patch("tfc.utils.slack.WebhookClient")
    @patch("tfc.utils.slack.os.getenv")
    def test_unset_webhook_builds_no_client_and_logs_nothing(
        self, mock_getenv, mock_webhook_client
    ):
        """A self-hosted install leaves ERROR_LOGS_WEBHOOK empty: no client,
        no request, and nothing logged above debug."""
        from tfc.utils.slack import send_critical_slack_notification

        mock_getenv.return_value = "production"

        with capture_logs() as records:
            result = send_critical_slack_notification("Test message")

        assert result is False
        mock_webhook_client.assert_not_called()
        assert [r for r in records if r["log_level"] != "debug"] == []
