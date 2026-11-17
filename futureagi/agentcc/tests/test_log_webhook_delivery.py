"""How the log webhook answers a delivery, which decides what the gateway keeps.

The gateway keeps a batch and delivers it again on every answer but a 2xx and a
malformed batch (400 invalid_log_batch). So a refused credential is 401, an
unconfigured webhook 503, and a server error 500, never 400. A signed delivery
(X-Webhook-Timestamp, X-Webhook-Signature over "<timestamp>.<body>") is judged
on its signature alone, inside a 300 s window; the static X-Webhook-Secret
header alone is still accepted this release, and counted. No error body carries
the secret, a signature or anything from the delivery.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

URL = "/agentcc/webhook/logs/"
INGEST = "agentcc.views.webhook.ingest_request_logs"
SECRET = "spool-webhook-secret-test-value"  # a test value, not a credential
BODY_MARKER = "delivery-body-marker-7f3a"


@pytest.fixture
def client():
    with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET):
        yield APIClient()


def _body(logs=None):
    if logs is None:
        logs = [{"request_id": "req_sig_1", "model": BODY_MARKER, "metadata": {}}]
    return json.dumps({"logs": logs}).encode()


def _signature(ts, body, secret=SECRET):
    return hmac.new(
        secret.encode(), ts.encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def _post(client, body, *, static=None, ts=None, signature=None):
    headers = {}
    if static is not None:
        headers["HTTP_X_WEBHOOK_SECRET"] = static
    if ts is not None:
        headers["HTTP_X_WEBHOOK_TIMESTAMP"] = ts
    if signature is not None:
        headers["HTTP_X_WEBHOOK_SIGNATURE"] = signature
    return client.generic("POST", URL, body, content_type="application/json", **headers)


def _assert_nothing_echoed(response, *values):
    text = response.content.decode()
    for value in (SECRET, BODY_MARKER, *values):
        assert value not in text


@pytest.mark.integration
@pytest.mark.django_db
class TestSignedDelivery:
    def test_a_signed_delivery_inside_the_window_is_accepted(self, client):
        body, ts = _body(), str(int(time.time()))
        with patch(
            "agentcc.views.webhook.ingest_request_logs", return_value=1
        ) as ingest:
            response = _post(client, body, ts=ts, signature=_signature(ts, body))
        assert response.status_code == status.HTTP_200_OK
        ingest.assert_called_once()

    @pytest.mark.parametrize("age", [301, -301])
    def test_a_signature_outside_the_window_is_401(self, client, age):
        body = _body()
        ts = str(int(time.time()) - age)
        with patch(INGEST, return_value=0) as ingest:
            response = _post(client, body, ts=ts, signature=_signature(ts, body))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        ingest.assert_not_called()

    def test_the_signature_covers_the_exact_body_bytes(self, client):
        body, ts = _body(), str(int(time.time()))
        signature = _signature(ts, body)
        with patch(INGEST, return_value=0) as ingest:
            response = _post(client, body + b" ", ts=ts, signature=signature)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        ingest.assert_not_called()

    @pytest.mark.parametrize(
        "headers",
        [
            {"ts": "now", "signature": "0" * 64},
            {"ts": "now", "signature": None},
            {"ts": None, "signature": "sig"},
            {"ts": "not-a-number", "signature": "sig"},
        ],
    )
    def test_a_bad_signature_is_never_retried_on_the_static_header(
        self, client, headers
    ):
        # No downgrade: either signing header present means the signature
        # decides, even with a correct static secret alongside.
        ts = str(int(time.time())) if headers["ts"] == "now" else headers["ts"]
        with patch(INGEST, return_value=0) as ingest:
            response = _post(
                client, _body(), static=SECRET, ts=ts, signature=headers["signature"]
            )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        ingest.assert_not_called()
        _assert_nothing_echoed(response, "0" * 64)


@pytest.mark.integration
@pytest.mark.django_db
class TestStaticSecretTransition:
    def test_the_static_header_alone_is_accepted_and_counted(self, client):
        with (
            patch("agentcc.views.webhook.ingest_request_logs", return_value=1),
            patch("agentcc.views.webhook.logger") as log,
        ):
            response = _post(client, _body(), static=SECRET)
        assert response.status_code == status.HTTP_200_OK
        log.info.assert_called_once_with("agentcc_webhook_static_secret_accepted")

    def test_the_static_header_alone_is_401_once_it_is_turned_off(self, client):
        # The next release's default: pinned here so its removal is one flag.
        with (
            patch("agentcc.views.webhook.STATIC_SECRET_ALLOWED", False),
            patch(INGEST, return_value=0) as ingest,
        ):
            response = _post(client, _body(), static=SECRET)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        ingest.assert_not_called()

    def test_a_wrong_static_secret_is_401(self, client):
        with patch(INGEST, return_value=0) as ingest:
            response = _post(client, _body(), static="wrong-secret")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        ingest.assert_not_called()
        _assert_nothing_echoed(response, "wrong-secret")


@pytest.mark.integration
@pytest.mark.django_db
class TestRefusalCodes:
    def test_an_unconfigured_webhook_is_503(self):
        with (
            patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", ""),
            patch(INGEST, return_value=0) as ingest,
        ):
            response = _post(APIClient(), _body(), static="anything")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        ingest.assert_not_called()

    def test_a_malformed_batch_is_400_invalid_log_batch(self, client):
        response = _post(
            client, json.dumps({"logs": "not-a-list"}).encode(), static=SECRET
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_log_batch"

    def test_a_server_error_is_500_with_a_fixed_message(self, client):
        with patch(
            "agentcc.views.webhook.ingest_request_logs",
            side_effect=RuntimeError(f"database said {BODY_MARKER} {SECRET}"),
        ):
            response = _post(client, _body(), static=SECRET)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        _assert_nothing_echoed(response)

    def test_a_partly_stored_batch_is_503_with_a_fixed_message(self, client):
        from agentcc.services.log_ingestion import LogIngestionError

        with patch(
            "agentcc.views.webhook.ingest_request_logs",
            side_effect=LogIngestionError(f"part {BODY_MARKER} failed"),
        ):
            response = _post(client, _body(), static=SECRET)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        _assert_nothing_echoed(response)
