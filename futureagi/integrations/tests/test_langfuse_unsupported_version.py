"""A Langfuse that no longer serves the trace list stops the import with its own state.

Langfuse v4 in its default events_only mode answers the trace list with 404
while the same keys still read the project. The import records that as an
unsupported version, says so on the connection, and keeps the time of the
first such failure. A project that is really gone keeps its own message.
All HTTP is mocked; dates in the projection tests are fixed.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests

from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.serializers.integration_connection import (
    IntegrationConnectionListSerializer,
)
from integrations.serializers.sync_log import SyncLogSerializer
from integrations.services.langfuse_service import LangfuseService
from integrations.temporal.activities import sync_integration_connection

REQUEST = "integrations.services.langfuse_service.requests.request"
HOST = "https://langfuse.example.com"
CREDS = {"public_key": "pk-lf-test", "secret_key": "sk-lf-test"}
STOPPED_SINCE_KEY = "unsupported_since"

UNSUPPORTED = "This Langfuse version isn't supported by the import."
NOT_FOUND = "Langfuse project not found. It may have been deleted."
ROW_TAIL = (
    "Langfuse no longer serves the API this import reads. "
    "Everything already imported is unchanged."
)
EVENTS_ONLY_BODY = {
    "message": (
        "This endpoint is not available on deployments running in Langfuse v4 "
        "events_only mode. Upgrade to the latest Langfuse SDKs, which send traces "
        "via OpenTelemetry, or send traces directly to POST "
        "/api/public/otel/v1/traces. Scores can still be sent via POST "
        "/api/public/scores."
    )
}
PROJECTS_BODY = {"data": [{"id": "project-1", "name": "my-langfuse-project"}]}
EMPTY_LIST_BODY = {"data": [], "meta": {"page": 1, "totalItems": 0, "totalPages": 1}}


def _response(status, body):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"HTTP {status}", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _langfuse(answers):
    """Answer each Langfuse path with its (status, body); anything else is 404."""

    def request(method, url, **kwargs):
        status, body = answers.get(urlsplit(url).path, (404, {"message": "Not Found"}))
        return _response(status, body)

    return request


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _row_sentence(since):
    since = since.astimezone(UTC)
    day = f"{since.day} {MONTHS[since.month - 1]} {since.year}"
    return f"{UNSUPPORTED} Import stopped on {day}: {ROW_TAIL}"


V4_ANSWERS = {
    "/api/public/traces": (404, EVENTS_ONLY_BODY),
    "/api/public/projects": (200, PROJECTS_BODY),
}
DELETED_PROJECT_ANSWERS = {
    "/api/public/traces": (404, {"message": "Not Found"}),
    "/api/public/projects": (404, {"message": "Not Found"}),
}


# ---------------------------------------------------------------------------
# The service tells an unsupported deployment from a missing project
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTraceListNotFound:
    def test_list_404_while_project_answers_raises_unsupported_version(self):
        with patch(REQUEST, side_effect=_langfuse(V4_ANSWERS)):
            with pytest.raises(requests.exceptions.HTTPError) as caught:
                LangfuseService().fetch_traces(HOST, CREDS, page=1, limit=100)

        assert type(caught.value).__name__ == "LangfuseUnsupportedVersion"
        assert caught.value.response.status_code == 404

    def test_list_404_while_project_is_missing_keeps_the_plain_404(self):
        with patch(REQUEST, side_effect=_langfuse(DELETED_PROJECT_ANSWERS)):
            with pytest.raises(requests.exceptions.HTTPError) as caught:
                LangfuseService().fetch_traces(HOST, CREDS, page=1, limit=100)

        assert type(caught.value).__name__ == "HTTPError"
        assert caught.value.response.status_code == 404

    def test_list_404_when_the_project_read_fails_keeps_the_plain_404(self):
        def request(method, url, **kwargs):
            if urlsplit(url).path == "/api/public/projects":
                raise requests.exceptions.ConnectionError("unreachable")
            return _response(404, {"message": "Not Found"})

        with patch(REQUEST, side_effect=request):
            with pytest.raises(requests.exceptions.HTTPError) as caught:
                LangfuseService().fetch_traces(HOST, CREDS, page=1, limit=100)

        assert type(caught.value).__name__ == "HTTPError"
        assert caught.value.response.status_code == 404


# ---------------------------------------------------------------------------
# The import's state after such a sync
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUnsupportedVersionSync:
    @patch("time.sleep")
    def test_events_only_list_answer_stops_the_backfill_as_unsupported(
        self, mock_sleep, backfilling_connection
    ):
        connection = backfilling_connection
        with patch(REQUEST, side_effect=_langfuse(V4_ANSWERS)):
            sync_integration_connection(str(connection.id))

        connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=connection).latest("started_at")
        assert connection.status == ConnectionStatus.ERROR
        assert connection.status_message == UNSUPPORTED
        assert log.status == SyncStatus.FAILED
        assert log.error_message == UNSUPPORTED
        assert log.error_details["kind"] == "unsupported_version"
        assert log.error_details["http_status"] == 404
        stopped_since = connection.sync_cursor[STOPPED_SINCE_KEY]
        assert datetime.fromisoformat(stopped_since) == log.started_at

        row = IntegrationConnectionListSerializer(connection).data
        assert row["status"] == ConnectionStatus.ERROR
        assert row["status_message"] == _row_sentence(log.started_at)
        assert SyncLogSerializer(log).data["error_message"] == UNSUPPORTED

    @patch("time.sleep")
    def test_a_later_unsupported_failure_keeps_the_first_date(
        self, mock_sleep, integration_connection
    ):
        connection = integration_connection
        first = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
        connection.status = ConnectionStatus.ERROR
        connection.status_message = UNSUPPORTED
        connection.sync_cursor = {STOPPED_SINCE_KEY: first.isoformat()}
        connection.save(update_fields=["status", "status_message", "sync_cursor"])

        with patch(REQUEST, side_effect=_langfuse(V4_ANSWERS)):
            sync_integration_connection(str(connection.id), retry=True)

        connection.refresh_from_db()
        assert connection.sync_cursor == {STOPPED_SINCE_KEY: first.isoformat()}
        row = IntegrationConnectionListSerializer(connection).data
        assert row["status_message"] == (
            f"{UNSUPPORTED} Import stopped on 16 Nov 2026: {ROW_TAIL}"
        )

    @patch("time.sleep")
    def test_a_sync_that_succeeds_ends_the_stop(
        self, mock_sleep, integration_connection
    ):
        connection = integration_connection
        connection.status = ConnectionStatus.ERROR
        connection.status_message = UNSUPPORTED
        connection.sync_cursor = {
            STOPPED_SINCE_KEY: datetime(2026, 11, 16, 9, 30, tzinfo=UTC).isoformat()
        }
        connection.save(update_fields=["status", "status_message", "sync_cursor"])

        answers = {"/api/public/traces": (200, EMPTY_LIST_BODY)}
        with patch(REQUEST, side_effect=_langfuse(answers)):
            sync_integration_connection(str(connection.id), retry=True)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ACTIVE
        assert STOPPED_SINCE_KEY not in connection.sync_cursor

    @patch("time.sleep")
    def test_a_deleted_project_still_reads_not_found(
        self, mock_sleep, integration_connection
    ):
        connection = integration_connection
        with patch(REQUEST, side_effect=_langfuse(DELETED_PROJECT_ANSWERS)):
            sync_integration_connection(str(connection.id))

        connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=connection).latest("started_at")
        assert connection.status == ConnectionStatus.ERROR
        assert connection.status_message == NOT_FOUND
        assert log.error_details["kind"] == "not_found"
        assert STOPPED_SINCE_KEY not in (connection.sync_cursor or {})
        row = IntegrationConnectionListSerializer(connection).data
        assert row["status_message"] == NOT_FOUND


# ---------------------------------------------------------------------------
# What the connection row and Sync History say
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnsupportedVersionWording:
    def _failed_log(self, connection, started_at):
        return SyncLog.objects.create(
            connection=connection,
            status=SyncStatus.FAILED,
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=1),
            error_message=UNSUPPORTED,
            error_details={
                "kind": "unsupported_version",
                "type": "LangfuseUnsupportedVersion",
                "http_status": 404,
                "vendor_code": None,
            },
        )

    def test_connection_row_says_it_stopped_since_the_first_failure(
        self, integration_connection
    ):
        connection = integration_connection
        connection.status = ConnectionStatus.ERROR
        connection.status_message = UNSUPPORTED
        connection.sync_cursor = {STOPPED_SINCE_KEY: "2026-11-16T09:30:00+00:00"}
        connection.save(update_fields=["status", "status_message", "sync_cursor"])
        log = self._failed_log(connection, datetime(2026, 11, 18, 8, 0, tzinfo=UTC))

        row = IntegrationConnectionListSerializer(connection).data

        assert row["status_message"] == (
            "This Langfuse version isn't supported by the import. "
            "Import stopped on 16 Nov 2026: Langfuse no longer serves the API "
            "this import reads. Everything already imported is unchanged."
        )
        assert SyncLogSerializer(log).data["error_message"] == UNSUPPORTED

    def test_row_without_a_stored_first_failure_uses_the_failed_sync_date(
        self, integration_connection
    ):
        connection = integration_connection
        connection.status = ConnectionStatus.ERROR
        connection.status_message = UNSUPPORTED
        connection.save(update_fields=["status", "status_message"])
        self._failed_log(connection, datetime(2026, 11, 17, 23, 59, tzinfo=UTC))

        row = IntegrationConnectionListSerializer(connection).data

        assert row["status_message"] == (
            f"{UNSUPPORTED} Import stopped on 17 Nov 2026: {ROW_TAIL}"
        )
