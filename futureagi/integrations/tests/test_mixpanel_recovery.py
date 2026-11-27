"""Persisted Mixpanel retries must recover without moving past rejected rows."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.services.credentials import CredentialManager
from integrations.temporal.activities import sync_integration_connection
from integrations.tests.test_mixpanel_service import CREDENTIALS, acknowledged, response


@pytest.fixture
def mixpanel_connection(integration_connection):
    connection = integration_connection
    connection.platform = "mixpanel"
    connection.encrypted_credentials = CredentialManager.encrypt(CREDENTIALS)
    connection.last_synced_at = None
    connection.backfill_from = None
    connection.backfill_completed = False
    connection.save()
    return connection


def seed_requests(connection, timestamp):
    return [
        AgentccRequestLog.objects.create(
            organization=connection.organization,
            workspace=connection.workspace,
            request_id=f"mixpanel-recovery-{index}",
            started_at=timestamp,
            model="synthetic-model",
            provider="synthetic-provider",
            status_code=200,
        )
        for index in range(2)
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_failed_export_can_be_retried_without_losing_or_changing_ids(
    mixpanel_connection, status
):
    connection = mixpanel_connection
    seed_requests(connection, datetime.now(UTC) - timedelta(minutes=2))
    failure = response(
        {"code": status, "num_records_imported": 1, "status": "Bad Request"},
        status=status,
    )
    with patch(
        "integrations.services.mixpanel_service.requests.post",
        side_effect=[failure, acknowledged(2)],
    ) as post:
        sync_integration_connection(str(connection.id))
        connection.refresh_from_db()
        assert connection.last_synced_at is None
        assert connection.backfill_completed is False
        first_log = SyncLog.objects.get(connection=connection)
        assert first_log.status == (
            SyncStatus.RATE_LIMITED if status == 429 else SyncStatus.FAILED
        )
        assert first_log.traces_created == 0

        # sync_now accepts ERROR connections; run the activity it dispatches.
        sync_integration_connection(str(connection.id))
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ACTIVE
        assert connection.last_synced_at is not None
        assert connection.backfill_completed is True
        logs = list(
            SyncLog.objects.filter(connection=connection).order_by("started_at")
        )
        assert len(logs) == 2
        assert logs[1].status == SyncStatus.SUCCESS
        assert logs[1].traces_created == 2
        assert post.call_count == 2
        assert (
            post.call_args_list[0].kwargs["json"]
            == post.call_args_list[1].kwargs["json"]
        )


@pytest.mark.django_db(transaction=True)
def test_first_backfill_retry_retains_original_window(mixpanel_connection):
    connection = mixpanel_connection
    now = datetime.now(UTC)
    seed_requests(connection, now - timedelta(hours=23))
    with (
        patch("integrations.temporal.activities.datetime", wraps=datetime) as clock,
        patch(
            "integrations.services.mixpanel_service.requests.post",
            side_effect=[response({}, status=429), acknowledged(2)],
        ) as post,
    ):
        clock.now.return_value = now
        sync_integration_connection(str(connection.id))
        initial_log = SyncLog.objects.get(connection=connection)
        clock.now.return_value = now + timedelta(days=2)
        sync_integration_connection(str(connection.id))
        connection.refresh_from_db()
        logs = list(
            SyncLog.objects.filter(connection=connection).order_by("started_at")
        )
        assert logs[1].sync_from == initial_log.sync_from
        assert logs[1].traces_created == 2
        assert logs[1].status == SyncStatus.SUCCESS
        assert connection.backfill_completed is True
        assert post.call_count == 2
        assert (
            post.call_args_list[0].kwargs["json"]
            == post.call_args_list[1].kwargs["json"]
        )


@pytest.mark.django_db(transaction=True)
def test_paused_mixpanel_is_not_exported(mixpanel_connection):
    connection = mixpanel_connection
    connection.status = ConnectionStatus.PAUSED
    connection.save(update_fields=["status"])
    with patch("integrations.services.mixpanel_service.requests.post") as post:
        sync_integration_connection(str(connection.id))
    post.assert_not_called()
    assert not SyncLog.objects.filter(connection=connection).exists()
