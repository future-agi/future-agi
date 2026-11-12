"""Initial event-time exclusions are covered, not deferred into the next sync."""

from datetime import timedelta

import pytest

from integrations.models import ConnectionStatus, SyncLog, SyncStatus

from .test_rline_ingestion_watermark import (
    PLATFORMS, T0, _connection, _ids, _row, _sync,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("eligible_first", [True, False])
def test_bootstrap_age_exclusion_is_not_reoffered_after_retry(
    organization, workspace, user, platform, eligible_first
):
    connection = _connection(organization, workspace, user, platform)
    connection.status = ConnectionStatus.BACKFILLING
    connection.last_synced_at = None
    connection.backfill_completed = False
    connection.backfill_from = T0 - timedelta(hours=4)
    connection.save(update_fields=[
        "status", "last_synced_at", "backfill_completed", "backfill_from",
    ])
    if eligible_first:
        _row(organization, "recent", T0 - timedelta(seconds=30), T0 - timedelta(hours=1))
    excluded = _row(
        organization, "excluded-before-first-sync", T0 - timedelta(seconds=20),
        T0 - timedelta(hours=20),
    )
    sent, _ = _sync(connection, T0)
    assert _ids(sent) == (["recent"] if eligible_first else [])
    first = SyncLog.objects.get(connection=connection)
    assert first.status == (SyncStatus.SUCCESS if eligible_first else SyncStatus.NO_NEW_DATA)
    cursor = connection.sync_cursor["request_logs"]
    assert cursor.get("created_at") == excluded.created_at.isoformat()
    assert cursor.get("id") == str(excluded.id)

    # A genuine destination failure must not acknowledge the next ingestion.
    late = _row(
        organization, "late-after-first-sync", T0 + timedelta(seconds=30),
        T0 - timedelta(hours=2),
    )
    before = connection.sync_cursor.copy()
    _sync(connection, T0 + timedelta(minutes=5), failure=RuntimeError("synthetic refusal"))
    failed = SyncLog.objects.filter(connection=connection).latest("started_at")
    assert failed.status == SyncStatus.FAILED
    assert connection.sync_cursor == before
    assert connection.status == ConnectionStatus.ERROR

    recovered, _ = _sync(connection, T0 + timedelta(minutes=10))
    assert _ids(recovered) == ["late-after-first-sync"]
    recovery = SyncLog.objects.filter(connection=connection).latest("started_at")
    assert (recovery.status, recovery.traces_fetched) == (SyncStatus.SUCCESS, 1)
    assert not recovery.error_message
    assert connection.sync_cursor["request_logs"]["id"] == str(late.id)
    assert connection.total_traces_synced == (2 if eligible_first else 1)
    assert _sync(connection, T0 + timedelta(minutes=15))[0] == []
    assert SyncLog.objects.filter(connection=connection).latest("started_at").status == SyncStatus.NO_NEW_DATA
    all_ids = _ids(sent + recovered)
    assert len(all_ids) == len(set(all_ids))
    assert "excluded-before-first-sync" not in all_ids
