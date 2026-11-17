"""A connection in Error comes back on its own.

The scheduler syncs a connection in Error again after its sync interval, then
after twice that for each further failure in a row, at most an hour apart; a
retry that succeeds makes it active again, and the email alert still fires
once it has been in Error for an hour, however many retries ran. Slack and
Linear have nothing to sync and are never scheduled. Every vendor call is
patched: nothing is sent.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.services.credentials import CredentialManager
from integrations.temporal.activities import (
    check_integration_error_alerts,
    poll_active_integrations,
    sync_integration_connection,
)

SYNC = "integrations.temporal.activities.sync_integration_connection"


def _export(organization, workspace, user, **fields):
    defaults = {
        "platform": IntegrationPlatform.POSTHOG,
        "display_name": "PostHog",
        "host_url": "http://posthog.sink.test",
        "encrypted_credentials": CredentialManager.encrypt({"api_key": "phc_key"}),
        "external_project_name": "posthog",
        "status": ConnectionStatus.ERROR,
        "status_message": "Could not reach PostHog at http://posthog.sink.test.",
        "sync_interval_seconds": 300,
        "backfill_completed": True,
        "last_synced_at": datetime.now(UTC) - timedelta(hours=3),
    }
    defaults.update(fields)
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization, workspace=workspace, created_by=user, **defaults
    )


def _failed_syncs(connection, *ended_minutes_ago):
    """One failed sync log per value, each ending that many minutes ago."""
    now = datetime.now(UTC)
    for minutes in ended_minutes_ago:
        ended = now - timedelta(minutes=minutes)
        SyncLog.objects.create(
            connection=connection,
            status=SyncStatus.FAILED,
            started_at=ended - timedelta(seconds=5),
            completed_at=ended,
            error_message="Could not reach PostHog.",
        )


@pytest.mark.django_db(transaction=True)
class TestTheSchedulerRetriesAConnectionInError:
    @patch(SYNC)
    def test_after_its_sync_interval(self, sync, organization, workspace, user):
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 6)

        poll_active_integrations()

        sync.delay.assert_called_once_with(str(connection.id), retry=True)

    @patch(SYNC)
    def test_not_before_its_sync_interval(self, sync, organization, workspace, user):
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 2)

        poll_active_integrations()

        sync.delay.assert_not_called()

    @pytest.mark.parametrize(
        ("last_failure_minutes_ago", "retried"), [(19, False), (21, True)]
    )
    @patch(SYNC)
    def test_the_wait_doubles_with_each_failure_in_a_row(
        self, sync, last_failure_minutes_ago, retried, organization, workspace, user
    ):
        # Three failures in a row on a 5-minute interval: 5 x 2 x 2 = 20 minutes.
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, last_failure_minutes_ago, 40, 50)

        poll_active_integrations()

        assert sync.delay.called is retried

    @patch(SYNC)
    def test_the_wait_is_at_most_an_hour(self, sync, organization, workspace, user):
        connection = _export(organization, workspace, user, sync_interval_seconds=1800)
        _failed_syncs(connection, 61, *range(120, 1200, 100))

        poll_active_integrations()

        sync.delay.assert_called_once_with(str(connection.id), retry=True)

    @patch(SYNC)
    def test_a_success_ends_the_run_of_failures(
        self, sync, organization, workspace, user
    ):
        # Failed 6 minutes ago after a success: one failure in a row, so the
        # 5-minute wait has passed (the two older failures do not double it).
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 30, 40)
        ended = datetime.now(UTC) - timedelta(minutes=20)
        SyncLog.objects.create(
            connection=connection,
            status=SyncStatus.SUCCESS,
            started_at=ended - timedelta(seconds=5),
            completed_at=ended,
        )
        _failed_syncs(connection, 6)

        poll_active_integrations()

        sync.delay.assert_called_once_with(str(connection.id), retry=True)

    @pytest.mark.parametrize(
        "platform", [IntegrationPlatform.LINEAR, IntegrationPlatform.SLACK]
    )
    @patch(SYNC)
    def test_slack_and_linear_are_never_scheduled(
        self, sync, platform, organization, workspace, user
    ):
        _export(
            organization,
            workspace,
            user,
            platform=platform,
            external_project_name=platform,
            status=ConnectionStatus.ACTIVE,
            status_message="",
            last_synced_at=None,
        )

        poll_active_integrations()

        sync.delay.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestARetryThatSucceeds:
    def test_makes_the_connection_active_again(self, organization, workspace, user):
        from agentcc.models import AgentccRequestLog

        AgentccRequestLog.no_workspace_objects.bulk_create(
            [
                AgentccRequestLog(
                    organization=organization,
                    workspace=None,
                    request_id=f"r-{i}",
                    model="gpt-4o-mini",
                    provider="openai",
                    status_code=200,
                    started_at=datetime.now(UTC) - timedelta(hours=1),
                )
                for i in range(3)
            ]
        )
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 6)
        response = MagicMock(status_code=200)
        response.raise_for_status.return_value = None
        with patch(
            "integrations.services.posthog_service.requests.post",
            return_value=response,
        ) as post:
            sync_integration_connection(str(connection.id), retry=True)

        # Everything since the last success is sent: the three logs of the
        # hours the connection was in Error.
        assert post.call_count == 1
        log = SyncLog.objects.filter(connection=connection).order_by("-started_at")[0]
        assert (log.status, log.traces_fetched) == (SyncStatus.SUCCESS, 3)
        connection.refresh_from_db()
        assert (connection.status, connection.status_message) == (
            ConnectionStatus.ACTIVE,
            "",
        )


@pytest.mark.django_db(transaction=True)
class TestTheErrorAlert:
    @patch("integrations.temporal.activities._send_error_notification")
    def test_fires_after_an_hour_of_retries(self, send, organization, workspace, user):
        # Retried every few minutes for two hours: each retry saved the
        # connection, so it was last updated a moment ago.
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 1, 30, 60, 90, 120)

        check_integration_error_alerts()

        send.assert_called_once()
        assert send.call_args.args[0].id == connection.id

    @patch("integrations.temporal.activities._send_error_notification")
    def test_waits_for_the_first_hour(self, send, organization, workspace, user):
        connection = _export(organization, workspace, user)
        _failed_syncs(connection, 1, 30)

        check_integration_error_alerts()

        send.assert_not_called()
