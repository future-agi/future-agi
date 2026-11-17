"""Whose words a request log's error message is: stored at ingestion from the
log entry's error_source (gateway, provider or client), and returned by the
list and the detail.

The gateway sets it beside error_code (plugins/logging/record.go): provider
when the message is a provider's own error answer, client when the caller
ended the request, gateway for every other error. An unknown value is not
stored. Rows stored before the column existed, and requests without an error,
read null.
"""

import pytest
from rest_framework import status

from agentcc.models import AgentccAPIKey
from agentcc.models.request_log import AgentccRequestLog
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.test_log_ingestion import GOLDEN_KEY_ID, v1_entry

LOGS_URL = "/agentcc/request-logs/"


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID, name="golden-key", organization=organization
    )


def _failed(request_id, source, message="max_tokens is too large"):
    entry = v1_entry(
        request_id,
        status_code=400,
        is_error=True,
        error_message=message,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )
    if source is not None:
        entry["error_source"] = source
    return entry


def _listed(client, request_id):
    response = client.get(LOGS_URL, {"request_id": request_id})
    assert response.status_code == status.HTTP_200_OK, response.content
    (row,) = response.json()["results"]
    return row


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogErrorSource:
    def test_ingestion_stores_each_source(self, gateway_key):
        ingest_request_logs(
            [
                _failed("req_src_provider", "provider"),
                _failed(
                    "req_src_gateway", "gateway", "this key may not use this model"
                ),
                _failed("req_src_client", "client", "context canceled"),
            ]
        )

        stored = dict(
            AgentccRequestLog.objects.filter(
                request_id__in=["req_src_provider", "req_src_gateway", "req_src_client"]
            ).values_list("request_id", "error_source")
        )
        assert stored == {
            "req_src_provider": "provider",
            "req_src_gateway": "gateway",
            "req_src_client": "client",
        }

    def test_an_unknown_or_missing_source_is_null(self, gateway_key):
        ingest_request_logs(
            [
                _failed("req_src_unknown", "upstream"),
                _failed("req_src_missing", None),
                _failed("req_src_empty", ""),
            ]
        )

        stored = dict(
            AgentccRequestLog.objects.filter(
                request_id__in=["req_src_unknown", "req_src_missing", "req_src_empty"]
            ).values_list("request_id", "error_source")
        )
        assert stored == {
            "req_src_unknown": None,
            "req_src_missing": None,
            "req_src_empty": None,
        }

    def test_list_and_detail_return_the_source(self, auth_client, gateway_key):
        ingest_request_logs([_failed("req_src_api", "provider")])

        row = _listed(auth_client, "req_src_api")
        assert row["error_source"] == "provider"
        response = auth_client.get(f"{LOGS_URL}{row['id']}/")
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["result"]["error_source"] == "provider"

    def test_a_row_without_a_source_returns_null(self, auth_client, gateway_key):
        ingest_request_logs([_failed("req_src_old", None)])

        row = _listed(auth_client, "req_src_old")
        assert "error_source" in row
        assert row["error_source"] is None
