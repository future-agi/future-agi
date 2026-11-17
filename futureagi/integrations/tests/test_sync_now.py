"""Sync now answers what it did.

On an active connection it dispatches a sync; on a connection in Error it is a
real retry (the sync runs though the connection is in Error, and a success
makes it active again); a minute must pass since the last attempt, failed or
not; and it answers 200 only when a sync was dispatched, otherwise it refuses
with the reason. Nothing is sent: the dispatch is patched.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

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
SYNC = "integrations.temporal.activities.sync_integration_connection"


def _export(organization, workspace, user, status, last_synced_minutes_ago=180):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=IntegrationPlatform.POSTHOG,
        display_name="PostHog",
        host_url="http://posthog.sink.test",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "phc_key"}),
        external_project_name="posthog",
        status=status,
        status_message=(
            "Could not reach PostHog." if status == ConnectionStatus.ERROR else ""
        ),
        backfill_completed=True,
        last_synced_at=datetime.now(UTC) - timedelta(minutes=last_synced_minutes_ago),
    )


def _sync_now(auth_client, connection):
    response = auth_client.post(
        f"{URL}{connection.id}/sync_now/",
        data=json.dumps({}),
        content_type="application/json",
    )
    body = response.json()
    return response.status_code, body.get("result", body)


def _failed_attempt(connection, seconds_ago):
    started = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    SyncLog.objects.create(
        connection=connection,
        status=SyncStatus.FAILED,
        started_at=started,
        completed_at=started + timedelta(seconds=2),
        error_message="Could not reach PostHog.",
    )


@pytest.mark.django_db
class TestSyncNow:
    @patch(SYNC)
    def test_on_an_active_connection_it_syncs(
        self, sync, auth_client, organization, workspace, user
    ):
        connection = _export(organization, workspace, user, ConnectionStatus.ACTIVE)

        code, result = _sync_now(auth_client, connection)

        assert (code, result) == (
            http_status.HTTP_200_OK,
            {"message": "Sync triggered."},
        )
        sync.delay.assert_called_once_with(str(connection.id))

    @patch(SYNC)
    def test_on_a_connection_in_error_it_retries(
        self, sync, auth_client, organization, workspace, user
    ):
        connection = _export(organization, workspace, user, ConnectionStatus.ERROR)
        _failed_attempt(connection, 300)

        code, result = _sync_now(auth_client, connection)

        assert (code, result) == (
            http_status.HTTP_200_OK,
            {"message": "Retry started."},
        )
        sync.delay.assert_called_once_with(str(connection.id), retry=True)

    @patch(SYNC)
    def test_a_minute_must_pass_since_the_last_failed_attempt(
        self, sync, auth_client, organization, workspace, user
    ):
        # The last success was hours ago; the last attempt, which failed,
        # 20 seconds ago.
        connection = _export(organization, workspace, user, ConnectionStatus.ERROR)
        _failed_attempt(connection, 20)

        code, result = _sync_now(auth_client, connection)

        assert code == http_status.HTTP_400_BAD_REQUEST
        assert "before retrying" in json.dumps(result)
        sync.delay.assert_not_called()

    @patch(SYNC)
    def test_on_a_paused_connection_it_refuses_with_the_reason(
        self, sync, auth_client, organization, workspace, user
    ):
        connection = _export(organization, workspace, user, ConnectionStatus.PAUSED)

        code, result = _sync_now(auth_client, connection)

        assert code == http_status.HTTP_400_BAD_REQUEST
        assert "Resume it before triggering a sync" in json.dumps(result)
        sync.delay.assert_not_called()
