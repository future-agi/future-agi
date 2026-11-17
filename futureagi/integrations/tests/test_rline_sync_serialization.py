"""Synthetic concurrent successes must not duplicate or regress delivery.

Requires a PostgreSQL test database: separate thread connections exercise
real row locking, not a mocked queryset. No destination is contacted.
"""

from datetime import datetime, timedelta
from threading import Event, Lock, Thread, current_thread
from unittest.mock import MagicMock, patch

import pytest
from django.db import close_old_connections, connections

from integrations.models import IntegrationConnection, SyncLog, SyncStatus
from integrations.temporal.activities import (
    _sync_export_integration,
    sync_integration_connection,
)

from .test_rline_ingestion_watermark import (
    ACTIVITIES, PLATFORMS, T0, _connection, _ids, _row,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("position", ["later_time", "same_time"])
def test_concurrent_success_cannot_replay_or_overwrite_a_newer_watermark(
    organization, workspace, user, platform, position
):
    connection = _connection(organization, workspace, user, platform)
    connection.sync_cursor = {
        "unrelated": {"preserved": True},
        "request_logs": {
            "from": (T0 - timedelta(minutes=5)).isoformat(),
            "created_at": (T0 - timedelta(minutes=1)).isoformat(),
            "id": "00000000-0000-0000-0000-000000000000",
        },
    }
    connection.save(update_fields=["sync_cursor"])
    stale = IntegrationConnection.no_workspace_objects.get(pk=connection.pk)
    first = _row(
        organization, "first", T0 - timedelta(seconds=30),
        T0 - timedelta(minutes=2), row_id=1,
    )
    sending = Event()
    release = Event()
    contender_done = Event()
    mutex = Lock()
    delivered = []
    failures = []

    def send(*args):
        payload = args[-1]
        with mutex:
            delivered.extend(payload)
        if current_thread().name == "rline-slow":
            sending.set()
            assert release.wait(30), "slow export was not released"
        return {"sent": len(payload)}

    def service_for(_platform):
        service = MagicMock()
        service.export_events.side_effect = send
        service.export_logs.side_effect = send
        service.publish_logs.side_effect = send
        service.export_metrics.side_effect = lambda credentials, series: {"sent": len(series)}
        return service

    def run(done=None):
        close_old_connections()
        try:
            sync_integration_connection(str(connection.id), retry=True)
        except BaseException as exc:
            with mutex:
                failures.append(exc)
        finally:
            connections.close_all()
            if done:
                done.set()

    slow = Thread(target=run, name="rline-slow", daemon=True)
    contender = Thread(target=run, args=(contender_done,), name="rline-contender", daemon=True)
    with (
        patch(f"{ACTIVITIES}.datetime", wraps=datetime) as clock,
        patch("integrations.services.base.get_integration_service", side_effect=service_for),
    ):
        clock.now.return_value = T0
        slow.start()
        try:
            assert sending.wait(15), "first export never reached its destination"
            newer = _row(
                organization, "newer",
                first.created_at if position == "same_time" else T0 - timedelta(seconds=20),
                T0 - timedelta(minutes=3), row_id=2,
            )
            # On RED this job delivers first+newer and acknowledges newer while
            # the older first-only export is still awaiting its successful reply.
            contender.start()
            assert contender_done.wait(15), "overlapping dispatch should yield to its owner"
            connection.refresh_from_db()
            observed = connection.sync_cursor["request_logs"].copy()
        finally:
            release.set()
            slow.join(15)
            if contender.ident is not None:
                contender.join(15)
        assert not slow.is_alive() and not contender.is_alive()
        assert not failures, failures
        connection.refresh_from_db()
        saved = connection.sync_cursor["request_logs"]
        assert (saved["created_at"], saved["id"]) >= (observed["created_at"], observed["id"])

        # A previously fetched instance must never supply the next delivery
        # cursor: reload it under ownership before deciding what to send.
        _sync_export_integration(stale)
        sync_integration_connection(str(connection.id), retry=True)

    connection.refresh_from_db()
    assert sorted(_ids(delivered)) == ["first", "newer"]
    assert len(_ids(delivered)) == len(set(_ids(delivered)))
    assert connection.total_traces_synced == 2
    assert connection.sync_cursor["request_logs"]["id"] == str(newer.id)
    assert connection.sync_cursor["request_logs"]["created_at"] == newer.created_at.isoformat()
    assert connection.sync_cursor["unrelated"] == {"preserved": True}
    statuses = list(SyncLog.objects.filter(connection=connection).values_list("status", flat=True))
    assert statuses.count(SyncStatus.SUCCESS) == 2
    assert statuses.count(SyncStatus.NO_NEW_DATA) == 1
    assert len(statuses) == 3
