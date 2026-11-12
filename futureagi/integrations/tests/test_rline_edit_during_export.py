"""A real connection Edit must not undo an export's successful acknowledgement.

PostgreSQL only: two worker connections exercise the actual PATCH and row
lock, with synthetic destination acknowledgements and no external requests.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event, Thread
from time import monotonic
from unittest.mock import MagicMock, patch

import pytest
from django.db import close_old_connections, connections

from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.temporal.activities import sync_integration_connection
from integrations.views.integration_connection import IntegrationConnectionViewSet

from .test_rline_ingestion_watermark import (
    ACTIVITIES, T0, _connection, _ids, _row, _sync,
)


@pytest.mark.django_db(transaction=True)
def test_edit_waiting_for_success_preserves_watermark_and_next_sync_sends_nothing(
    auth_client, organization, workspace, user
):
    assert connections["default"].vendor == "postgresql", "Requires PostgreSQL row locks"
    connection = _connection(organization, workspace, user, "posthog")
    before = {
        "unrelated": {"preserved": True},
        "request_logs": {
            "from": (T0 - timedelta(minutes=5)).isoformat(),
            "created_at": (T0 - timedelta(minutes=1)).isoformat(),
            "id": "00000000-0000-0000-0000-000000000000",
        },
    }
    connection.sync_cursor = before
    connection.status = ConnectionStatus.ERROR
    connection.status_message = "Synthetic prior failure"
    connection.backfill_completed = False
    connection.save(update_fields=[
        "sync_cursor", "status", "status_message", "backfill_completed",
    ])
    row = _row(
        organization, "acknowledged-once", T0 - timedelta(seconds=30),
        T0 - timedelta(minutes=2), row_id=1,
    )
    sending, release, edit_read, edit_done = Event(), Event(), Event(), Event()
    pids, observed = {}, {}
    delivered, responses, failures = [], [], []
    original_get_object = IntegrationConnectionViewSet.get_object

    def backend_pid():
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            return cursor.fetchone()[0]

    def get_object(view):
        instance = original_get_object(view)
        pids["edit"] = backend_pid()
        observed["cursor"] = deepcopy(instance.sync_cursor)
        observed["status"] = instance.status
        edit_read.set()
        return instance

    def send(*args):
        # PostHog passes (host_url, credentials, events); Mixpanel passes (credentials, events).
        payload = args[-1]
        delivered.extend(payload)
        pids["export"] = backend_pid()
        sending.set()
        assert release.wait(30), "successful export was not released"
        return {"sent": len(payload)}

    def export():
        sync_integration_connection(str(connection.id), retry=True)

    def edit():
        responses.append(auth_client.patch(
            f"/integrations/connections/{connection.id}/",
            {"display_name": "Renamed during export"}, format="json",
        ))

    def run(action, done=None):
        close_old_connections()
        try:
            action()
        except BaseException as exc:
            failures.append(exc)
        finally:
            connections.close_all()
            if done:
                done.set()

    exporter = Thread(target=run, args=(export,), daemon=True)
    editor = Thread(target=run, args=(edit, edit_done), daemon=True)
    service = MagicMock()
    service.export_events.side_effect = send
    with (
        patch(f"{ACTIVITIES}.datetime", wraps=datetime) as clock,
        patch("integrations.services.base.get_integration_service", return_value=service),
        patch.object(IntegrationConnectionViewSet, "get_object", get_object),
    ):
        clock.now.return_value = T0
        exporter.start()
        try:
            assert sending.wait(15), "export never reached the synthetic destination"
            editor.start()
            assert edit_read.wait(15), "PATCH never read the connection"
            assert observed == {"cursor": before, "status": ConnectionStatus.ERROR}
            assert pids["export"] != pids["edit"]

            # Observe PostgreSQL's actual lock wait, not a timing assumption or
            # a mocked save. Release the successful send only after Edit blocks.
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline and not edit_done.is_set():
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))",
                        [pids["export"], pids["edit"]],
                    )
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                edit_done.wait(0.01)
            assert blocked, "PATCH did not wait for the export's row lock"
            assert not edit_done.is_set()
        finally:
            release.set()
            exporter.join(15)
            if editor.ident is not None:
                editor.join(15)
        assert not exporter.is_alive() and not editor.is_alive()
        assert not failures, failures
        assert len(responses) == 1
        assert responses[0].status_code == 200, responses[0].json()
        service.export_events.assert_called_once()

    connection.refresh_from_db()
    assert connection.display_name == "Renamed during export"
    assert connection.sync_cursor == {
        **before,
        "request_logs": {
            **before["request_logs"],
            "created_at": row.created_at.isoformat(),
            "id": str(row.id),
        },
    }
    assert connection.last_synced_at == T0
    assert connection.total_traces_synced == 1
    assert connection.backfill_completed is True
    assert (connection.status, connection.status_message) == (ConnectionStatus.ACTIVE, "")
    assert _ids(delivered) == ["acknowledged-once"]
    first_log = SyncLog.objects.get(connection=connection)
    assert (first_log.status, first_log.traces_created) == (SyncStatus.SUCCESS, 1)
    acknowledged = deepcopy(connection.sync_cursor)

    resent, next_service = _sync(connection, T0 + timedelta(minutes=5))
    assert resent == []
    next_service.export_events.assert_not_called()
    assert connection.sync_cursor == acknowledged
    assert connection.total_traces_synced == 1
    assert list(SyncLog.objects.filter(connection=connection).order_by("started_at")
                .values_list("status", flat=True)) == [
        SyncStatus.SUCCESS, SyncStatus.NO_NEW_DATA,
    ]
