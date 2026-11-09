"""A failed export's exception text never leaves the worker.

The exception a failed export raises names the address it called, and that
address can carry credentials: userinfo or a token in its query, as a PostHog
host entered with them or a redirect's target does. The sync log in storage,
the sync-logs API (detail and list) and the worker's export_failed log line
hold the failure's kind, the exception's type and the HTTP status, and none of
that text, including for a sync log stored before error_details held only
those. Every vendor call is patched: nothing is sent.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
import structlog

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager

# Synthetic, minted for this file: a credential-bearing address on a reserved
# host, as an exception's text would name it.
USERINFO_USER = "e2e-disclosure-user"
USERINFO_PASSWORD = "e2e-disclosure-password-5c1e"
QUERY_TOKEN = "e2e-disclosure-token-9b27"
HOST = "override.disclosure.invalid"
CALLED = (
    f"http://{USERINFO_USER}:{USERINFO_PASSWORD}@{HOST}:8080/api/v2/logs"
    f"?dd-api-key={QUERY_TOKEN}"
)
SENSITIVE = (USERINFO_USER, USERINFO_PASSWORD, QUERY_TOKEN, HOST)
ACTIVITIES_LOGGER = "integrations.temporal.activities"
DETAIL_FIELDS = {"kind", "type", "http_status", "vendor_code"}


def _leaks(text: str) -> list[str]:
    return [marker for marker in SENSITIVE if marker in text]


def _refusal(url: str) -> requests.Response:
    """Datadog's log intake refusing the key, as requests hands it back."""
    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response.url = url
    response._content = json.dumps({"errors": [f"Forbidden at {url}"]}).encode()
    return response


def _api_reads(auth_client, log, connection) -> dict:
    detail = auth_client.get(f"/integrations/sync-logs/{log.id}/")
    listed = auth_client.get(
        "/integrations/sync-logs/", {"connection_id": str(connection.id)}
    )
    assert (detail.status_code, listed.status_code) == (200, 200)
    detail = detail.json()
    rows = listed.json()["result"]["sync_logs"]
    return {"detail": detail.get("result", detail), "rows": rows}


@pytest.fixture
def refused(caplog, monkeypatch, organization, workspace, user):
    """A Datadog sync whose log intake refuses the key at a credential-bearing
    address: the connection, its sync log and the worker's log records (kept
    here: caplog reads only the records of the phase it is read in, and the
    sync runs in this fixture's setup)."""
    from agentcc.models import AgentccRequestLog
    from integrations.temporal import activities

    AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        request_id="disclosure-1",
        model="gpt-4o-mini",
        provider="openai",
        status_code=200,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    connection = IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.DATADOG,
        display_name="Datadog",
        host_url="https://api.datadoghq.eu",
        encrypted_credentials=CredentialManager.encrypt(
            {"api_key": "e2e-disclosure-key", "site": "eu1"}
        ),
        external_project_name="datadog",
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
        last_synced_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    # A fresh proxy, so the line goes through the configured processors
    # (exception rendering included) to the stdlib logger caplog reads.
    monkeypatch.setattr(activities, "logger", structlog.get_logger(ACTIVITIES_LOGGER))
    caplog.set_level(logging.WARNING, logger=ACTIVITIES_LOGGER)
    with patch(
        "integrations.services.datadog_service.requests.post",
        return_value=_refusal(CALLED),
    ) as post:
        activities.sync_integration_connection(str(connection.id))
    connection.refresh_from_db()
    log = SyncLog.objects.get(connection=connection)
    return SimpleNamespace(
        connection=connection,
        log=log,
        records=list(caplog.records),
        text=caplog.text,
        called=post.called,
    )


def assert_refused(refused):
    assert refused.called
    sentence = (
        "Datadog refused the API key (HTTP 403). Replace it in Edit, then Sync Now."
    )
    assert (refused.log.status, refused.log.error_message) == (
        SyncStatus.FAILED,
        sentence,
    )
    assert (refused.connection.status, refused.connection.status_message) == (
        ConnectionStatus.ERROR,
        sentence,
    )


@pytest.mark.django_db(transaction=True)
class TestARefusedExport:
    def test_stores_none_of_the_called_address(self, refused):
        assert_refused(refused)
        details = refused.log.error_details
        assert _leaks(json.dumps(details)) == []
        assert set(details) <= DETAIL_FIELDS
        assert details.get("http_status") == 403

    def test_answers_none_of_it_through_the_sync_logs_api(self, refused, auth_client):
        assert_refused(refused)
        read = _api_reads(auth_client, refused.log, refused.connection)
        assert _leaks(json.dumps(read)) == []
        assert [row["id"] for row in read["rows"]] == [str(refused.log.id)]
        for answer in (read["detail"], *read["rows"]):
            assert set(answer["error_details"]) <= DETAIL_FIELDS

    def test_logs_none_of_it(self, refused):
        assert_refused(refused)
        # One export_failed line, rendered as the worker renders it.
        failed = [
            record
            for record in refused.records
            if isinstance(record.msg, dict)
            and record.msg.get("event") == "export_failed"
        ]
        assert len(failed) == 1
        line = failed[0].msg
        assert "export_failed" in refused.text
        assert _leaks(refused.text) == []
        assert _leaks(json.dumps(line, default=str)) == []
        assert "exception" not in line and "error" not in line
        assert (line.get("connection_id"), line.get("http_status")) == (
            str(refused.connection.id),
            403,
        )
        assert line.get("kind") == "credentials_refused"


@pytest.mark.django_db
class TestAStoredSyncLog:
    def test_answers_none_of_the_exception_it_was_stored_with(
        self, auth_client, integration_connection
    ):
        # Stored as sync logs were before error_details held only the kind.
        log = SyncLog.objects.create(
            connection=integration_connection,
            status=SyncStatus.FAILED,
            started_at=datetime.now(UTC) - timedelta(minutes=1),
            completed_at=datetime.now(UTC),
            error_message="The sync stopped.",
            error_details={
                "error": f"403 Client Error: Forbidden for url: {CALLED}",
                "type": "HTTPError",
                "traceback": (
                    "Traceback (most recent call last):\n"
                    f"requests.exceptions.HTTPError: 403 for url: {CALLED}"
                ),
            },
        )

        read = _api_reads(auth_client, log, integration_connection)

        assert _leaks(json.dumps(read)) == []
        assert [row["id"] for row in read["rows"]] == [str(log.id)]
        for answer in (read["detail"], *read["rows"]):
            assert set(answer["error_details"]) <= DETAIL_FIELDS
            assert answer["error_details"].get("type") == "HTTPError"
