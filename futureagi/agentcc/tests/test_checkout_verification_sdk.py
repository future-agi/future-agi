"""The landing page's Stripe read through the real stripe SDK (MUST-FIX G, R6).

read_checkout_session is the one place FA reads a Checkout Session. These
tests drive the installed stripe SDK end to end (StripeClient, its v1
namespace, its requestor and error mapping) with an in-test HTTPClient that
answers canned responses: no network, no database. They pin the SDK surface
the read relies on (the client arguments, one GET, no retries, a timeout) and
that every failure becomes a fixed, value-free refusal.

The key, session ids and markers are synthetic and generated at runtime;
failures report fixed messages, never a value. The SDK's own ``stripe``
logger would record the request URL (INFO) and body (DEBUG); the read
installs a source filter before the SDK call. Configured console/file and
telemetry-boundary coverage is in test_checkout_sdk_sinks.
"""

import json
import logging
import secrets
from urllib.parse import urlsplit

import pytest
import stripe

from agentcc.services import checkout_verification
from agentcc.services.checkout_verification import (
    READ_TIMEOUT_SECONDS,
    CheckoutReadRefused,
    CheckoutSessionState,
    read_checkout_session,
)

pytestmark = pytest.mark.unit

FA_LOGGER = checkout_verification.__name__


def _require(condition, message):
    # A fixed message only: never a key, a session id or a body.
    if not condition:
        pytest.fail(message, pytrace=False)


class FakeStripeHTTP(stripe.HTTPClient):
    """Answers each request with the next canned (body, status, headers) or
    raises it; records every call. Never opens a connection."""

    name = "fi-test-canned"

    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, headers, post_data=None, **kwargs):
        self.calls.append((method, url, dict(headers or {}), post_data))
        if not self.responses:
            raise AssertionError("an unexpected extra request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def request_stream(self, method, url, headers, post_data=None, **kwargs):
        raise AssertionError("the read never streams")

    def close(self):
        self.closed = True


def _session():
    return "cs_test_" + secrets.token_hex(16)


def _key():
    return "sk_test_SYNTHETIC_NOT_A_CREDENTIAL_" + secrets.token_hex(8)


def _body(session_id, **fields):
    return json.dumps(
        {
            "id": session_id,
            "object": "checkout.session",
            "mode": "payment",
            "status": "complete",
            "payment_status": "paid",
            "livemode": False,
            "currency": "usd",
            "amount_total": 2500,
            "customer_details": {"email": "buyer-" + secrets.token_hex(4) + "@x.test"},
            **fields,
        }
    )


def _ok(body):
    return (body, 200, {"request-id": "req_" + secrets.token_hex(8)})


@pytest.fixture
def stripe_key(settings):
    key = _key()
    settings.STRIPE_SECRET_KEY = key
    return key


@pytest.fixture
def http(monkeypatch):
    """Installs a canned HTTP client as the read's client factory."""

    def install(*responses):
        fake = FakeStripeHTTP(*responses)
        monkeypatch.setattr(checkout_verification, "_http_client", lambda: fake)
        return fake

    return install


# --- T4.1 ---------------------------------------------------------------------


def test_real_sdk_reads_session_through_fake_http_client(stripe_key, http, caplog):
    caplog.set_level(logging.DEBUG)
    session = _session()
    fake = http(_ok(_body(session)))

    state = read_checkout_session(session)

    assert state == CheckoutSessionState(
        id=state.id,
        mode="payment",
        status="complete",
        payment_status="paid",
        livemode=False,
        currency="usd",
        amount_total=2500,
    )
    _require(state.id == session, "the parsed id is not the session's")
    assert len(fake.calls) == 1
    method, url, headers, post_data = fake.calls[0]
    assert method.lower() == "get"
    _require(
        urlsplit(url).path == f"/v1/checkout/sessions/{session}",
        "the read is not GET /v1/checkout/sessions/<id>",
    )
    assert post_data is None
    _require(
        headers.get("Authorization") == f"Bearer {stripe_key}",
        "the read does not authenticate with STRIPE_SECRET_KEY",
    )
    assert fake.closed is True
    _require(
        not any(stripe_key in str(vars(r)) for r in caplog.records),
        "a log record carries the key",
    )
    assert [r for r in caplog.records if r.name.startswith(FA_LOGGER)] == []


def test_production_http_client_has_the_timeout():
    client = checkout_verification._http_client()
    assert isinstance(client, stripe.RequestsClient)
    assert READ_TIMEOUT_SECONDS == 5
    assert client._timeout == READ_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "fields, parsed",
    [
        ({"amount_total": "2500"}, {"amount_total": None}),
        ({"amount_total": True}, {"amount_total": None}),
        ({"livemode": "false"}, {"livemode": None}),
        ({"payment_status": None}, {"payment_status": None}),
        ({"currency": 840}, {"currency": None}),
    ],
    ids=["amount-text", "amount-bool", "livemode-text", "status-null", "currency-int"],
)
def test_unexpected_field_types_parse_as_absent(stripe_key, http, fields, parsed):
    session = _session()
    http(_ok(_body(session, **fields)))

    state = read_checkout_session(session)

    for name, value in parsed.items():
        assert getattr(state, name) == value, name


