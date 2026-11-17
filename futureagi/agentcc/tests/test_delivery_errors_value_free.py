"""ALERT-ACCESS-1 round 2: stored delivery errors carry no provider or receiver text.

An email alert's last_error and a webhook event's last_error are read by every
org Member. Round 1 kept the provider's, the mail server's or the receiver's
own text in them (an SMTP or provider rejection, an exception's message, a
receiver's response body, requests' InvalidHeader message), and any of those
can quote a saved password, API key or header value. Now a failed delivery
stores a fixed message (a webhook's HTTP status code included, which is
already in last_response_code), the raw text goes to the server log only, and
a read shows a raw value saved earlier as a fixed message.

Synthetic data only: DNS answers are stubbed, the transports are faked or refuse
in-process, and no mail or request leaves the test.
"""

import smtplib
import socket
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from cryptography.fernet import Fernet
from django.utils import timezone

from agentcc.models.email_alert import AgentccEmailAlert
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.alert_dispatcher import dispatch_pending
from agentcc.services.email_sender import NO_RECIPIENTS, SEND_FAILED
from agentcc.services.webhook_delivery import deliver_webhook_events
from agentcc.tests.r7_console_a_support import ClientFactory, user_with_level
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)
from integrations.services.credentials import CredentialManager
from tfc.constants.levels import Level

ALERTS = "/agentcc/email-alerts/"
HOOKS = "/agentcc/webhooks/"
EVENTS = "/agentcc/webhook-events/"
# Test values, not real credentials; no hex letters, so no UUID in a response
# can contain a slice of them by chance.
PASSWORD = "zyxwvutsrqpo"
API_KEY = "re_zqxjkvwmzqxjkvwm"
TOKEN = "Bearer zqxjkvwm-receiver-token"
SMTP_CONFIG = {
    "host": "smtp.public.test",
    "port": 587,
    "username": "alerts-user",
    "password": PASSWORD,
    "from_email": "alerts@example.com",
    "use_tls": True,
}
HOOK_URL = "https://hooks.example.test/in"
DNS = {"smtp.public.test": ["93.184.216.34"], "hooks.example.test": ["93.184.216.35"]}


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def dns(provider_dns):
    # Other names (localhost, the test services) go to the real resolver.
    return provider_dns(DNS, passthrough=True)


@pytest.fixture
def clients():
    factory = ClientFactory()
    yield factory
    factory.close()


@pytest.fixture
def member_client(organization, workspace, clients):
    member, member_workspace = user_with_level(organization, Level.MEMBER)
    return clients(member, member_workspace)


def _alert(organization, provider="smtp", config=None, **fields):
    """An active alert with one pending event, due now (no cooldown)."""
    at = (timezone.now() - timedelta(seconds=30)).isoformat()
    return AgentccEmailAlert.no_workspace_objects.create(
        organization=organization,
        name=fields.pop("name", "errors-alert"),
        recipients=["ops@example.com"],
        events=["error.occurred"],
        provider=provider,
        cooldown_minutes=0,
        is_active=True,
        encrypted_config=CredentialManager.encrypt(
            dict(SMTP_CONFIG) if config is None else config
        ),
        dispatch_state={
            "pending": {
                "error.occurred": {
                    "count": 1,
                    "first_at": at,
                    "last_at": at,
                    "context": {"Request": "errors-1"},
                }
            },
            "budget_keys": {},
        },
        **fields,
    )


def _dispatch(organization):
    return dispatch_pending(org_id=str(organization.id))


# --- email alerts: what a failed send stores -----------------------------------------


