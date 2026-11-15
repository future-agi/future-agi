# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Private range bounds may be purged without resetting stored-row accounting."""

import pytest

from tracer.models.derived_metric import MetricProjectRefresh, MetricStateConflict
from tracer.services.derived_metrics import (
    admission,
    backfill,
    compaction_ranges,
    retention,
    size_cap,
)
from tracer.services.derived_metrics.publication import (
    MAX_COMPACTION_ROWS,
    committed_rows_select,
)
from tracer.services.derived_metrics.refresh import (
    DEFAULT_SERVER_SIDE_MIN_ROWS,
    TABLE_KINDS,
)
from tracer.services.derived_metrics.storage import SOURCE_DEPENDENCIES
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


@pytest.mark.django_db(transaction=True)
def test_old_purged_range_receipt_requires_truthful_checkpoint_rebuild(
    live_writer, monkeypatch, settings
):
    writer = live_writer
    seed_all(writer, count=2)
    receipt = compaction_ranges.visibility_receipts

    def old_envelope(attempt):
        # Older writers publish exactly this valid envelope: the registry's
        # count counts ranges, NOT output rows. No fabricated database receipt.
        return [
            {key: value for key, value in entry.items() if key != "row_count"}
            for entry in receipt(attempt)
        ]

    with monkeypatch.context() as old_writer:
        old_writer.setattr(compaction_ranges, "visibility_receipts", old_envelope)
        head = writer.compact(server_side=True)
    measured = size_cap.head_rows(head)  # old envelope, private ranges still present
    assert measured > 0
    retention._purge_terminal_payloads(writer.lease.project_id)
    with pytest.raises(MetricStateConflict, match="row-count receipt unavailable"):
        size_cap.head_rows(head)
    # Unknown is not zero: no scheduled fresh work may grow this head before
    # its count is restored. Bind the dispatcher to this real fixture writer.
    settings.METRICS_RETENTION_DAYS_PROVIDER = "fixture.unlimited"

    def unexpected_enrollment(*args, **kwargs):
        pytest.fail("fresh work started on an unmeasurable checkpoint")

    with monkeypatch.context() as dispatch:
        dispatch.setattr(admission, "import_string", lambda _: lambda org: 0)
        dispatch.setattr(admission, "_ScheduledRefreshWriter", lambda *a, **k: writer)
        dispatch.setattr(backfill, "enter_backfill", unexpected_enrollment)
        with pytest.raises(MetricStateConflict, match="row-count receipt unavailable"):
            admission.run_project_pass(writer.lease, "repair")
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        == head.id
    )
    # An explicit maintenance rebuild uses the actual committed CH rows. It
    # does not guess a count, clear a safety flag, or retain private bounds.
    rebuilt = writer.compact(server_side=True)
    retention._purge_terminal_payloads(writer.lease.project_id)
    assert size_cap.head_rows(rebuilt) == measured


@pytest.mark.django_db(transaction=True)
def test_default_server_threshold_then_purge_and_growth(live_writer, settings):
    writer = live_writer
    settings.METRICS_SERVER_SIDE_MIN_ROWS = DEFAULT_SERVER_SIDE_MIN_ROWS
    count = DEFAULT_SERVER_SIDE_MIN_ROWS + 1
    attempt = writer.begin(purpose="compaction")
    columns = []
    for name, kind in SOURCE_DEPENDENCIES.schema:
        literal = kind.replace("'", "\\'")
        value = f"defaultValueOfTypeName('{literal}')"
        if name in {"trace_id", "span_id"}:
            value = "concat('key-', leftPad(toString(number), 8, '0'))"
        elif name == "unit_start":
            value = "toDateTime('2026-10-20 00:00:00', 'UTC')"
        columns.append(f"{value} AS {name}")
    writer.freeze(
        attempt,
        SOURCE_DEPENDENCIES,
        "SELECT " + ", ".join(columns) + f" FROM numbers({count})",
        row_limit=MAX_COMPACTION_ROWS,
        collect_payloads=False,
    )
    writer.seal(attempt)
    initial = writer.stage_and_publish(attempt)
    assert size_cap.head_rows(initial) == count
    head = writer.compact()
    assert head.published_by.source_ceilings["range_compaction"] is True
    assert writer.store.execute(
        "SELECT count() FROM metric_source_dependencies "
        "WHERE attempt_id = toUUID(%(attempt)s)",
        {"attempt": str(head.published_by_id)},
    ) == [(count,)]
    retention._purge_terminal_payloads(writer.lease.project_id)
    assert not head.published_by.compaction_ranges.exists()
    assert size_cap.head_rows(head) == count
    settings.METRICS_MAX_CHECKPOINT_ROWS = count
    growth, _ = prepare_field(writer, unit_id="threshold-growth", n=5)
    grown = writer.stage_and_publish(growth)
    assert size_cap.head_rows(grown) == count + 1
    assert size_cap.pause_if_too_large(
        writer.lease.project_id, size_cap.head_rows(grown), now=writer.lease.clock()
    )
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).paused_rows
        == count + 1
    )
