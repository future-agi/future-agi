"""The log webhook's status codes, which decide what the gateway redelivers.

The gateway keeps a batch and delivers it again on any answer but a 2xx and a
malformed batch (400 invalid_log_batch). A batch that was only partly stored
(LogIngestionError: one org failed, the others were stored) must come back,
and ingestion is replay-safe, so the retry stores only what is missing: 503.
A refused secret is 401 and any other server error 500, both delivered again;
only an invalid body is 400.
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from agentcc.services.log_ingestion import LogIngestionError

URL = "/agentcc/webhook/logs/"
SECRET = "d7-webhook-secret-test-value"  # a test value, not a credential


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
def test_bad_secret_is_401(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs") as ingest:
        response = _post(webhook, _batch(), secret="wrong-secret")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    ingest.assert_not_called()


@pytest.mark.integration
@pytest.mark.django_db
def test_invalid_body_still_400(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs") as ingest:
        response = _post(webhook, {"logs": "not-a-list"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "invalid_log_batch"
    ingest.assert_not_called()


@pytest.mark.integration
@pytest.mark.django_db
def test_other_ingestion_error_is_500(webhook):
    # Not the batch's fault (a database blip, say): delivered again.
    with patch(
        "agentcc.views.webhook.ingest_request_logs", side_effect=ValueError("bad entry")
    ):
        response = _post(webhook, _batch())

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.integration
@pytest.mark.django_db
def test_stored_batch_is_200(webhook):
    with patch("agentcc.views.webhook.ingest_request_logs", return_value=1):
        response = _post(webhook, _batch())

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == {"ingested": 1}
