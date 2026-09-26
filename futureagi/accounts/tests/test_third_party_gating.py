"""Third-party calls on the auth and first-run path run only when their key is set.

A self-hosted install ships with HUBSPOT_API_TOKEN, SLACK_WEBHOOK_CHANNEL,
ERROR_LOGS_WEBHOOK, MIX_PANEL_TOKEN, POSTHOG_API_KEY and RECAPTCHA_SECRET_KEY
empty. Signup, login, token refresh and first-account creation must then make
no outbound request, log nothing above debug for the skipped integration, add
no latency and never fail because of it. Each integration still fires once its
key is set, which the second half of every pair below pins.

The classes without ``django_db`` need no services. The ``integration`` ones
drive the real views and ``manage.py create_user`` against the test database.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests
from django.core.management import call_command
from django.test import override_settings
from slack_sdk import WebhookClient
from structlog.testing import capture_logs

HUBSPOT_TOKEN = "pat-test-token"
HUBSPOT_UPDATE_URL = (
    "https://api.hubapi.com/crm/v3/objects/contacts/{}?idProperty=email"
)


@pytest.fixture
def outbound_calls(monkeypatch):
    """Record every outbound HTTP request instead of sending it.

    Covers ``requests`` (HubSpot, reCAPTCHA, Mixpanel's consumer, PostHog),
    ``httpx`` and Slack's ``WebhookClient``. Tests that expect silence assert
    the list stays empty; tests that expect a call read it back.
    """
    calls: list[tuple[str, str, str]] = []

    def fake_session_request(self, method, url, *args, **kwargs):
        calls.append(("requests", str(method).upper(), str(url)))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"success": true}'
        response.url = str(url)
        return response

    def fake_httpx_send(self, request, *args, **kwargs):
        calls.append(("httpx", request.method, str(request.url)))
        return httpx.Response(200, json={}, request=request)

    def fake_webhook_send(self, *args, **kwargs):
        calls.append(("slack", "POST", str(getattr(self, "url", ""))))
        return MagicMock(status_code=200, body="ok")

    monkeypatch.setattr(requests.sessions.Session, "request", fake_session_request)
    monkeypatch.setattr(httpx.Client, "send", fake_httpx_send)
    monkeypatch.setattr(WebhookClient, "send", fake_webhook_send)
    return calls


def _above_debug(records):
    return [r for r in records if r.get("log_level") not in ("debug", None)]


def _mock_user(email="lead@example.com"):
    user = MagicMock()
    user.id = "user-1"
    user.email = email
    user.name = "Ada Lovelace"
    user.organization_role = "Owner"
    return user


def _threads(thread_class):
    """Swap the ``threading`` module ``accounts.utils`` sees, leaving every
    other module's threads alone."""
    return patch("accounts.utils.threading", SimpleNamespace(Thread=thread_class))


class _SyncThread:
    """Stand-in for ``threading.Thread`` that runs the target on ``start``."""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None


# --------------------------------------------------------------------------
# HubSpot gate
# --------------------------------------------------------------------------


class TestHubspotIsConfigured:
    @override_settings(HUBSPOT_API_TOKEN="")
    def test_off_without_a_token(self):
        from accounts.utils import hubspot_is_configured

        assert hubspot_is_configured() is False
        assert hubspot_is_configured("HUBSPOT_UPDATE_URL") is False

    @override_settings(HUBSPOT_API_TOKEN="   ")
    def test_a_blank_token_counts_as_unset(self):
        from accounts.utils import hubspot_is_configured

        assert hubspot_is_configured() is False

    @override_settings(HUBSPOT_API_TOKEN=HUBSPOT_TOKEN)
    def test_on_with_a_token(self):
        from accounts.utils import hubspot_is_configured

        assert hubspot_is_configured() is True
        assert hubspot_is_configured("HUBSPOT_UPDATE_URL") is True

    @override_settings(HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL="")
    def test_an_empty_endpoint_turns_that_call_off(self):
        from accounts.utils import hubspot_is_configured

        assert hubspot_is_configured() is True
        assert hubspot_is_configured("HUBSPOT_UPDATE_URL") is False


# --------------------------------------------------------------------------
# Signup: HubSpot contact + Slack "new user" notification
# --------------------------------------------------------------------------


class TestSignupContactSync:
    @override_settings(HUBSPOT_API_TOKEN="")
    def test_no_request_and_no_noise_without_a_token(self, outbound_calls):
        from accounts.utils import send_hubspot_notification

        with (
            patch("accounts.utils.get_user_organization") as get_org,
            capture_logs() as records,
        ):
            assert send_hubspot_notification(_mock_user()) == (False, None)

        assert outbound_calls == []
        # Returns before it even resolves the org for the contact payload.
        get_org.assert_not_called()
        assert _above_debug(records) == []

    @override_settings(HUBSPOT_API_TOKEN=HUBSPOT_TOKEN)
    def test_creates_the_contact_with_a_token(self):
        from django.conf import settings

        from accounts.utils import send_hubspot_notification

        response = MagicMock(status_code=201)
        response.json.return_value = {"id": "1"}
        with (
            patch("accounts.utils.get_user_organization", return_value=None),
            patch("accounts.utils.requests.post", return_value=response) as post,
        ):
            assert send_hubspot_notification(_mock_user()) == (True, None)

        post.assert_called_once()
        assert post.call_args.args[0] == settings.HUBSPOT_URL
        headers = post.call_args.kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {HUBSPOT_TOKEN}"
        assert post.call_args.kwargs["json"]["properties"]["email"] == (
            "lead@example.com"
        )
        assert post.call_args.kwargs["timeout"] == 10


class TestSignupSlackNotification:
    @override_settings(SLACK_WEBHOOK_CHANNEL="")
    def test_no_webhook_client_without_a_channel(self, outbound_calls):
        from accounts.utils import send_slack_notification

        with (
            patch("accounts.utils.WebhookClient") as client,
            capture_logs() as records,
        ):
            send_slack_notification(_mock_user())

        client.assert_not_called()
        assert outbound_calls == []
        assert _above_debug(records) == []

    @override_settings(SLACK_WEBHOOK_CHANNEL="https://hooks.slack.test/T/B/X")
    def test_posts_with_a_channel(self):
        from accounts.utils import send_slack_notification

        with (
            patch("accounts.utils.get_user_organization", return_value=None),
            patch("accounts.utils.WebhookClient") as client,
        ):
            send_slack_notification(_mock_user())

        client.assert_called_once_with("https://hooks.slack.test/T/B/X", timeout=10)
        client.return_value.send.assert_called_once()


class TestPostRegistrationOnASelfHostedInstall:
    """Email and SSO signups run post-registration. A self-hosted install with
    ENV_TYPE=production must still stay off HubSpot and Slack."""

    @override_settings(
        HUBSPOT_API_TOKEN="", SLACK_WEBHOOK_CHANNEL="", ERROR_LOGS_WEBHOOK=""
    )
    @patch.dict("os.environ", {"ENV_TYPE": "production"})
    def test_no_hubspot_or_slack_call_without_keys(self, outbound_calls):
        from accounts.utils import _run_post_registration

        with (
            patch("accounts.models.User.objects.get", return_value=_mock_user()),
            patch("accounts.utils.send_signup_email") as signup_email,
            patch("accounts.utils.get_user_organization", return_value=None),
            patch("accounts.utils.WebhookClient") as slack_client,
            capture_logs() as records,
        ):
            _run_post_registration("user-1", "generated-password")

        signup_email.assert_called_once()
        slack_client.assert_not_called()
        assert outbound_calls == []
        assert _above_debug(records) == []

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN,
        SLACK_WEBHOOK_CHANNEL="https://hooks.slack.test/T/B/X",
    )
    @patch.dict("os.environ", {"ENV_TYPE": "production"})
    def test_both_fire_once_configured(self):
        from accounts.utils import _run_post_registration

        created = MagicMock(status_code=201)
        created.json.return_value = {"id": "1"}
        with (
            patch("accounts.models.User.objects.get", return_value=_mock_user()),
            patch("accounts.utils.send_signup_email"),
            patch("accounts.utils.get_user_organization", return_value=None),
            patch("accounts.utils.requests.post", return_value=created) as post,
            patch("accounts.utils.WebhookClient") as slack_client,
        ):
            _run_post_registration("user-1", "generated-password")

        post.assert_called_once()
        slack_client.return_value.send.assert_called_once()


