# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Progress regressions with real discovery and persisted publication state."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics import changes, refresh, size_cap
from tracer.services.derived_metrics.queries import Scope
from tracer.tests.test_derived_metric_changes_clickhouse import (  # noqa: F401
    NOW,
    Analytics,
    store,
)
from tracer.tests.test_derived_metric_changes_clickhouse import (
    seed_span as seed_source_span,
)
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import definition_for


def test_expired_burst_continues_to_normal_completion_without_invalid_phase():
    from tracer.services.derived_metrics.backfill import _retained_repair_checkpoint

    first = NOW - timedelta(days=2)
    cp = changes.ReconciliationCheckpoint(
        NOW,
        floor=first,
        day=first.date(),
        last_day=first.date(),
    )
    moved = _retained_repair_checkpoint(cp, cutoff=NOW)
    assert moved.phase == "sources"
    assert moved.day == moved.last_day == NOW.date()
    assert moved.floor == cp.floor
    assert changes.ReconciliationCheckpoint.from_dict(moved.to_dict()) == moved


@pytest.mark.django_db(transaction=True)
def test_decided_no_head_project_cannot_bypass_source_admission(
    live_writer, workspace, observe_project, settings
):
    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        history_mode="forward", coverage_start=now - timedelta(days=1)
    )
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for index in range(3):
        seed_span(
            writer.store.client,
            scope,
            f"s-{index}",
            uuid4(),
            uuid4(),
            now - timedelta(hours=1),
            resolved=1,
        )
    settings.METRICS_MAX_CHECKPOINT_ROWS = 2
    assert size_cap.pause_at_enrollment(
        writer.lease, Analytics(writer.store.client), retention_days=0
    )
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id is None
    assert (state.paused_reason, state.paused_rows) == ("too_large", 3)
    settings.METRICS_MAX_CHECKPOINT_ROWS = 3
    assert size_cap.resume_within_limit(now=now) == 1
    assert not size_cap.pause_at_enrollment(
        writer.lease, Analytics(writer.store.client), retention_days=0
    )


@pytest.mark.django_db(transaction=True)
def test_capture_overrun_strictly_lowers_effective_budget(live_writer, settings):
    settings.METRICS_PASS_MAX_CAPTURES = 48
    writer = live_writer
    writer.pass_captures = 178
    writer.lease.ticker = lambda: 0
    writer.lease.deadline = 100
    attempt = writer.begin()
    with pytest.raises(refresh.MetricPassYield):
        writer.yield_if_late(attempt)
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert refresh.capture_budget(state) == 24
    attempt.refresh_from_db()
    assert attempt.abort_reason == refresh.PASS_YIELD


@pytest.mark.integration
def test_burst_uses_arrival_ceiling_and_second_burst_finds_old_start(store):
    floor = NOW - timedelta(hours=4)
    # Event time and arrival time intentionally disagree. A start-time ceiling
    # is not a valid replacement for the frozen arrival boundary.
    seed_source_span(
        store,
        span="first-late",
        start=floor,
        version=changes.ns(NOW - timedelta(seconds=1)),
    )
    seed_source_span(
        store,
        span="next-old",
        start=floor + timedelta(minutes=1),
        version=changes.ns(NOW + timedelta(seconds=1)),
    )
    seed_source_span(
        store,
        span="next-young",
        start=NOW + timedelta(hours=1),
        version=changes.ns(NOW + timedelta(seconds=1)),
    )

    def sweep(at, lower, arrival_floor_ns=None):
        # The first assertion must reproduce the old unbounded sweep on base,
        # not stop at an unsupported keyword before reading any source rows.
        window = (
            {} if arrival_floor_ns is None else {"arrival_floor_ns": arrival_floor_ns}
        )
        cp = changes.ReconciliationCheckpoint(at, floor=lower, **window)
        found = set()
        for _ in range(8):
            page = changes.reconcile_page(
                store.analytics,
                org_id=store.org,
                project_id=store.project,
                committed_attempts=(),
                now=at,
                checkpoint=cp,
                page_size=1,
            )
            found.update(k.span_id for k in page.changes.source_keys)
            cp = page.checkpoint
            if cp.phase == "complete":
                return found
        pytest.fail("frozen burst did not converge")

    assert sweep(NOW, floor) == {"first-late"}
    second = NOW + timedelta(minutes=10)
    lower = changes.arrival_start_floor(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        watermark_ns=changes.ns(NOW),
        eval_watermark_ns=changes.ns(NOW),
        remap_cursor_ns=changes.ns(NOW),
        ceiling=second,
    )
    assert lower == floor  # includes the documented overlap
    assert sweep(second, lower) == {"first-late", "next-old", "next-young"}
    # A second overflow after consuming that window must not reread the old
    # burst just because the next arrival has an old event time.
    seed_source_span(
        store,
        span="third-late",
        start=floor,
        version=changes.ns(second + timedelta(seconds=1)),
    )
    third = second + timedelta(minutes=10)
    window = changes.arrival_window(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        watermark_ns=changes.ns(second),
        eval_watermark_ns=changes.ns(second),
        remap_cursor_ns=changes.ns(second),
        ceiling=third,
    )
    assert window.start_floor == floor
    assert sweep(third, window.start_floor, window.source_floor_ns) == {"third-late"}


