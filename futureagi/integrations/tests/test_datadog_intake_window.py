"""A Datadog export sends only what Datadog's log intake keeps.

Datadog accepts a log dated up to 18 hours in the past and drops older ones
while answering 2xx (https://docs.datadoghq.com/api/latest/logs/#send-logs).
So a Datadog connection's first sync covers the last 18 hours, a start date
older than that is refused when the connection is made, and a sync whose
window reaches further back (a connection that was in Error or paused for
longer) sends and counts only the last 18 hours and says how many older
request logs were not sent. Every vendor call is patched: nothing is sent.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status as http_status

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager

URL = "/integrations/connections/"


def _logs(organization, hours_ago):
    from agentcc.models import AgentccRequestLog

    now = datetime.now(UTC)
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=organization,
                workspace=None,
                request_id=f"r-{h}h",
                model="gpt-4o-mini",
                provider="openai",
                status_code=200,
                started_at=now - timedelta(hours=h),
            )
            for h in hours_ago
        ]
    )


def _datadog(organization, workspace, user, **fields):
    defaults = {
        "status": ConnectionStatus.BACKFILLING,
        "backfill_completed": False,
    }
    defaults.update(fields)
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.DATADOG,
        display_name="Datadog",
        host_url="https://api.datadoghq.eu",
        encrypted_credentials=CredentialManager.encrypt(
            {"api_key": "dd-key", "site": "eu1"}
        ),
        external_project_name="datadog",
        **defaults,
    )


def _sync(connection, **kwargs):
    from integrations.temporal.activities import sync_integration_connection

    response = MagicMock(status_code=202)
    response.raise_for_status.return_value = None
    with patch(
        "integrations.services.datadog_service.requests.post", return_value=response
    ) as post:
        sync_integration_connection(str(connection.id), **kwargs)
    sent = [
        log["attributes"]["request_id"]
        for call in post.call_args_list
        if call.args[0].endswith("/api/v2/logs")
        for log in call.kwargs["json"]
    ]
    series = [
        s
        for call in post.call_args_list
        if call.args[0].endswith("/api/v2/series")
        for s in call.kwargs["json"]["series"]
    ]
    log = SyncLog.objects.filter(connection=connection).order_by("-started_at")[0]
    connection.refresh_from_db()
    return sent, series, log


@pytest.mark.django_db(transaction=True)
class TestTheFirstSync:
    def test_covers_the_last_18_hours(self, organization, workspace, user):
        _logs(organization, [1, 17, 20])
        connection = _datadog(organization, workspace, user)

        sent, series, log = _sync(connection)

        assert sorted(sent) == ["r-17h", "r-1h"]
        assert (log.status, log.traces_fetched) == (SyncStatus.SUCCESS, 2)
        # 18 hours less the minute a request takes to reach the intake.
        assert log.sync_to - log.sync_from == timedelta(hours=17, minutes=59)
        requests = next(s for s in series if s["metric"] == "agentcc.gateway.requests")
        assert requests["points"][0]["value"] == 2
        assert connection.total_traces_synced == 2


@pytest.mark.django_db(transaction=True)
class TestAWindowOlderThan18Hours:
    def test_sends_the_last_18_hours_and_says_what_was_not_sent(
        self, organization, workspace, user
    ):
        # In Error for a day: the retry's window starts at the last success.
        _logs(organization, [2, 20, 22])
        connection = _datadog(
            organization,
            workspace,
            user,
            status=ConnectionStatus.ERROR,
            status_message="Could not reach Datadog.",
            backfill_completed=True,
            last_synced_at=datetime.now(UTC) - timedelta(hours=24),
        )

        sent, series, log = _sync(connection, retry=True)

        assert sent == ["r-2h"]
        assert (log.status, log.traces_fetched) == (SyncStatus.PARTIAL, 1)
        assert log.error_message == (
            "2 request logs from before Datadog's 18-hour window were not sent: "
            "Datadog accepts logs from the last 18 hours."
        )
        requests = next(s for s in series if s["metric"] == "agentcc.gateway.requests")
        assert requests["points"][0]["value"] == 1
        assert (connection.status, connection.total_traces_synced) == (
            ConnectionStatus.ACTIVE,
            1,
        )


def _valid_service():
    service = MagicMock()
    service.validate_credentials.return_value = {
        "valid": True,
        "projects": [],
        "total_traces": 0,
    }
    return service


@pytest.mark.django_db
class TestAStartDate:
    def _create(self, auth_client, hours_ago):
        payload = {
            "platform": "datadog",
            "credentials": {"api_key": "dd-key", "site": "eu1"},
            "backfill_option": "from_date",
            "backfill_from_date": (
                datetime.now(UTC) - timedelta(hours=hours_ago)
            ).isoformat(),
            "sync_interval_seconds": 300,
        }
        with (
            patch(
                "integrations.views.integration_connection.get_integration_service",
                return_value=_valid_service(),
            ) as service,
            patch("integrations.temporal.activities.start_backfill_workflow"),
        ):
            response = auth_client.post(
                URL, data=json.dumps(payload), content_type="application/json"
            )
        return response, service.return_value

    def test_older_than_18_hours_is_refused_with_the_reason(self, auth_client):
        response, service = self._create(auth_client, 20)

        assert response.status_code == http_status.HTTP_400_BAD_REQUEST
        assert (
            "Datadog accepts logs from the last 18 hours. Pick a start within them."
            in json.dumps(response.json())
        )
        service.validate_credentials.assert_not_called()
        assert not IntegrationConnection.no_workspace_objects.filter(
            platform="datadog"
        ).exists()

    def test_within_18_hours_is_kept(self, auth_client):
        response, _ = self._create(auth_client, 12)

        assert response.status_code == http_status.HTTP_201_CREATED, response.json()
