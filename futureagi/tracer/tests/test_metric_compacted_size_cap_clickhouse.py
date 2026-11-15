# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Private range bounds may be purged without resetting stored-row accounting."""

import pytest

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import retention, size_cap
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.refresh import TABLE_KINDS
from tracer.tests.test_derived_metric_compaction_ranges_clickhouse import seed_all
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)


@pytest.mark.django_db(transaction=True)
def test_server_checkpoint_size_survives_private_purge_and_growth(
    live_writer, settings
):
    writer = live_writer
    # Small semantic fixture, NOT certification of the default 300k threshold
    # or of the provisional 800k cap. Exercise actual automatic path selection.
    settings.METRICS_SERVER_SIDE_MIN_ROWS = 100
    initial = seed_all(writer, count=17)
    assert size_cap.head_rows(initial) > settings.METRICS_SERVER_SIDE_MIN_ROWS
    head = writer.compact()
    assert head.published_by.source_ceilings["range_compaction"] is True
    assert not head.published_by.batches.exists()
    assert head.published_by.compaction_ranges.exists()

    measured = 0
    for table in TABLE_KINDS.values():
        sql, params = committed_rows_select(
            table, head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        measured += writer.store.execute(f"SELECT count() FROM ({sql})", params)[0][0]
    assert size_cap.head_rows(head) == measured
    retention._purge_terminal_payloads(writer.lease.project_id)
    assert not head.published_by.compaction_ranges.exists()
    assert size_cap.head_rows(head) == measured

    settings.METRICS_MAX_CHECKPOINT_ROWS = measured
    assert not size_cap.pause_if_too_large(
        writer.lease.project_id, size_cap.head_rows(head), now=writer.lease.clock()
    )
    attempt, _ = prepare_field(writer, unit_id="new-growth", n=5)
    grown = writer.stage_and_publish(attempt)
    assert size_cap.head_rows(grown) == measured + 1
    assert size_cap.pause_if_too_large(
        writer.lease.project_id, size_cap.head_rows(grown), now=writer.lease.clock()
    )
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert (state.paused_reason, state.paused_rows) == ("too_large", measured + 1)
