"""Server-side analytics and alert webhooks stay off the network without a key.

Mixpanel (MIX_PANEL_TOKEN), PostHog (POSTHOG_API_KEY) and the internal-alert
Slack webhook (ERROR_LOGS_WEBHOOK) all run inline on signup and login. A
self-hosted install leaves every one of them empty, so each must then make no
request and log nothing above debug; and each must still send once its key is
set. No database or network needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings
from slack_sdk import WebhookClient
from structlog.testing import capture_logs


@pytest.fixture
def outbound_calls(monkeypatch):
    """Record every outbound ``requests`` / Slack webhook call instead of sending it."""
    calls: list[tuple[str, str, str]] = []

    def fake_session_request(self, method, url, *args, **kwargs):
        calls.append(("requests", str(method).upper(), str(url)))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"status": 1}'
        response.url = str(url)
        return response

    def fake_webhook_send(self, *args, **kwargs):
        calls.append(("slack", "POST", str(getattr(self, "url", ""))))
        return MagicMock(status_code=200, body="ok")

    monkeypatch.setattr(requests.sessions.Session, "request", fake_session_request)
    monkeypatch.setattr(WebhookClient, "send", fake_webhook_send)
    return calls


@pytest.fixture(autouse=True)
def _capturable_module_loggers(monkeypatch):
    """Give each module under test a fresh structlog proxy.

    mixpanel_util and posthog_util build their tracker at import, which logs.
    When a full session imports them during collection, structlog is still
    configured to cache, so their module loggers keep the processors of that
    moment and ``capture_logs()`` would see nothing they log afterwards.
    """
    import structlog

    import analytics.mixpanel_util as mixpanel_util
    import analytics.posthog_util as posthog_util
    import analytics.utils as analytics_utils

    for module in (mixpanel_util, posthog_util, analytics_utils):
        monkeypatch.setattr(module, "logger", structlog.get_logger(module.__name__))


def _above_debug(records):
    return [r for r in records if r.get("log_level") not in ("debug", None)]


@pytest.fixture
def fresh_token_notice(monkeypatch):
    """Let each tracker emit its one-time "no token" notice again."""
    import analytics.mixpanel_util as mixpanel_util
    import analytics.posthog_util as posthog_util

    monkeypatch.setattr(mixpanel_util, "_token_warning_logged", False)
    monkeypatch.setattr(posthog_util, "_token_warning_logged", False)


def _user_and_org():
    user = MagicMock()
    user.id = "user-1"
    user.email = "lead@example.com"
    user.name = "Ada Lovelace"
    org = MagicMock()
    org.id = "org-1"
    org.display_name = "Analytical Engines"
    return user, org


class TestMixpanel:
    def test_no_token_no_client_no_request_no_noise(
        self, monkeypatch, outbound_calls, fresh_token_notice
    ):
        from analytics.mixpanel_util import MixpanelTracker

        monkeypatch.delenv("MIX_PANEL_TOKEN", raising=False)
        user, org = _user_and_org()
        with (
            patch("analytics.mixpanel_util.Mixpanel") as client,
            capture_logs() as records,
        ):
            tracker = MixpanelTracker()
            tracker.set_details(user)
            tracker.track_event("Login_clicked", {"$user_id": "user-1"})
            tracker.update_org_details(org.id, org.display_name, "self-hosted")

        client.assert_not_called()
        assert tracker.mp is None
        assert outbound_calls == []
        assert _above_debug(records) == []

    def test_a_blank_token_counts_as_unset(self, monkeypatch, outbound_calls):
        from analytics.mixpanel_util import MixpanelTracker

        monkeypatch.setenv("MIX_PANEL_TOKEN", "   ")
        tracker = MixpanelTracker()
        tracker.track_event("Login_clicked", {"$user_id": "user-1"})

        assert tracker.mp is None
        assert outbound_calls == []

    def test_with_a_token_events_are_sent_with_a_bounded_timeout(
        self, monkeypatch, outbound_calls
    ):
        from analytics.mixpanel_util import (
            MIXPANEL_REQUEST_TIMEOUT_SECONDS,
            MixpanelTracker,
        )

        monkeypatch.setenv("MIX_PANEL_TOKEN", "mp-test-token")
        tracker = MixpanelTracker()
        tracker.track_event("Login_clicked", {"$user_id": "user-1"})

        assert tracker.mp._consumer._request_timeout == (
            MIXPANEL_REQUEST_TIMEOUT_SECONDS
        )
        assert outbound_calls == [
            ("requests", "POST", "https://api.mixpanel.com/track")
        ]

    def test_an_unreachable_mixpanel_is_not_retried(self, monkeypatch):
        """Login makes three calls inline; the client's default of four
        retries turns each unreachable one into ~25s instead of 5."""
        from analytics.mixpanel_util import MixpanelTracker

        monkeypatch.setenv("MIX_PANEL_TOKEN", "mp-test-token")
        tracker = MixpanelTracker()

        adapter = tracker.mp._consumer._session.get_adapter("https://api.mixpanel.com")
        assert adapter.max_retries.total == 0

    def test_set_details_never_fails_the_signup(self, monkeypatch):
        """``first_signup`` calls this inline; a Mixpanel outage must not
        turn into a failed signup."""
        from mixpanel import MixpanelException

        from analytics.mixpanel_util import MixpanelTracker

        monkeypatch.setenv("MIX_PANEL_TOKEN", "mp-test-token")
        tracker = MixpanelTracker()
        tracker.mp = MagicMock()
        tracker.mp.group_set_once.side_effect = MixpanelException("503")
        user, org = _user_and_org()

        with (
            patch("analytics.mixpanel_util.get_current_organization", return_value=org),
            patch("analytics.mixpanel_util.OrganizationSubscription", None),
            capture_logs() as records,
        ):
            tracker.set_details(user)

        assert [r["event"] for r in records if r["log_level"] == "error"] == [
            "mixpanel_set_details_failed"
        ]


class TestPostHog:
    def test_no_key_no_client_no_request_no_noise(
        self, monkeypatch, outbound_calls, fresh_token_notice
    ):
        from analytics.posthog_util import PostHogTracker

        monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
        user, org = _user_and_org()
        with (
            patch("analytics.posthog_util.Posthog") as client,
            capture_logs() as records,
        ):
            tracker = PostHogTracker()
            tracker.identify_user(user, org=org)
            tracker.capture("user-1", "api_request")
            assert tracker.is_feature_enabled("flag", "user-1") is False

        client.assert_not_called()
        assert tracker.is_enabled is False
        assert outbound_calls == []
        assert _above_debug(records) == []

    def test_with_a_key_the_client_is_built(self, monkeypatch):
        from analytics.posthog_util import PostHogTracker

        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")
        with patch("analytics.posthog_util.Posthog") as client:
            tracker = PostHogTracker()
            tracker.capture("user-1", "api_request")

        client.assert_called_once_with("phc_test", host="https://eu.i.posthog.com")
        client.return_value.capture.assert_called_once()


class TestTrackMixpanelEvent:
    def test_disabled_tracker_sends_nothing(self, outbound_calls):
        from analytics.mixpanel_util import mixpanel_tracker
        from analytics.utils import track_mixpanel_event

        with patch.object(mixpanel_tracker, "mp", None):
            track_mixpanel_event("Signup_details_submitted", {"$user_id": "u"})

        assert outbound_calls == []


class TestErrorLogsWebhook:
    @override_settings(ERROR_LOGS_WEBHOOK="")
    @patch.dict("os.environ", {"ENV_TYPE": "production"})
    def test_unset_webhook_posts_nothing(self, outbound_calls):
        from analytics.utils import mixpanel_slack_notfy

        with (
            patch("analytics.utils.WebhookClient") as client,
            capture_logs() as records,
        ):
            mixpanel_slack_notfy("HubSpot update failed")

        client.assert_not_called()
        assert outbound_calls == []
        assert _above_debug(records) == []

    @override_settings(ERROR_LOGS_WEBHOOK="https://hooks.slack.test/T/B/E")
    @patch.dict("os.environ", {"ENV_TYPE": "local"})
    def test_local_env_posts_nothing_even_with_a_webhook(self, outbound_calls):
        from analytics.utils import mixpanel_slack_notfy

        with patch("analytics.utils.WebhookClient") as client:
            mixpanel_slack_notfy("HubSpot update failed")

        client.assert_not_called()
        assert outbound_calls == []

    @override_settings(ERROR_LOGS_WEBHOOK="https://hooks.slack.test/T/B/E")
    @patch.dict("os.environ", {"ENV_TYPE": "production"})
    def test_posts_once_configured(self):
        from analytics.utils import mixpanel_slack_notfy

        with patch("analytics.utils.WebhookClient") as client:
            mixpanel_slack_notfy("HubSpot update failed")

        client.assert_called_once_with("https://hooks.slack.test/T/B/E", timeout=10)
        sent = client.return_value.send.call_args.kwargs["text"]
        assert sent.startswith("HubSpot update failed")