@pytest.mark.integration
def test_arrival_burst_nominates_backdated_keys_before_coverage(store):
    coverage = NOW - timedelta(hours=1)
    old_start = coverage - timedelta(hours=1)
    seed_source_span(
        store,
        span="backdated",
        start=old_start,
        version=changes.ns(NOW - timedelta(seconds=1)),
    )
    floor_ns = changes.ns(NOW - timedelta(minutes=5))
    page = changes.reconcile_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        now=NOW,
        floor=coverage,
        checkpoint=changes.ReconciliationCheckpoint(
            NOW,
            floor=old_start,
            arrival_floor_ns=floor_ns,
        ),
    )
    assert {key.span_id for key in page.changes.source_keys} == {"backdated"}
    assert page.checkpoint.phase == "complete"


@pytest.mark.integration
def test_remap_burst_keeps_old_source_versions(store):
    old_id, new_id = uuid4(), uuid4()
    at = NOW - timedelta(hours=2)
    seed_source_span(
        store, span="remapped", session=old_id, start=at, version=changes.ns(at)
    )
    store.client.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(old_id, new_id, NOW - timedelta(seconds=1))],
    )
    window = changes.arrival_window(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        watermark_ns=changes.ns(NOW - timedelta(minutes=1)),
        eval_watermark_ns=changes.ns(NOW - timedelta(minutes=1)),
        remap_cursor_ns=changes.ns(NOW - timedelta(minutes=1)),
        ceiling=NOW,
    )
    assert window.start_floor == at
    assert window.source_floor_ns is None
    page = changes.reconcile_page(
        store.analytics,
        org_id=store.org,
        project_id=store.project,
        committed_attempts=(),
        now=NOW,
        checkpoint=changes.ReconciliationCheckpoint(NOW, floor=window.start_floor),
    )
    assert {key.span_id for key in page.changes.source_keys} == {"remapped"}


@pytest.mark.django_db(transaction=True)
def test_26_definition_capture_counts_and_sql_match_bounded_plan(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics.publication import committed_rows_select
    from tracer.services.derived_metrics.storage import FIELD_VALUES

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    first = definition_for(writer, workspace, observe_project)
    definitions = [first]
    for index in range(1, 26):
        metric = DerivedMetric.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            project=observe_project,
            name=f"Capture {index}",
        )
        definitions.append(
            DerivedMetricVersion.objects.create(
                metric=metric, version=1, **first.semantics()
            )
        )
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    units, keys, rows, truth = [], [], [], {}
    for index in range(500):
        session, trace = str(uuid4()), str(uuid4())
        span = f"span-{index}"
        value = float(index % 7)
        truth[session] = value
        units.append(changes.UnitKey("conversation", session))
        keys.append(changes.SourceKey(trace, span))
        rows.append(
            (
                span,
                trace,
                observe_project.id,
                workspace.organization_id,
                session,
                at,
                at + timedelta(seconds=1),
                at,
                at,
                changes.ns(at),
                {"resolved": value},
                {"region": "west"},
            )
        )
    writer.store.client.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number, attrs_string) VALUES",
        rows,
    )
    captured = []
    capture = refresh.capture_source_output

    def counted(store, sql, params, **kwargs):
        rendered = store.client.substitute_params(
            sql, params, store.client.connection.context
        )
        captured.append((kwargs["candidate_count"], len(rendered.encode())))
        assert captured[-1][1] <= 64 * 1024
        return capture(store, sql, params, **kwargs)

    monkeypatch.setattr(refresh, "capture_source_output", counted)
    head = writer.refresh(
        changes.ChangeBatch(tuple(keys), tuple(units), (), (), (), {}),
        definitions,
        scope,
        start=at - timedelta(hours=1),
        end=at + timedelta(hours=1),
        as_of=at + timedelta(hours=1),
        settle_minutes=1,
    )
    assert len(captured) == refresh.estimated_captures(definitions, len(units)) == 26
    assert writer.pass_captures == 26
    assert all(size == 500 for size, _ in captured)
    fields, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=observe_project.id
    )
    actual = writer.store.execute(
        f"SELECT field_id, unit_id, value_number FROM ({fields}) WHERE is_deleted = 0",
        params,
    )
    assert len(actual) == 26 * len(truth)
    for definition in definitions:
        assert {
            unit: value
            for field, unit, value in actual
            if str(field) == str(definition.metric_id)
        } == truth