# --------------------------------------------------------------------------
# Login: HubSpot "logged_in" flag
# --------------------------------------------------------------------------


class TestLoginHubspotUpdate:
    @override_settings(HUBSPOT_API_TOKEN="")
    def test_no_thread_no_request_no_noise_without_a_token(self, outbound_calls):
        from accounts.utils import record_hubspot_login

        thread = MagicMock()
        with _threads(thread), capture_logs() as records:
            assert record_hubspot_login(_mock_user()) is None

        thread.assert_not_called()
        assert outbound_calls == []
        assert _above_debug(records) == []

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL
    )
    def test_patches_the_contact_with_a_token(self):
        from accounts.utils import record_hubspot_login

        with patch("accounts.utils.requests.patch") as hubspot_patch:
            thread = record_hubspot_login(_mock_user("lead@example.com"))
            assert thread is not None
            thread.join(timeout=5)

        hubspot_patch.assert_called_once()
        assert hubspot_patch.call_args.args[0] == HUBSPOT_UPDATE_URL.format(
            "lead@example.com"
        )
        assert hubspot_patch.call_args.kwargs["json"] == {
            "properties": {"lead_type": "Owner", "logged_in": "Yes"}
        }
        assert hubspot_patch.call_args.kwargs["headers"]["Authorization"] == (
            f"Bearer {HUBSPOT_TOKEN}"
        )

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL
    )
    def test_a_slow_hubspot_does_not_hold_the_login(self):
        """The PATCH runs off the request path: the call returns while HubSpot
        is still answering."""
        from accounts.utils import record_hubspot_login

        release = threading.Event()

        def slow_patch(*args, **kwargs):
            release.wait(timeout=5)
            return MagicMock(status_code=200)

        with patch("accounts.utils.requests.patch", side_effect=slow_patch):
            started = time.monotonic()
            thread = record_hubspot_login(_mock_user())
            elapsed = time.monotonic() - started
            assert thread.is_alive()
            release.set()
            thread.join(timeout=5)

        assert elapsed < 1.0
        assert thread.daemon is True

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL
    )
    def test_an_unreachable_hubspot_is_logged_not_raised(self):
        from accounts.utils import record_hubspot_login

        with (
            patch(
                "accounts.utils.requests.patch",
                side_effect=requests.ConnectionError("no route to host"),
            ),
            _threads(_SyncThread),
            capture_logs() as records,
        ):
            record_hubspot_login(_mock_user())

        assert [r["event"] for r in records if r["log_level"] == "error"] == [
            "hubspot_login_update_failed"
        ]

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL
    )
    def test_never_raises_into_the_login(self):
        from accounts.utils import record_hubspot_login

        with _threads(MagicMock(side_effect=RuntimeError("can't start new thread"))):
            assert record_hubspot_login(_mock_user()) is None


