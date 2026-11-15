# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Published burst corrections must include a later-page member's old owner."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import changes
from tracer.services.derived_metrics.backfill import queue_bulk_change
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import SOURCE_DEPENDENCIES
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_backfill_clickhouse import (  # noqa: F401
    context,
    fields,
    history_writer,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    metric_bucket_rows,
    seed_span_revision,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("revision_kind", "fail_owner_read"),
    [("move", False), ("move_delete", False), ("update", False), ("move", True)],
)
def test_partial_repair_then_overflow_matches_latest_source(
    history_writer, workspace, observe_project, monkeypatch, revision_kind,
    fail_owner_read,
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project, retention=0, page_size=1)
    now = datetime.now(UTC).replace(microsecond=0)
    writer.lease.clock = lambda: now
    at = now - timedelta(hours=2)
    watermark = changes.ns(now - timedelta(minutes=20))
    definition = make_version(
        observe_project,
        workspace,
        active=True,
        kind="total",
        formula="sum(x)",
        history_mode="forward",
        coverage_start=at - timedelta(hours=1),
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "sum",
            }
        ],
    )
    a, b, c, trace = uuid4(), uuid4(), uuid4(), uuid4()
    # Keep the opportunistic physical preimage available, as in the finding.
    # Committed dependencies, not these rows, provide durability after merges.
    writer.store.execute("SYSTEM STOP MERGES spans")
    for span, value in (("s1", 10), ("s2", 20)):
        seed_span_revision(
            writer, scope, trace, span, a, at, value, revision=changes.ns(at)
        )
    MetricProjectRefresh.objects.filter(project=observe_project).update(
        watermark_ns=watermark, eval_watermark_ns=watermark, remap_cursor_ns=watermark
    )
    queue_bulk_change(observe_project.id, now=now - timedelta(minutes=10))
    head = runner.run_repair_page(skip_incremental=True)
    assert {row[0]: row[2] for row in fields(writer, head, definition)} == {str(a): 30}
    dependencies, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=observe_project.id,
    )
    assert writer.store.execute(
        f"SELECT span_id FROM ({dependencies}) WHERE is_deleted = 0", params
    ) == [("s1",)]
    before = MetricProjectRefresh.for_project(observe_project.id)
    assert before.pending_repair and before.reconciliation_cursor["phase"] == "sources"
    assert before.watermark_ns == watermark
    seed_span_revision(
        writer,
        scope,
        trace,
        "s2",
        a if revision_kind == "update" else b,
        at,
        25 if revision_kind == "update" else 20,
        revision=changes.ns(now - timedelta(minutes=1)),
        deleted=int(revision_kind == "move_delete"),
    )
    seed_span_revision(
        writer, scope, trace, "s3", c, at, 7,
        revision=changes.ns(now - timedelta(minutes=1)),
    )
    # Exercise the real overflow catch/queue with a tiny discovery fixture;
    # production budgets and the sweep's bounded page size remain unchanged.
    monkeypatch.setattr(changes.arrivals, "_MAX_PUBLIC_CANDIDATES", 1)
    if fail_owner_read:
        read = changes.read

        def fail_preimages(analytics, query, params, budget, **kwargs):
            if "SELECT DISTINCT trace_session_id AS raw_session_id" in query:
                raise changes.MetricChangeReadError("owner-read fixture limit")
            return read(analytics, query, params, budget, **kwargs)

        with monkeypatch.context() as failing:
            failing.setattr(changes, "read", fail_preimages)
            with pytest.raises(changes.MetricChangeReadError, match="owner-read fixture"):
                runner.run_repair_page()
        unchanged = MetricProjectRefresh.for_project(observe_project.id)
        assert unchanged.head_manifest_id == head.id
        assert unchanged.watermark_ns == watermark
        assert unchanged.reconciliation_cursor["after"] is None
        assert unchanged.reconciliation_cursor["scanned"] == 0
    head = runner.run_repair_page(skip_incremental=fail_owner_read)
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.pending_repair
    assert state.reconciliation_cursor["arrival_floor_ns"] > changes.ns(at)
    burst_ceiling = changes.ns(
        datetime.fromisoformat(state.reconciliation_cursor["started_at"])
    )
    assert state.watermark_ns == watermark
    for _ in range(6):
        head = runner.run_repair_page(skip_incremental=True)
        state = MetricProjectRefresh.for_project(observe_project.id)
        if not state.pending_repair:
            break
        assert state.watermark_ns == watermark
    else:
        pytest.fail("bounded arrival sweep did not complete")
    assert state.watermark_ns == burst_ceiling
    assert state.reconciliation_cursor["completed_passes"] == 0

    # Independent oracle: collapse raw source identities BEFORE filtering live
    # membership, then sum by session. No metric compiler or metric fields.
    truth = dict(writer.store.execute(
        "SELECT toString(trace_session_id), sum(attrs_number['resolved']) "
        "FROM (SELECT * FROM spans WHERE project_id = toUUID(%(project)s) "
        "ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id) "
        "WHERE is_deleted = 0 GROUP BY trace_session_id",
        {"project": str(observe_project.id)},
    ))
    actual = {row[0]: row[2] for row in fields(writer, head, definition)}
    assert actual == truth
    buckets = metric_bucket_rows(writer, head, definition)
    assert sum(row[3] for row in buckets) == sum(truth.values())
    assert sum(row[2] for row in buckets) == len(truth)
