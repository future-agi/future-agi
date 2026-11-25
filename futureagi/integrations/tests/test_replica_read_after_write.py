"""Integration sync read-after-write regressions with a frozen PG replica."""

from unittest.mock import MagicMock

import pytest

from integrations.models import SyncLog, SyncStatus
from integrations.temporal.activities import sync_integration_connection
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace


@pytest.mark.django_db(transaction=True)
def test_sync_does_not_backfill_a_trace_repaired_in_the_current_page(
    lagging_replica,  # noqa: F811
    integration_connection,
    monkeypatch,
):
    """C10: the outer trace query and its span subquery see the same repair.

    The empty trace is replicated before sync starts, isolating this regression
    from the Langfuse identity lookup. Only the network service is replaced;
    sync still transforms and upserts the actual trace and observation rows.
    """
    connection = integration_connection
    trace = Trace.objects.create(project=connection.project, external_id="repair")
    service = MagicMock()
    service.fetch_traces.return_value = {
        "traces": [{"id": "repair"}],
        "has_more": False,
    }
    service.fetch_trace_detail.return_value = {
        "id": "repair",
        "name": "repaired trace",
        "observations": [{"id": "repaired-child", "type": "SPAN", "name": "child"}],
    }
    monkeypatch.setattr(
        "integrations.services.base.get_integration_service", lambda _: service
    )
    monkeypatch.setattr("integrations.temporal.activities.time.sleep", lambda _: None)
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")
    lagging_replica.freeze()

    sync_integration_connection(str(connection.id))

    service.fetch_trace_detail.assert_called_once()
    log = SyncLog.objects.get(connection=connection)
    assert log.status == SyncStatus.SUCCESS
    assert log.traces_fetched == 1
    assert log.traces_updated == 1
    assert log.spans_synced == 1
    connection.refresh_from_db()
    assert connection.total_spans_synced == 1
    assert (
        Trace.objects.using("default")
        .filter(project=connection.project, external_id="repair")
        .count()
        == 1
    )
    assert ObservationSpan.objects.using("default").filter(trace=trace).count() == 2