class TestHubspotContactUrl:
    """The address is the contact id in the URL path. A valid one may hold
    ``?``, ``#`` or ``/``; unquoted, those end the path and point the PATCH
    at another contact."""

    @pytest.mark.parametrize(
        "email, contact",
        [
            ("lead@example.com", "lead@example.com"),
            ("12345?@evil.com", "12345%3F@evil.com"),
            ("12345#@evil.com", "12345%23@evil.com"),
            ("a/../12345@evil.com", "a%2F..%2F12345@evil.com"),
        ],
    )
    @override_settings(HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL)
    def test_quotes_the_address(self, email, contact):
        from accounts.utils import hubspot_contact_url

        assert hubspot_contact_url(email) == HUBSPOT_UPDATE_URL.format(contact)

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN, HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL
    )
    def test_the_login_patch_targets_the_quoted_contact(self):
        from accounts.utils import record_hubspot_login

        with patch("accounts.utils.requests.patch") as hubspot_patch:
            record_hubspot_login(_mock_user("12345?@evil.com")).join(timeout=5)

        url = hubspot_patch.call_args.args[0]
        assert requests.utils.urlparse(url).path == (
            "/crm/v3/objects/contacts/12345%3F@evil.com"
        )
        assert requests.utils.urlparse(url).query == "idProperty=email"