@pytest.mark.django_db
class TestEmailAlertLastErrorIsValueFree:
    def test_an_smtp_error_quoting_the_password_is_not_stored(self, organization):
        alert = _alert(organization)
        failure = smtplib.SMTPAuthenticationError(
            535, f"5.7.8 rejected alerts-user:{PASSWORD}".encode()
        )

        with patch("agentcc.services.email_sender._send_via_smtp", side_effect=failure):
            assert _dispatch(organization)["failed"] == 1

        alert.refresh_from_db()
        assert alert.last_error and alert.last_error_at is not None
        assert PASSWORD not in alert.last_error
        assert "535" not in alert.last_error
        from agentcc.services.email_sender import SMTP_AUTH_FAILED

        assert alert.last_error == SMTP_AUTH_FAILED

    def test_a_provider_body_quoting_the_api_key_is_kept_out(self, organization):
        alert = _alert(
            organization,
            provider="sendgrid",
            config={"api_key": API_KEY, "from_email": "alerts@example.com"},
        )
        rejected = MagicMock(
            status_code=401, text=f"zq-provider-body: key {API_KEY} is revoked"
        )

        with (
            patch("agentcc.services.email_sender.requests.post", return_value=rejected),
            patch("agentcc.services.alert_dispatcher.logger") as logger,
        ):
            _dispatch(organization)

        alert.refresh_from_db()
        assert alert.last_error
        assert API_KEY not in alert.last_error
        assert "zq-provider-body" not in alert.last_error
        # The provider's own text stays with operators, in the server log.
        assert "zq-provider-body" in repr(logger.warning.call_args_list)
        from agentcc.services.email_sender import PROVIDER_REFUSED

        assert alert.last_error == PROVIDER_REFUSED

    def test_a_mail_server_rejection_text_is_not_stored(self, organization):
        alert = _alert(organization)

        with patch(
            "agentcc.services.email_sender._send_via_smtp",
            return_value={
                "success": False,
                "error": f"550 5.1.1 alerts-user:{PASSWORD} unknown",
            },
        ):
            _dispatch(organization)

        alert.refresh_from_db()
        assert PASSWORD not in alert.last_error
        assert alert.last_error == SEND_FAILED

    def test_an_unexpected_exception_text_is_not_stored(self, organization):
        alert = _alert(organization)

        with patch(
            "agentcc.services.email_sender.send_alert_digest",
            side_effect=RuntimeError(f"boom {PASSWORD}"),
        ):
            _dispatch(organization)

        alert.refresh_from_db()
        assert PASSWORD not in alert.last_error
        assert alert.last_error == SEND_FAILED

    def test_a_refused_connection_is_named_without_its_address(self, organization):
        alert = _alert(organization)
        dialed = []

        def create_connection(address, *args, **kwargs):
            dialed.append(address)
            raise ConnectionRefusedError(111, f"refused by {address[0]}")

        with patch("socket.create_connection", create_connection):
            _dispatch(organization)

        assert dialed == [("93.184.216.34", 587)]
        alert.refresh_from_db()
        assert "93.184" not in alert.last_error
        from agentcc.services.email_sender import SMTP_CONNECT_FAILED

        assert alert.last_error == SMTP_CONNECT_FAILED


# --- email alerts: what a read shows ---------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEmailAlertReadsShowAFixedLastError:
    def test_a_raw_error_saved_before_reads_as_a_fixed_message(
        self, auth_client, member_client, organization
    ):
        alert = _alert(
            organization,
            last_error=f"SMTPAuthenticationError: (535, b'5.7.8 {PASSWORD}')",
            last_error_at=timezone.now(),
        )

        for client in (auth_client, member_client):
            for response in (client.get(ALERTS), client.get(f"{ALERTS}{alert.id}/")):
                assert response.status_code == 200, response.content
                body = response.content.decode()
                assert PASSWORD not in body and "SMTPAuthentication" not in body
            shown = client.get(f"{ALERTS}{alert.id}/").json()["result"]
            assert shown["last_error"] == SEND_FAILED
            assert shown["last_error_at"] is not None

    def test_a_fixed_message_reads_as_saved(self, member_client, organization):
        alert = _alert(
            organization, last_error=NO_RECIPIENTS, last_error_at=timezone.now()
        )

        shown = member_client.get(f"{ALERTS}{alert.id}/").json()["result"]

        assert shown["last_error"] == NO_RECIPIENTS

    def test_no_error_reads_as_empty(self, member_client, organization):
        alert = _alert(organization)

        shown = member_client.get(f"{ALERTS}{alert.id}/").json()["result"]

        assert shown["last_error"] == ""
        assert shown["last_error_at"] is None

    def test_the_send_test_names_the_failure_without_its_text(
        self, auth_client, organization
    ):
        alert = _alert(organization)
        failure = smtplib.SMTPAuthenticationError(535, b"5.7.8 zq-banner")

        with patch("agentcc.services.email_sender._send_via_smtp", side_effect=failure):
            response = auth_client.post(f"{ALERTS}{alert.id}/test/", {}, format="json")

        assert response.status_code == 400, response.content
        assert "zq-banner" not in response.content.decode()
        from agentcc.services.email_sender import SMTP_AUTH_FAILED

        assert response.json()["message"] == SMTP_AUTH_FAILED


# --- webhook events ----------------------------------------------------------------------


def _hook(organization, headers=None):
    return make_webhook(
        manager=AgentccWebhook.no_workspace_objects,
        organization=organization,
        name="errors-hook",
        url=HOOK_URL,
        events=["error.occurred"],
        headers={"Authorization": TOKEN} if headers is None else headers,
        secret="whsec-zqxjkvwm",
    )


def _event(organization, hook, **fields):
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=organization,
        webhook=hook,
        event_type="error.occurred",
        payload={"event": "error.occurred", "request_id": fields.pop("rid", "e-1")},
        **fields,
    )


@pytest.fixture
def receiver():
    """The webhook session, faked: set .post's return value or side effect."""
    session = MagicMock()
    with (
        patch("agentcc.services.webhook_delivery.ensure_public_http_url"),
        patch(
            "agentcc.services.webhook_delivery.build_ssrf_safe_session",
            return_value=session,
        ),
    ):
        yield session


