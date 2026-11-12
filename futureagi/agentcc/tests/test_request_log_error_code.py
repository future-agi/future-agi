"""The gateway's error code on a request log: stored at ingestion from the
log entry's top-level error_code, and returned by the list and the detail.

A refusal's code is what tells one refusal from another: the decisions
endpoint logs only the HTTP status text as its message, so its code is its
only reason. The gateway leaves a provider's own code out of error_code
(plugins/logging/flusher.go), so what is stored is the gateway's. Rows stored
before the column existed, and requests that carry no code, read null.
"""

import pytest
from rest_framework import status

from agentcc.models import AgentccAPIKey
from agentcc.models.request_log import AgentccRequestLog
from agentcc.services.log_ingestion import ingest_request_logs
from agentcc.tests.test_log_ingestion import GOLDEN_KEY_ID, golden_entry, v1_entry

LOGS_URL = "/agentcc/request-logs/"


@pytest.fixture
def gateway_key(organization):
    return AgentccAPIKey.objects.create(
        gateway_key_id=GOLDEN_KEY_ID, name="golden-key", organization=organization
    )


def _refused(request_id, code):
    entry = v1_entry(
        request_id,
        status_code=403,
        is_error=True,
        error_message="Forbidden",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
    )
    if code is not None:
        entry["error_code"] = code
    return entry


def _listed(client, request_id):
    response = client.get(LOGS_URL, {"request_id": request_id})
    assert response.status_code == status.HTTP_200_OK, response.content
    (row,) = response.json()["results"]
    return row


def _detail(client, row_id):
    response = client.get(f"{LOGS_URL}{row_id}/")
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


@pytest.mark.integration
@pytest.mark.api
class TestRequestLogErrorCode:
    def test_ingestion_stores_the_entry_error_code(self, gateway_key):
        ingest_request_logs(
            [
                _refused("req_code_refused", "decision_refused"),
                _refused("req_code_none", None),
                _refused("req_code_empty", ""),
            ]
        )

        codes = dict(
            AgentccRequestLog.objects.filter(
                request_id__in=["req_code_refused", "req_code_none", "req_code_empty"]
            ).values_list("request_id", "error_code")
        )
        assert codes == {
            "req_code_refused": "decision_refused",
            "req_code_none": None,
            "req_code_empty": None,
        }

    def test_a_v2_entry_keeps_its_code_and_a_success_has_none(self, gateway_key):
        ingest_request_logs(
            [
                golden_entry(
                    request_id="req_code_v2",
                    generation_id="req_code_v2",
                    status_code=402,
                    is_error=True,
                    error_message="Model not allowed for this key",
                    error_code="model_not_allowed",
                ),
                golden_entry(request_id="req_code_ok", generation_id="req_code_ok"),
            ]
        )

        stored = dict(
            AgentccRequestLog.objects.filter(
                request_id__in=["req_code_v2", "req_code_ok"]
            ).values_list("request_id", "error_code")
        )
        assert stored["req_code_v2"] == "model_not_allowed"
        assert stored["req_code_ok"] is None

    def test_an_over_long_code_is_not_stored_and_the_row_still_is(self, gateway_key):
        ingest_request_logs([_refused("req_code_long", "x" * 300)])

        row = AgentccRequestLog.objects.get(request_id="req_code_long")
        assert row.error_code is None
        assert row.status_code == 403

    def test_list_and_detail_return_the_code(self, auth_client, gateway_key):
        ingest_request_logs([_refused("req_code_api", "decision_refused")])

        row = _listed(auth_client, "req_code_api")
        assert row["error_code"] == "decision_refused"
        assert row["error_message"] == "Forbidden"
        detail = _detail(auth_client, row["id"])
        assert detail["error_code"] == "decision_refused"
        assert detail["status_code"] == 403

    def test_a_row_without_a_code_returns_null(self, auth_client, gateway_key):
        ingest_request_logs([_refused("req_code_old", None)])

        row = _listed(auth_client, "req_code_old")
        assert "error_code" in row
        assert row["error_code"] is None
        assert _detail(auth_client, row["id"])["error_code"] is None