# --------------------------------------------------------------------------
# AWS and GCP Marketplace
# --------------------------------------------------------------------------

MARKETPLACE_ROUTES = {
    "aws-marketplace/verify-token/",
    "aws-marketplace/signup/",
    "aws-marketplace/launch-software/",
    "gcp-marketplace/verify-token/",
    "gcp-marketplace/signup/",
}


def _fresh(module):
    """``module`` executed again as a separate module, so a gate at import
    reads the patched ``is_oss`` without disturbing the loaded URLconf."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"_{module.__name__.replace('.', '_')}_under_test", module.__file__
    )
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    return fresh


def _routes(patterns):
    return {str(pattern.pattern) for pattern in patterns}


class TestMarketplaceRoutes:
    """Anonymous endpoints that call AWS and Google are Future AGI Cloud's."""

    def test_not_mounted_on_a_self_hosted_install(self):
        import accounts.urls

        with patch("tfc.ee_gating.is_oss", return_value=True):
            mounted = _routes(_fresh(accounts.urls).urlpatterns)

        assert not mounted & MARKETPLACE_ROUTES

    def test_mounted_off_oss(self):
        import accounts.urls

        with patch("tfc.ee_gating.is_oss", return_value=False):
            mounted = _routes(_fresh(accounts.urls).urlpatterns)

        assert mounted >= MARKETPLACE_ROUTES

    def test_a_self_hosted_api_schema_still_documents_them(self):
        """The checked-in OpenAPI contract is one surface for every edition."""
        import tfc.openapi_urls

        with patch("tfc.ee_gating.is_oss", return_value=True):
            documented = _fresh(tfc.openapi_urls).urlpatterns[-1]

        assert str(documented.pattern) == "accounts/"
        assert _routes(documented.url_patterns) == MARKETPLACE_ROUTES


class TestAWSMarketplaceService:
    @pytest.mark.parametrize(
        "key_id, secret", [("", ""), ("AKIAMARKETPLACE", ""), ("", "secret")]
    )
    def test_never_signs_with_the_default_credential_chain(
        self, monkeypatch, key_id, secret
    ):
        """Without its own keys boto3 would sign with the operator's Bedrock
        or S3 keys, or the instance role."""
        from accounts.services.aws_marketplace import (
            AWSMarketplaceNotConfigured,
            AWSMarketplaceService,
        )

        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAOPERATORBEDROCK")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "operator-secret")
        monkeypatch.setenv("AWS_MARKETPLACE_ACCESS_KEY_ID", key_id)
        monkeypatch.setenv("AWS_MARKETPLACE_SECRET_ACCESS_KEY", secret)
        with (
            patch("accounts.services.aws_marketplace.boto3.client") as client,
            pytest.raises(AWSMarketplaceNotConfigured),
        ):
            AWSMarketplaceService()

        client.assert_not_called()

    def test_signs_with_the_marketplace_keys(self, monkeypatch):
        from accounts.services.aws_marketplace import AWSMarketplaceService

        monkeypatch.setenv("AWS_MARKETPLACE_ACCESS_KEY_ID", "AKIAMARKETPLACE")
        monkeypatch.setenv("AWS_MARKETPLACE_SECRET_ACCESS_KEY", "marketplace-secret")
        with patch("accounts.services.aws_marketplace.boto3.client") as client:
            AWSMarketplaceService()

        assert client.call_count == 2
        for call in client.call_args_list:
            assert call.kwargs["aws_access_key_id"] == "AKIAMARKETPLACE"
            assert call.kwargs["aws_secret_access_key"] == "marketplace-secret"


