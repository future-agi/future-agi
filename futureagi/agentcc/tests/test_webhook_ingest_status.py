"""The log webhook's status codes, which decide what the gateway redelivers.

The gateway's flusher drops a batch on any 4xx and retries it on a 5xx. A
batch that was only partly stored (LogIngestionError: one org failed, the
others were stored) must come back, and ingestion is replay-safe, so the
retry stores only what is missing: 503. A bad secret or an invalid body would
fail the same way again: 400.
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.services.log_ingestion import LogIngestionError

URL = "/agentcc/webhook/logs/"
SECRET = "webhook-secret-test-value"  # a test value, not a credential


@pytest.fixture
def webhook():
    with patch("agentcc.views.webhook.AGENTCC_WEBHOOK_SECRET", SECRET):
        yield APIClient()


def _post(client, body, secret=SECRET):
    return client.post(URL, body, format="json", HTTP_X_WEBHOOK_SECRET=secret)


def _batch():
    return {
        "logs": [
            {"request_id": "req_1", "model": "gpt-4o", "metadata": {"org_id": "x"}}
        ]
    }


@pytest.mark.integration
@pytest.mark.django_db
def test_partial_ingestion_failure_returns_503(webhook):
    with patch(
        "agentcc.views.webhook.ingest_request_logs",
        side_effect=LogIngestionError("1 part(s) of the log batch were not stored"),
    ):
        response = _post(webhook, _batch())

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "not stored" in str(response.json())


@pytest.mark.integration
@pytest.mark.django_db
def test_bad_secret_still_400(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs") as ingest:
        response = _post(webhook, _batch(), secret="wrong-secret")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    ingest.assert_not_called()


@pytest.mark.integration
@pytest.mark.django_db
def test_invalid_body_still_400(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs") as ingest:
        response = _post(webhook, {"logs": "not-a-list"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    ingest.assert_not_called()


@pytest.mark.integration
@pytest.mark.django_db
def test_other_ingestion_error_still_400(webhook):
    # Not a partly stored batch: a redelivery would fail the same way.
    with patch(
        "agentcc.views.webhook.ingest_request_logs", side_effect=ValueError("bad entry")
    ):
        response = _post(webhook, _batch())

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.django_db
def test_stored_batch_is_200(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs", return_value=1):
        response = _post(webhook, _batch())

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"ingested": 1}
