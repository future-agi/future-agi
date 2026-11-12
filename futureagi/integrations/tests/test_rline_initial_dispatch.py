"""An undispatched first backfill remains retryable; no live task service."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from integrations.models import ConnectionStatus, IntegrationConnection, SyncLog
from integrations.temporal.activities import poll_active_integrations, sync_integration_connection

URL = "/integrations/connections/"
ACTIVITIES = "integrations.temporal.activities"


def _create(auth_client, option="all", *, fail=True):
    service = MagicMock()
    service.validate_credentials.return_value = {"valid": True, "projects": []}
    payload = {
        "platform": "posthog",
        "host_url": "https://posthog.example.test",
        "credentials": {"api_key": "phc_synthetic"},
        "backfill_option": option,
        "sync_interval_seconds": 300,
    }
    if option == "from_date":
        payload["backfill_from_date"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with (
        patch("integrations.views.integration_connection.get_integration_service", return_value=service),
        patch(f"{ACTIVITIES}.start_backfill_workflow",
              side_effect=RuntimeError("synthetic-private-dispatch-detail") if fail else None) as dispatch,
    ):
        response = auth_client.post(URL, data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 201, response.json()
    body = response.json()
    body = body.get("result", body)
    connection = IntegrationConnection.no_workspace_objects.get(id=body["id"])
    dispatch.assert_called_once_with(str(connection.id))
    return connection, body


@pytest.mark.django_db
@pytest.mark.parametrize("option", ["all", "from_date"])
def test_dispatch_failure_returns_201_with_recoverable_actionable_state(auth_client, option):
    connection, body = _create(auth_client, option)
    assert connection.status == ConnectionStatus.ERROR
    assert body["status"] == ConnectionStatus.ERROR
    assert "Sync Now" in connection.status_message
    assert "retry" in connection.status_message.lower()
    assert "synthetic-private-dispatch-detail" not in json.dumps(body)
    assert not connection.backfill_completed
    assert connection.last_synced_at is None
    assert not SyncLog.objects.filter(connection=connection).exists()
    if option == "from_date":
        assert connection.backfill_from is not None


@pytest.mark.django_db
def test_dispatch_recovery_message_survives_detail_and_list_projection(auth_client):
    connection, created = _create(auth_client)
    detail = auth_client.get(f"{URL}{connection.id}/").json()["result"]
    listed = auth_client.get(URL).json()["result"]["connections"]
    card = next(row for row in listed if row["id"] == str(connection.id))
    for row in (created, detail, card):
        assert row["status_message"] == connection.status_message
        assert "Sync Now" in row["status_message"]
        assert "synthetic-private-dispatch-detail" not in json.dumps(row)


@pytest.mark.django_db(transaction=True)
def test_failed_initial_dispatch_is_retried_by_poll_after_interval(auth_client):
    connection, _ = _create(auth_client)
    with patch(f"{ACTIVITIES}.sync_integration_connection") as sync:
        poll_active_integrations()
        sync.delay.assert_not_called()
        IntegrationConnection.no_workspace_objects.filter(pk=connection.pk).update(
            updated_at=datetime.now(UTC) - timedelta(seconds=301)
        )
        poll_active_integrations()
    sync.delay.assert_called_once_with(str(connection.id), retry=True)


@pytest.mark.django_db
def test_sync_now_accepts_a_connection_with_no_dispatched_task(auth_client):
    connection, _ = _create(auth_client)
    with patch(f"{ACTIVITIES}.sync_integration_connection") as sync:
        response = auth_client.post(
            f"{URL}{connection.id}/sync_now/", data="{}", content_type="application/json"
        )
    assert response.status_code == 200, response.json()
    sync.delay.assert_called_once_with(str(connection.id), retry=True)


@pytest.mark.django_db(transaction=True)
def test_retry_finishes_the_preserved_initial_backfill(auth_client, organization):
    from agentcc.models import AgentccRequestLog

    connection, _ = _create(auth_client, "from_date")
    requested_from = connection.backfill_from
    AgentccRequestLog.no_workspace_objects.bulk_create([AgentccRequestLog(
        organization=organization, workspace=None, request_id="historical-request",
        model="synthetic", provider="openai", status_code=200,
        started_at=datetime.now(UTC) - timedelta(days=1),
    )])
    service = MagicMock()
    service.export_events.side_effect = lambda host, credentials, events: {"sent": len(events)}
    with patch("integrations.services.base.get_integration_service", return_value=service):
        sync_integration_connection(str(connection.id), retry=True)
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.backfill_completed
    assert connection.backfill_from == requested_from
    assert connection.total_traces_synced == 1
    service.export_events.assert_called_once()


@pytest.mark.django_db
def test_successfully_dispatched_backfill_still_blocks_duplicate_sync_now(auth_client):
    connection, body = _create(auth_client, fail=False)
    assert body["status"] == ConnectionStatus.BACKFILLING
    with patch(f"{ACTIVITIES}.sync_integration_connection") as sync:
        response = auth_client.post(
            f"{URL}{connection.id}/sync_now/", data="{}", content_type="application/json"
        )
    assert response.status_code == 409
    sync.delay.assert_not_called()