# --- T4.2 ---------------------------------------------------------------------


def _error_body(session_id, marker):
    return json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "message": f"No such checkout.session: '{session_id}' {marker}",
                "param": "session",
            }
        }
    )


@pytest.mark.parametrize(
    "answer, exception_type",
    [
        ("not-found", "InvalidRequestError"),
        ("server-error", "APIError"),
        ("conflict", "APIError"),
        ("timeout", "APIConnectionError"),
        ("not-json", "APIError"),
        ("unauthorized", "AuthenticationError"),
    ],
)
def test_sdk_errors_become_value_free_refusals(
    stripe_key, http, caplog, answer, exception_type
):
    caplog.set_level(logging.DEBUG)
    session = _session()
    marker = "marker-" + secrets.token_hex(8)
    headers = {"request-id": "req_" + secrets.token_hex(8)}
    response = {
        "not-found": (_error_body(session, marker), 404, headers),
        "server-error": (_error_body(session, marker), 500, headers),
        "conflict": (_error_body(session, marker), 409, headers),
        "timeout": stripe.APIConnectionError(
            f"timed out {session} {marker}", should_retry=True
        ),
        "not-json": (f"<html>{session} {marker}</html>", 200, headers),
        "unauthorized": (_error_body(session, marker), 401, headers),
    }[answer]
    fake = http(response)

    with pytest.raises(CheckoutReadRefused) as raised:
        read_checkout_session(session)

    refused = raised.value
    assert refused.code == "stripe_read_failed"
    assert refused.exception_type == exception_type
    # One request: retryable answers (409, 5xx, connection) are not retried.
    assert len(fake.calls) == 1
    assert fake.closed is True
    text = f"{refused} {refused!r} {refused.args} {vars(refused)}"
    for value, what in ((stripe_key, "key"), (session, "session id"), (marker, "body")):
        _require(value not in text, f"the refusal carries the {what}")
    _require(refused.__cause__ is None, "the refusal chains the SDK error")
    _require(refused.__context__ is None, "the refusal carries the SDK error")
    _require(
        not any(stripe_key in str(vars(r)) for r in caplog.records),
        "a log record carries the key",
    )
    assert [r for r in caplog.records if r.name.startswith(FA_LOGGER)] == []


@pytest.mark.parametrize(
    "configured", [None, "", "   "], ids=["unset", "empty", "spaces"]
)
def test_no_stripe_key_is_not_configured_without_http(
    settings, monkeypatch, configured
):
    settings.STRIPE_SECRET_KEY = configured
    factory_calls = []
    monkeypatch.setattr(
        checkout_verification,
        "_http_client",
        lambda: factory_calls.append(1) or FakeStripeHTTP(),
    )

    with pytest.raises(CheckoutReadRefused) as raised:
        read_checkout_session(_session())

    assert raised.value.code == "stripe_not_configured"
    assert raised.value.exception_type is None
    assert factory_calls == []
