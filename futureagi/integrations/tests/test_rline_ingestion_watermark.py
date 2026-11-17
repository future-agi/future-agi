"""Export delivery follows ingestion, not when a gateway request started.

All destination calls are synthetic; no destination is contacted.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, IntegrationConnection, SyncLog, SyncStatus
from integrations.services.credentials import CredentialManager
from integrations.temporal.activities import sync_integration_connection

ACTIVITIES = "integrations.temporal.activities"
PLATFORMS = ["posthog", "mixpanel", "cloud_storage", "message_queue", "datadog"]
T0 = datetime(2026, 11, 9, 12, tzinfo=UTC)


def _connection(organization, workspace, user, platform):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        created_by=user,
        platform=platform,
        display_name="Synthetic export",
        host_url="https://export.example.test",
        encrypted_credentials=CredentialManager.encrypt({"api_key": "synthetic"}),
        external_project_name=platform,
        status=ConnectionStatus.ACTIVE,
        backfill_completed=True,
        last_synced_at=T0 - timedelta(minutes=5),
    )


def _row(organization, name, created_at, started_at, *, row_id=None, **fields):
    values = dict(
        organization=organization,
        workspace=None,
        request_id=name,
        model="synthetic-model",
        provider="openai",
        status_code=200,
        started_at=started_at,
        **fields,
    )
    if row_id is not None:
        values["id"] = UUID(int=row_id)
    (row,) = AgentccRequestLog.no_workspace_objects.bulk_create(
        [AgentccRequestLog(**values)]
    )
    AgentccRequestLog.no_workspace_objects.filter(pk=row.pk).update(created_at=created_at)
    row.refresh_from_db()
    return row


def _sync(connection, now, *, failure=None, on_send=None):
    delivered = []
    service = MagicMock()

    def send(*args):
        payload = args[-1]
        if failure:
            raise failure
        delivered.extend(payload)
        if on_send:
            on_send()
        return {"sent": len(payload)}

    service.export_events.side_effect = send
    service.export_logs.side_effect = send
    service.publish_logs.side_effect = send
    service.export_metrics.side_effect = lambda credentials, series: {"sent": len(series)}
    with (
        patch(f"{ACTIVITIES}.datetime", wraps=datetime) as clock,
        patch("integrations.services.base.get_integration_service", return_value=service),
    ):
        clock.now.return_value = now
        sync_integration_connection(str(connection.id), retry=True)
    connection.refresh_from_db()
    return delivered, service


def _ids(payloads):
    return [
        p.get("properties", p.get("attributes", p))["request_id"] for p in payloads
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_late_completion_is_delivered_next_sync_once(organization, workspace, user, platform):
    connection = _connection(organization, workspace, user, platform)
    _row(organization, "already-sent", T0 - timedelta(seconds=30), T0 - timedelta(seconds=40))
    sent, _ = _sync(connection, T0)
    assert _ids(sent) == ["already-sent"]

    # This request started before the previous export and finished after it.
    _row(organization, "late-stream", T0 + timedelta(seconds=30), T0 - timedelta(minutes=2))
    _row(organization, "fresh-control", T0 + timedelta(minutes=1), T0 + timedelta(minutes=1))
    sent, _ = _sync(connection, T0 + timedelta(minutes=5))
    assert _ids(sent) == ["late-stream", "fresh-control"]
    assert _sync(connection, T0 + timedelta(minutes=10))[0] == []
    assert connection.total_traces_synced == 3
    assert connection.status == ConnectionStatus.ACTIVE


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_arrival_during_export_is_not_acknowledged_by_that_export(
    organization, workspace, user, platform
):
    connection = _connection(organization, workspace, user, platform)
    _row(organization, "first", T0 - timedelta(seconds=30), T0 - timedelta(seconds=40))

    def arrive():
        _row(organization, "arrived-during-send", T0 + timedelta(seconds=1), T0 - timedelta(minutes=3))

    sent, _ = _sync(connection, T0, on_send=arrive)
    assert _ids(sent) == ["first"]
    sent, _ = _sync(connection, T0 + timedelta(minutes=5))
    assert _ids(sent) == ["arrived-during-send"]
    assert _sync(connection, T0 + timedelta(minutes=10))[0] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_sync_does_not_move_ingestion_floor_or_lose_a_late_request(
    organization, workspace, user, platform
):
    connection = _connection(organization, workspace, user, platform)
    connection.last_synced_at = None
    connection.backfill_completed = False
    connection.save(update_fields=["last_synced_at", "backfill_completed"])
    assert _sync(connection, T0)[0] == []
    assert _sync(connection, T0 + timedelta(minutes=5))[0] == []
    _row(organization, "late-after-empty", T0 + timedelta(minutes=6), T0 - timedelta(minutes=2))
    sent, _ = _sync(connection, T0 + timedelta(minutes=10))
    assert _ids(sent) == ["late-after-empty"]
    assert _sync(connection, T0 + timedelta(minutes=15))[0] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_failed_delivery_keeps_watermark_and_retries_the_same_rows(
    organization, workspace, user, platform
):
    connection = _connection(organization, workspace, user, platform)
    _row(organization, "delivered", T0 - timedelta(seconds=30), T0 - timedelta(seconds=40))
    _sync(connection, T0)
    before = connection.sync_cursor.copy()
    last_success = connection.last_synced_at
    _row(organization, "retry-me", T0 + timedelta(seconds=30), T0 - timedelta(minutes=2))
    _sync(connection, T0 + timedelta(minutes=5), failure=RuntimeError("synthetic refusal"))
    assert connection.status == ConnectionStatus.ERROR
    assert connection.sync_cursor == before
    assert connection.last_synced_at == last_success
    sent, _ = _sync(connection, T0 + timedelta(minutes=10))
    assert _ids(sent) == ["retry-me"]
    assert connection.total_traces_synced == 2


@pytest.mark.django_db(transaction=True)
def test_equal_ingestion_times_use_stable_id_and_preserve_other_cursor_keys(
    organization, workspace, user
):
    connection = _connection(organization, workspace, user, "posthog")
    stamp = T0 - timedelta(seconds=30)
    connection.sync_cursor = {
        "unrelated": {"preserved": True},
        "request_logs": {"from": (T0 - timedelta(minutes=5)).isoformat(),
                         "created_at": stamp.isoformat(), "id": str(UUID(int=2))},
    }
    connection.save(update_fields=["sync_cursor"])
    for i in [3, 1, 2]:
        _row(organization, f"tie-{i}", stamp, T0 - timedelta(minutes=2), row_id=i)
    _row(organization, "null-start", stamp + timedelta(seconds=1), None, row_id=4)
    sent, _ = _sync(connection, T0)
    assert _ids(sent) == ["tie-3", "null-start"]
    assert connection.sync_cursor["unrelated"] == {"preserved": True}
    assert connection.sync_cursor["request_logs"]["id"] == str(UUID(int=4))
    assert _sync(connection, T0 + timedelta(minutes=5))[0] == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", ["posthog", "datadog"])
def test_equal_ingestion_times_across_batch_boundary_are_delivered_once(
    organization, workspace, user, platform
):
    connection = _connection(organization, workspace, user, platform)
    rows = [AgentccRequestLog(
        id=UUID(int=i), organization=organization, workspace=None,
        request_id=f"batch-{i}", model="synthetic", provider="openai", status_code=200,
        started_at=T0 - timedelta(minutes=2),
    ) for i in range(501, 0, -1)]
    AgentccRequestLog.no_workspace_objects.bulk_create(rows)
    AgentccRequestLog.no_workspace_objects.filter(
        organization=organization
    ).update(created_at=T0 - timedelta(seconds=30))
    sent, _ = _sync(connection, T0)
    assert _ids(sent) == [f"batch-{i}" for i in range(1, 502)]
    assert _sync(connection, T0 + timedelta(minutes=5))[0] == []
    assert connection.total_traces_synced == 501


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("arrival", ["newer", "same_time", "empty_capture"])
def test_pre_window_arrival_after_bound_capture_waits_for_next_sync(
    organization, workspace, user, platform, arrival
):
    from django.db.models.query import QuerySet

    connection = _connection(organization, workspace, user, platform)
    stamp = T0 - timedelta(seconds=30)
    first = None
    if arrival != "empty_capture":
        first = _row(organization, "captured", stamp, stamp, row_id=1)
    arrived = None
    original_last = QuerySet.last
    captures = 0

    def last_then_commit(qs):
        nonlocal arrived, captures
        last = original_last(qs)
        if qs.model is AgentccRequestLog:
            captures += 1
            if arrived is None:
                # Simulate a transaction whose created_at is before sync_to,
                # but which commits just after the upper-bound SELECT.
                created = stamp if arrival == "same_time" else stamp + timedelta(seconds=1)
                arrived = _row(
                    organization, "committed-after-capture", created,
                    T0 - timedelta(minutes=2), row_id=2,
                )
        return last

    with patch.object(QuerySet, "last", last_then_commit):
        sent, service = _sync(connection, T0)
    assert captures == 1
    assert arrived is not None
    assert _ids(sent) == (["captured"] if first else [])
    cursor = connection.sync_cursor["request_logs"]
    if first:
        assert cursor["created_at"] == first.created_at.isoformat()
        assert cursor["id"] == str(first.id)
    else:
        assert "created_at" not in cursor
        assert "id" not in cursor
    if platform == "datadog":
        if first:
            series = service.export_metrics.call_args.args[1]
            requests = next(s for s in series if s["metric"] == "agentcc.gateway.requests")
            assert requests["points"][0]["value"] == 1
        else:
            service.export_metrics.assert_not_called()

    next_sent, _ = _sync(connection, T0 + timedelta(minutes=5))
    assert _ids(next_sent) == ["committed-after-capture"]
    assert connection.sync_cursor["request_logs"]["id"] == str(arrived.id)
    assert _sync(connection, T0 + timedelta(minutes=10))[0] == []
    all_ids = _ids(sent + next_sent)
    assert len(all_ids) == len(set(all_ids))
    assert connection.total_traces_synced == len(all_ids)


@pytest.mark.django_db(transaction=True)
def test_datadog_late_logs_keep_event_dates_and_respect_intake_age(
    organization, workspace, user
):
    connection = _connection(organization, workspace, user, "datadog")
    assert _sync(connection, T0)[0] == []
    late = _row(organization, "within-intake", T0 + timedelta(seconds=10), T0 - timedelta(hours=2))
    _row(organization, "too-old", T0 + timedelta(seconds=20), T0 - timedelta(hours=20))
    sent, service = _sync(connection, T0 + timedelta(minutes=5))
    assert _ids(sent) == ["within-intake"]
    assert sent[0]["date"] == late.started_at.isoformat()
    series = service.export_metrics.call_args.args[1]
    requests = next(s for s in series if s["metric"] == "agentcc.gateway.requests")
    assert requests["points"][0]["value"] == 1
    log = SyncLog.objects.filter(connection=connection).latest("started_at")
    assert (log.status, log.traces_fetched) == (SyncStatus.PARTIAL, 1)
    assert "1 request log" in log.error_message
    assert _sync(connection, T0 + timedelta(minutes=10))[0] == []
    assert connection.total_traces_synced == 1
