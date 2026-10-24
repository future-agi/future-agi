"""Request Logs export answers that the console can show.

The console counts the rows its filters match (the list endpoint's `count`,
`limit=1`) before it downloads, shows the count and the 10,000-row cap, and
reads the server's message out of a refused export. These tests pin the
server half: the count the list reports is the count export uses; 0 rows and
too many rows are refused with a sentence that says which; the cap is the
one the console states; the filters export reads are validated like the
list's (an unreadable value is refused, never dropped).

No test here reaches a network.
"""

from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import status

from agentcc.models import AgentccRequestLog
from agentcc.services import export as export_service

LOGS = "/agentcc/request-logs/"
EXPORT = "/agentcc/request-logs/export/"
T0 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def _rows(organization, n, model="gpt-4o"):
    for i in range(n):
        AgentccRequestLog.objects.create(
            organization=organization,
            request_id=f"req_{model}_{i}",
            model=model,
            provider="openai",
            started_at=T0 + timedelta(minutes=i),
        )


def _message(response):
    body = response.json()
    return body.get("message") or body.get("result")


def _count(client, **params):
    response = client.get(LOGS, {**params, "limit": 1})
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["count"]


@pytest.mark.django_db
def test_the_cap_the_console_states_is_the_servers():
    # RequestExplorerSection's EXPORT_ROW_CAP is 10,000; this keeps them one number.
    assert export_service.MAX_EXPORT_ROWS == 10_000


@pytest.mark.django_db
def test_zero_rows_is_refused_with_a_sentence_that_says_so(auth_client, organization):
    _rows(organization, 2)
    response = auth_client.get(EXPORT, {"model": "no-such-model"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _message(response) == (
        "No requests match these filters, so there is nothing to export."
    )
    assert response.json()["code"] == "export_empty"


@pytest.mark.django_db
def test_over_the_cap_is_refused_with_the_count_and_the_cap(
    auth_client, organization, monkeypatch
):
    monkeypatch.setattr("agentcc.views.request_log.MAX_EXPORT_ROWS", 2)
    _rows(organization, 3)
    response = auth_client.get(EXPORT)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    message = _message(response)
    assert "3 rows" in message and "2 rows" in message, message
    assert response.json()["code"] == "export_too_large"


@pytest.mark.django_db
def test_list_count_matches_what_export_downloads(auth_client, organization):
    _rows(organization, 3, model="gpt-4o")
    _rows(organization, 2, model="claude-x")
    params = {"model": "claude-x", "started_after": "2026-10-19T08:00:00.000Z"}
    assert _count(auth_client, **params) == 2
    response = auth_client.get(EXPORT, {**params, "export_format": "json"})
    assert response.status_code == status.HTTP_200_OK
    lines = b"".join(response.streaming_content).decode().strip().splitlines()
    assert len(lines) == 2


@pytest.mark.django_db
def test_export_without_filters_downloads_every_row_the_table_lists(
    auth_client, organization
):
    _rows(organization, 3)
    assert _count(auth_client) == 3
    response = auth_client.get(EXPORT, {"export_format": "csv"})
    assert response.status_code == status.HTTP_200_OK
    lines = b"".join(response.streaming_content).decode().strip().splitlines()
    assert len(lines) == 1 + 3


@pytest.mark.django_db
def test_export_refuses_an_unreadable_filter_instead_of_widening(
    auth_client, organization
):
    _rows(organization, 2)
    response = auth_client.get(EXPORT, {"min_status_code": "two hundred"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "min_status_code" in response.content.decode()


def _exported_request_ids(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    body = b"".join(response.streaming_content).decode()
    return [line.split(",")[1] for line in body.strip().splitlines()[1:]]


@pytest.mark.django_db
def test_max_rows_streams_the_first_rows_in_the_tables_order(
    auth_client, organization, monkeypatch
):
    # The console asks for at most the cap; over it, the first rows in the
    # table's order are streamed instead of a refusal (it showed the count
    # and the cap first).
    monkeypatch.setattr("agentcc.views.request_log.MAX_EXPORT_ROWS", 2)
    _rows(organization, 3)
    response = auth_client.get(
        EXPORT, {"max_rows": 2, "ordering": "-started_at", "export_format": "csv"}
    )
    assert _exported_request_ids(response) == ["req_gpt-4o_2", "req_gpt-4o_1"]
    response = auth_client.get(
        EXPORT, {"max_rows": 2, "ordering": "started_at", "export_format": "csv"}
    )
    assert _exported_request_ids(response) == ["req_gpt-4o_0", "req_gpt-4o_1"]


@pytest.mark.django_db
def test_max_rows_above_the_cap_is_refused(auth_client, organization):
    _rows(organization, 1)
    response = auth_client.get(EXPORT, {"max_rows": 10_001})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "max_rows" in response.content.decode()


@pytest.mark.django_db
def test_max_rows_still_says_when_nothing_matches(auth_client, organization):
    _rows(organization, 1)
    response = auth_client.get(EXPORT, {"max_rows": 10, "model": "none"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "export_empty"


@pytest.mark.django_db
def test_without_max_rows_api_callers_keep_the_refusal(
    auth_client, organization, monkeypatch
):
    monkeypatch.setattr("agentcc.views.request_log.MAX_EXPORT_ROWS", 2)
    _rows(organization, 3)
    assert auth_client.get(EXPORT).json()["code"] == "export_too_large"


def test_tag_filters_are_documented_on_list_and_export():
    from agentcc.serializers.request_log import (
        AgentccRequestLogExportQuerySerializer,
        AgentccRequestLogQuerySerializer,
    )

    for serializer in (
        AgentccRequestLogQuerySerializer,
        AgentccRequestLogExportQuerySerializer,
    ):
        fields = serializer().fields
        for name in ("application", "service", "tags"):
            assert name in fields, (serializer.__name__, name)
    assert "max_rows" in AgentccRequestLogExportQuerySerializer().fields
