"""Webhook attempts use the saved URL and record the endpoint's own answer.

Redirects are never followed (a followed 301/302/303 would resend the POST as
a GET without its body, and send the saved headers and signature to another
host). A 3xx answer records one failure with a fixed reason and is not
retried; the receiver's Location never reaches a response or a read.

Use the real Requests session, redirect handling and pinned adapter. Only the
adapter's underlying transport is replaced; endpoint DNS is synthetic and
external sockets are refused. Persistence and the authenticated test action
need the Django DB.
"""

import hashlib
import hmac
import ipaddress
import json
import socket

import pytest
import requests
from requests.adapters import HTTPAdapter

from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.webhook_delivery import deliver_webhook_events, retry_delay
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

SAVED_URL = "https://saved.example.com/events"
SIGNING_KEY = "webhook-test-signing-key"
RECEIVER_TOKEN = "test-receiver-token"
# The fixed reason a redirect answer records (webhook_delivery).
REDIRECT_REASON = "The webhook URL answered with a redirect. Save the final URL."
REDIRECT_CODES = [301, 302, 303, 307, 308]
# A Location value that must never reach a response, an event or a read.
PLANTED_LOCATION = "https://planted-location-7c1e.example.net/next?token=do-not-show"
REDIRECTS = [
    pytest.param("http://saved.example.com/next", id="http-same-host"),
    pytest.param("http://next.example.com/next", id="http-other-host"),
    pytest.param("https://next.example.com/next", id="https-other-host"),
    pytest.param("/next", id="relative"),
]
pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def endpoint(monkeypatch, provider_dns):
    provider_dns(
        {
            "saved.example.com": ["93.184.216.34"],
            "next.example.com": ["93.184.216.35"],
        }
    )

    resolve = socket.getaddrinfo
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def require_loopback(host):
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("unexpected external network access in webhook test")

    def local_resolve(host, *args, **kwargs):
        require_loopback(host)
        return resolve(host, *args, **kwargs)

    def local_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            require_loopback(address[0])
        return connect(sock, address)

    def local_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            require_loopback(address[0])
        return connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *args: None)

    def install(status_code, location=None):
        sent = []

        def send(adapter, request, **kwargs):
            sent.append(request.copy())
            response = requests.Response()
            response.request = request
            response.url = request.url
            response._content = b""
            # A followed Location would answer 200, so an accidental second
            # POST is visible both in the request count and in the saved result.
            response.status_code = status_code if len(sent) == 1 else 200
            if len(sent) == 1 and location is not None:
                response.headers["Location"] = location
            return response

        monkeypatch.setattr(HTTPAdapter, "send", send)
        return sent

    return install


@pytest.fixture
def webhook(organization):
    return make_webhook(
        manager=AgentccWebhook.no_workspace_objects,
        organization=organization,
        name="redirect-answer",
        url=SAVED_URL,
        events=["request.completed"],
        secret=SIGNING_KEY,
        headers={"X-Receiver-Token": RECEIVER_TOKEN},
    )


def _event(webhook, *, max_attempts=5):
    return AgentccWebhookEvent.no_workspace_objects.create(
        organization=webhook.organization,
        webhook=webhook,
        event_type="request.completed",
        payload={"event": "request.completed", "request_id": "redirect-test"},
        max_attempts=max_attempts,
    )


def _assert_one_post(sent, payload):
    assert len(sent) == 1, [(request.method, request.url) for request in sent]
    request = sent[0]
    assert (request.method, request.url) == ("POST", SAVED_URL)
    assert json.loads(request.body) == payload
    assert request.headers["X-Receiver-Token"] == RECEIVER_TOKEN
    raw = request.body.encode() if isinstance(request.body, str) else request.body
    signature = hmac.new(SIGNING_KEY.encode(), raw, hashlib.sha256).hexdigest()
    assert request.headers["X-Agentcc-Signature"] == f"sha256={signature}"


@pytest.mark.parametrize("status_code", REDIRECT_CODES)
@pytest.mark.parametrize("location", REDIRECTS)
def test_delivery_records_redirect_once_with_its_reason(
    webhook, endpoint, status_code, location
):
    sent = endpoint(status_code, location)
    event = _event(webhook)

    result = deliver_webhook_events(event_ids=[event.id])

    _assert_one_post(sent, event.payload)
    assert result == {"delivered": 0, "failed": 1}
    event.refresh_from_db()
    assert event.status == AgentccWebhookEvent.FAILED
    assert event.last_response_code == status_code
    assert event.last_error == REDIRECT_REASON
    assert event.attempts == 1
    assert event.last_attempt_at is not None
    assert event.next_retry_at is None