@pytest.fixture
def refuse_connects():
    """Every socket connect is recorded and refused: nothing leaves the test."""
    connects = []

    def connect(sock, address):
        connects.append(address)
        raise OSError("synthetic: no network in this test")

    with patch.object(socket.socket, "connect", connect):
        yield connects


@pytest.mark.integration
@pytest.mark.django_db
class TestWebhookEventLastErrorIsValueFree:
    def test_a_receiver_answer_is_not_stored(self, organization, receiver):
        receiver.post.return_value = MagicMock(
            status_code=500, text=f"echo Authorization: {TOKEN}"
        )
        event = _event(organization, _hook(organization))

        deliver_webhook_events(event_ids=[event.id])

        event.refresh_from_db()
        assert event.last_response_code == 500
        assert event.last_error == "HTTP 500"

    def test_a_connection_error_text_is_not_stored(self, organization, receiver):
        receiver.post.side_effect = requests.ConnectionError(
            f"HTTPSConnectionPool(host='hooks.example.test', port=443): {TOKEN}"
        )
        event = _event(organization, _hook(organization))

        deliver_webhook_events(event_ids=[event.id])

        event.refresh_from_db()
        assert event.attempts == 1
        assert event.last_error and "zqxjkvwm" not in event.last_error
        assert "HTTPSConnectionPool" not in event.last_error
        from agentcc.services.webhook_delivery import WEBHOOK_UNREACHABLE

        assert event.last_error == WEBHOOK_UNREACHABLE

    def test_an_invalid_header_value_is_not_quoted(self, organization, refuse_connects):
        # requests refuses the value before any connect, and its message
        # quotes the value.
        hook = _hook(organization, headers={"Authorization": f"{TOKEN}\r\nX-In: 1"})
        event = _event(organization, hook)

        deliver_webhook_events(event_ids=[event.id])

        assert refuse_connects == []
        event.refresh_from_db()
        assert event.attempts == 1
        assert event.last_error and "zqxjkvwm" not in event.last_error

    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    def test_the_send_test_answers_and_records_a_fixed_error(
        self, session_builder, auth_client, organization
    ):
        hook = _hook(organization)
        session = MagicMock()
        session.post.side_effect = requests.ConnectionError(f"pool says {TOKEN}")
        session_builder.return_value = session

        response = auth_client.post(f"{HOOKS}{hook.id}/test/")

        assert response.status_code == 200, response.content
        assert "zqxjkvwm" not in response.content.decode()
        result = response.json()["result"]
        assert result["success"] is False and result["error"]
        event = AgentccWebhookEvent.objects.get(webhook=hook, event_type="test")
        assert event.last_error == result["error"]

    @patch("agentcc.views.webhook_outbound.build_ssrf_safe_session")
    def test_the_send_test_does_not_quote_an_invalid_header(
        self, session_builder, auth_client, organization
    ):
        hook = _hook(organization, headers={"Authorization": f"{TOKEN}\r\nX-In: 1"})
        session = MagicMock()
        session.post.side_effect = requests.exceptions.InvalidHeader(
            f"Invalid return character(s) in header value: {TOKEN!r}"
        )
        session_builder.return_value = session

        response = auth_client.post(f"{HOOKS}{hook.id}/test/")

        assert "zqxjkvwm" not in response.content.decode()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestWebhookEventReadsShowAFixedLastError:
    def test_raw_errors_saved_before_read_as_fixed_messages(
        self, auth_client, member_client, organization
    ):
        hook = _hook(organization)
        answered = _event(
            organization,
            hook,
            rid="answered",
            status=AgentccWebhookEvent.FAILED,
            last_response_code=500,
            last_error=f"HTTP 500: echo Authorization: {TOKEN}",
        )
        dropped = _event(
            organization,
            hook,
            rid="dropped",
            status=AgentccWebhookEvent.FAILED,
            last_error=f"HTTPSConnectionPool(host='hooks.example.test'): {TOKEN}",
        )
        fine = _event(
            organization, hook, rid="fine", status=AgentccWebhookEvent.DELIVERED
        )

        for client in (auth_client, member_client):
            response = client.get(EVENTS)
            assert response.status_code == 200, response.content
            assert "zqxjkvwm" not in response.content.decode()
            shown = {row["id"]: row["last_error"] for row in response.json()["result"]}
            assert shown[str(answered.id)] == "HTTP 500"
            assert shown[str(dropped.id)]
            assert "HTTPSConnectionPool" not in shown[str(dropped.id)]
            assert shown[str(fine.id)] == ""

            one = client.get(f"{EVENTS}{answered.id}/")
            assert one.status_code == 200, one.content
            assert one.json()["result"]["last_error"] == "HTTP 500"
