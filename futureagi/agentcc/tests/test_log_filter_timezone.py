"""The Request Logs date filter and its time zone.

The console sends `started_after` / `started_before` as ISO 8601 with an
offset (what `Date.toISOString()` produces); the API honours that offset.
A value with no offset means UTC: it is never refused, and it is made UTC
without Django's naive-datetime fallback. Export reads the same filters and
refuses a date it cannot read.

No test here reaches a network: rows are created directly, and the export
and list views only read the database.
"""

import warnings
from datetime import UTC, datetime

import pytest
from rest_framework import status

from agentcc.models import AgentccRequestLog

LOGS = "/agentcc/request-logs/"
EXPORT = "/agentcc/request-logs/export/"


def _log(organization, request_id, started_at):
    return AgentccRequestLog.objects.create(
        organization=organization,
        request_id=request_id,
        model="gpt-4o",
        provider="openai",
        started_at=started_at,
    )


@pytest.fixture
def pdt_morning(organization):
    """Two requests on 2026-10-19: one at 10:30 PDT (17:30Z), one at 10:30Z."""
    _log(organization, "req_pdt_1030", datetime(2026, 10, 19, 17, 30, tzinfo=UTC))
    _log(organization, "req_utc_1030", datetime(2026, 10, 19, 10, 30, tzinfo=UTC))


def _listed(client, **params):
    response = client.get(LOGS, params)
    assert response.status_code == status.HTTP_200_OK, response.content
    return sorted(row["request_id"] for row in response.json()["results"])


def _exported_ids(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    body = b"".join(response.streaming_content).decode()
    lines = body.strip().splitlines()[1:]  # header row first
    return sorted(line.split(",")[1] for line in lines)


@pytest.mark.django_db
class TestOffsetHonoured:
    # The console's 10:30-11:30 in America/Los_Angeles, as toISOString() sends it.
    WINDOW = {
        "started_after": "2026-10-19T17:30:00.000Z",
        "started_before": "2026-10-19T18:30:00.000Z",
    }

    def test_list_keeps_the_request_made_at_1030_pdt(self, auth_client, pdt_morning):
        assert _listed(auth_client, **self.WINDOW) == ["req_pdt_1030"]

    def test_explicit_negative_offset_is_honoured(self, auth_client, pdt_morning):
        ids = _listed(
            auth_client,
            started_after="2026-10-19T10:30:00-07:00",
            started_before="2026-10-19T11:30:00-07:00",
        )
        assert ids == ["req_pdt_1030"]

    def test_export_downloads_the_same_window_as_the_table(
        self, auth_client, pdt_morning
    ):
        response = auth_client.get(EXPORT, {**self.WINDOW, "export_format": "csv"})
        assert _exported_ids(response) == ["req_pdt_1030"]


@pytest.mark.django_db
class TestNaiveStaysUtc:
    """API callers that send no offset get UTC, never a 400."""

    NAIVE = {
        "started_after": "2026-10-19T10:30",
        "started_before": "2026-10-19T11:30",
    }

    def test_list_reads_a_naive_value_as_utc(self, auth_client, pdt_morning):
        assert _listed(auth_client, **self.NAIVE) == ["req_utc_1030"]

    def test_export_reads_a_naive_value_as_utc(self, auth_client, pdt_morning):
        response = auth_client.get(EXPORT, {**self.NAIVE, "export_format": "csv"})
        assert _exported_ids(response) == ["req_utc_1030"]

    def test_a_naive_value_is_made_utc_on_purpose_not_by_django_fallback(
        self, auth_client, pdt_morning
    ):
        # The value is made UTC before the lookup; Django's naive-datetime fallback
        # (a RuntimeWarning) is never reached.
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            assert _listed(auth_client, **self.NAIVE) == ["req_utc_1030"]
            response = auth_client.get(EXPORT, {**self.NAIVE, "export_format": "csv"})
            assert _exported_ids(response) == ["req_utc_1030"]


@pytest.mark.django_db
class TestUnreadableDate:
    def test_export_refuses_a_date_it_cannot_read(self, auth_client, pdt_morning):
        # An unreadable date is refused; the filter is never dropped.
        response = auth_client.get(
            EXPORT, {"started_after": "yesterday", "export_format": "csv"}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "started_after" in response.content.decode()

    def test_list_refuses_a_date_it_cannot_read(self, auth_client, pdt_morning):
        response = auth_client.get(LOGS, {"started_before": "not-a-date"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content


def test_help_text_states_the_offset_rule():
    from agentcc.serializers.request_queries import GatewayRequestLogQuerySerializer

    fields = GatewayRequestLogQuerySerializer().fields
    for name in ("started_after", "started_before"):
        text = str(fields[name].help_text or "")
        assert "offset" in text and "UTC" in text, (name, text)