@pytest.mark.parametrize("status_code", [301, 308])
def test_redirected_delivery_is_not_retried(webhook, endpoint, status_code):
    sent = endpoint(status_code, "http://next.example.com/next")
    event = _event(webhook, max_attempts=5)
    deliver_webhook_events(event_ids=[event.id])

    # A later delivery run, as the schedule makes, picks nothing up.
    result = deliver_webhook_events()

    assert result == {"delivered": 0, "failed": 0}
    assert len(sent) == 1
    event.refresh_from_db()
    assert event.status == AgentccWebhookEvent.FAILED
    assert event.attempts == 1
    assert event.next_retry_at is None


@pytest.mark.parametrize("status_code", REDIRECT_CODES)
@pytest.mark.parametrize("location", REDIRECTS)
def test_test_send_returns_the_redirect_reason(
    auth_client, webhook, endpoint, status_code, location
):
    sent = endpoint(status_code, location)

    response = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")

    assert response.status_code == 200, response.content
    assert response.json()["result"] == {
        "success": False,
        "status_code": status_code,
        "error": REDIRECT_REASON,
    }
    event = AgentccWebhookEvent.no_workspace_objects.get(webhook=webhook)
    _assert_one_post(sent, event.payload)
    assert event.event_type == "test"
    assert event.status == AgentccWebhookEvent.FAILED
    assert event.last_response_code == status_code
    assert event.last_error == REDIRECT_REASON
    assert event.attempts == 1
    assert event.last_attempt_at is not None


def test_events_read_shows_the_reason_and_never_the_location(
    auth_client, webhook, endpoint
):
    endpoint(301, PLANTED_LOCATION)
    test = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")
    # A fresh receiver: the fixture answers 200 after its first request.
    endpoint(301, PLANTED_LOCATION)
    event = _event(webhook)
    deliver_webhook_events(event_ids=[event.id])

    read = auth_client.get(f"/agentcc/webhook-events/?webhook_id={webhook.id}")

    assert read.status_code == 200, read.content
    rows = read.json()["result"]
    assert len(rows) == 2
    assert {row["last_error"] for row in rows} == {REDIRECT_REASON}
    for content in (test.content, read.content):
        assert b"planted-location-7c1e" not in content
        assert b"do-not-show" not in content
    for row in AgentccWebhookEvent.no_workspace_objects.filter(webhook=webhook):
        assert "planted-location-7c1e" not in row.last_error


@pytest.mark.parametrize("status_code", [404, 500])
def test_other_answers_keep_their_status_and_retry(webhook, endpoint, status_code):
    sent = endpoint(status_code)
    event = _event(webhook)

    result = deliver_webhook_events(event_ids=[event.id])

    _assert_one_post(sent, event.payload)
    assert result == {"delivered": 0, "failed": 1}
    event.refresh_from_db()
    assert event.status == AgentccWebhookEvent.PENDING
    assert event.last_response_code == status_code
    assert event.last_error == f"HTTP {status_code}"
    assert event.attempts == 1
    assert event.next_retry_at == event.last_attempt_at + retry_delay(1)


@pytest.mark.parametrize("status_code", [200, 204])
def test_delivery_accepts_plain_2xx(webhook, endpoint, status_code):
    sent = endpoint(status_code)
    event = _event(webhook)

    result = deliver_webhook_events(event_ids=[event.id])

    _assert_one_post(sent, event.payload)
    assert result == {"delivered": 1, "failed": 0}
    event.refresh_from_db()
    assert event.status == AgentccWebhookEvent.DELIVERED
    assert event.last_response_code == status_code
    assert event.attempts == 1
    assert event.last_error == ""
    assert event.next_retry_at is None


@pytest.mark.parametrize("status_code", [200, 204])
def test_test_send_accepts_plain_2xx(auth_client, webhook, endpoint, status_code):
    sent = endpoint(status_code)

    response = auth_client.post(f"/agentcc/webhooks/{webhook.id}/test/")

    assert response.status_code == 200, response.content
    assert response.json()["result"] == {"success": True, "status_code": status_code}
    event = AgentccWebhookEvent.no_workspace_objects.get(webhook=webhook)
    _assert_one_post(sent, event.payload)
    assert event.event_type == "test"
    assert event.status == AgentccWebhookEvent.DELIVERED
    assert event.last_response_code == status_code
    assert event.attempts == 1