# --------------------------------------------------------------------------
# reCAPTCHA
# --------------------------------------------------------------------------


class TestVerifyRecaptcha:
    def test_disabled_passes_without_calling_google(self, outbound_calls):
        from accounts.views.signup import verify_recaptcha

        with (
            patch("accounts.views.signup.RECAPTCHA_ENABLED", False),
            capture_logs() as records,
        ):
            assert verify_recaptcha("") is True

        assert outbound_calls == []
        assert _above_debug(records) == []

    def test_enabled_with_a_secret_calls_google(self, outbound_calls):
        from accounts.views.signup import verify_recaptcha

        with (
            patch("accounts.views.signup.RECAPTCHA_ENABLED", True),
            patch("accounts.views.signup.RECAPTCHA_SECRET_KEY", "secret"),
        ):
            assert verify_recaptcha("client-token") is True

        assert outbound_calls == [
            ("requests", "POST", "https://www.google.com/recaptcha/api/siteverify")
        ]

    def test_enabled_without_a_secret_fails_closed_without_a_call(self, outbound_calls):
        from accounts.views.signup import verify_recaptcha

        with (
            patch("accounts.views.signup.RECAPTCHA_ENABLED", True),
            patch("accounts.views.signup.RECAPTCHA_SECRET_KEY", ""),
        ):
            assert verify_recaptcha("client-token") is False

        assert outbound_calls == []


class TestRecaptchaDefault:
    """``RECAPTCHA_ENABLED`` when the operator leaves it unset."""

    @pytest.mark.parametrize(
        ("explicit", "env_type", "cloud", "secret", "expected"),
        [
            # Self-hosted, no key: never call Google, never block a login.
            (None, "production", "", "", False),
            ("", "prod", "", "", False),
            ("   ", "staging", "", "", False),
            # Self-hosted with a key: verify.
            (None, "production", "", "secret", True),
            # Managed cloud: verify, and fail closed if the key is missing.
            (None, "prod", "US", "", True),
            (None, "prod", "EU", "secret", True),
            # Local development: off.
            (None, "local", "US", "secret", False),
            (None, "development", "", "secret", False),
            # An explicit value always wins.
            ("true", "local", "", "", True),
            ("1", "production", "", "", True),
            ("false", "prod", "US", "secret", False),
            ("no", "prod", "US", "secret", False),
        ],
    )
    def test_default(self, explicit, env_type, cloud, secret, expected):
        from tfc.settings.settings import _recaptcha_enabled

        assert _recaptcha_enabled(explicit, env_type, cloud, secret) is expected


# --------------------------------------------------------------------------
# End to end on the auth path (needs the test database)
# --------------------------------------------------------------------------

NO_THIRD_PARTY_KEYS = {
    "HUBSPOT_API_TOKEN": "",
    "SLACK_WEBHOOK_CHANNEL": "",
    "ERROR_LOGS_WEBHOOK": "",
}


@pytest.fixture
def analytics_off():
    """Mixpanel and PostHog as a self-hosted install has them: no key."""
    from analytics.mixpanel_util import mixpanel_tracker
    from analytics.posthog_util import posthog_tracker

    with (
        patch.object(mixpanel_tracker, "mp", None),
        patch.object(posthog_tracker, "client", None),
        patch("accounts.views.signup.RECAPTCHA_ENABLED", False),
    ):
        yield


def _external_member(organization, email):
    """A member whose address takes the full login path (no test bypass)."""
    from accounts.models import User
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    member = User.objects.create_user(
        email=email,
        password="testpassword123",
        name="External Member",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
        is_active=True,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=member,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return member


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestAuthPathWithoutThirdPartyKeys:
    @override_settings(**NO_THIRD_PARTY_KEYS)
    def test_login_makes_no_outbound_call(
        self, api_client, organization, outbound_calls, analytics_off
    ):
        member = _external_member(organization, "ops@example.com")

        response = api_client.post(
            "/accounts/token/",
            {
                "email": member.email,
                "password": "testpassword123",
                "recaptcha_response": "",
            },
            format="json",
            SERVER_NAME="example.com",
        )

        assert response.status_code == 200, response.content
        assert response.json()["access"]
        assert "new_org" in response.json()
        assert outbound_calls == []

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN,
        HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL,
        SLACK_WEBHOOK_CHANNEL="",
        ERROR_LOGS_WEBHOOK="",
    )
    def test_login_marks_the_hubspot_contact_once_configured(
        self, api_client, organization, analytics_off
    ):
        member = _external_member(organization, "lead@example.com")

        with (
            _threads(_SyncThread),
            patch("accounts.utils.requests.patch") as hubspot_patch,
        ):
            response = api_client.post(
                "/accounts/token/",
                {
                    "email": member.email,
                    "password": "testpassword123",
                    "recaptcha_response": "",
                },
                format="json",
                SERVER_NAME="example.com",
            )

        assert response.status_code == 200, response.content
        hubspot_patch.assert_called_once()
        assert hubspot_patch.call_args.args[0] == HUBSPOT_UPDATE_URL.format(
            "lead@example.com"
        )

    @override_settings(
        HUBSPOT_API_TOKEN=HUBSPOT_TOKEN,
        HUBSPOT_UPDATE_URL=HUBSPOT_UPDATE_URL,
        SLACK_WEBHOOK_CHANNEL="",
        ERROR_LOGS_WEBHOOK="",
    )
    def test_login_survives_hubspot_being_down(
        self, api_client, organization, analytics_off
    ):
        member = _external_member(organization, "down@example.com")

        with (
            _threads(_SyncThread),
            patch(
                "accounts.utils.requests.patch",
                side_effect=requests.ConnectionError("unreachable"),
            ),
        ):
            response = api_client.post(
                "/accounts/token/",
                {
                    "email": member.email,
                    "password": "testpassword123",
                    "recaptcha_response": "",
                },
                format="json",
                SERVER_NAME="example.com",
            )

        assert response.status_code == 200, response.content
        assert response.json()["access"]

    @override_settings(**NO_THIRD_PARTY_KEYS)
    def test_create_user_command_makes_no_outbound_call(
        self, outbound_calls, analytics_off
    ):
        """``bin/install`` creates the first account with this command."""
        from accounts.models import User

        with (
            patch.dict("os.environ", {"ALLOW_ANY_EMAIL": "true"}),
            patch("accounts.utils.process_post_registration") as post_registration,
        ):
            call_command(
                "create_user",
                "--email",
                "first-owner@example.com",
                "--name",
                "First Owner",
                "--password",
                "Kx7!mountain-lantern",
            )

        assert User.objects.filter(email="first-owner@example.com").exists()
        # A chosen password means no welcome mail and no lead sync.
        post_registration.assert_not_called()
        assert outbound_calls == []

    @override_settings(**NO_THIRD_PARTY_KEYS)
    def test_oss_signup_makes_no_outbound_call(
        self, api_client, outbound_calls, analytics_off
    ):
        with (
            patch.dict("os.environ", {"ALLOW_ANY_EMAIL": "true"}),
            patch("accounts.views.signup.is_oss", return_value=True),
        ):
            response = api_client.post(
                "/accounts/signup/",
                {
                    "email": "oss-owner@example.com",
                    "full_name": "OSS Owner",
                    "company_name": "",
                    "recaptcha_response": "",
                    "allow_email": True,
                    "password": "Kx7!mountain-lantern",
                },
                format="json",
            )

        assert response.status_code == 200, response.content
        assert outbound_calls == []
