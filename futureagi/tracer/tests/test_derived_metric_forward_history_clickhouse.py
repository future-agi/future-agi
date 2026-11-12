# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Start with no history, against a real ClickHouse 25 sidecar.

The size count stops at the limit, the forward-only sweep never reads before its
coverage start, a real Code 241 keeps its cause chain, and a forward-only
version computes only units (and spans) that start at or after coverage_start.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetricVersion
from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.services.derived_metrics import backfill, changes
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    Dependency,
    MetricReadBudget,
    SourceKey,
    UnitKey,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_changes_clickhouse import (  # noqa: F401
    NOW,
    OLD,
    complete_repair,
    discover,
    query_reads,
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
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    definition_for,
    metric_bucket_rows,
    seed_span_revision,
    span_definition_for,
)


def _seed_rows(store, project, at, count, prefix):
    store.client.execute(
        "INSERT INTO spans (project_id, trace_id, id, start_time, _version) "
        "SELECT toUUID(%(project)s), concat(%(prefix)s, toString(intDiv(number, 10))), "
        "concat(%(prefix)s, toString(number)), "
        "toDateTime64(%(at)s, 6, 'UTC') + toIntervalSecond(number), 1 "
        "FROM numbers(%(count)s)",
        {
            "project": project,
            "prefix": prefix,
            "at": at.strftime("%Y-%m-%d %H:%M:%S"),
            "count": count,
        },
    )


@pytest.mark.integration
def test_history_window_count_stops_at_the_limit_and_counts_only_the_window(store):
    rows = 20_000
    _seed_rows(store, store.project, NOW - timedelta(days=10), rows, "in-")
    # Outside the window, and another project's spans, never count.
    _seed_rows(store, store.project, NOW - timedelta(days=100), 500, "old-")
    _seed_rows(store, str(uuid4()), NOW - timedelta(days=10), 3_000, "other-")

    def count(limit, retention_days=0):
        return backfill.history_window_spans(
            store.analytics,
            store.project,
            now=NOW,
            retention_days=retention_days,
            limit=limit,
        )

    assert count(50_000) == rows
    # Exactly the limit is not above it: the caller keeps history.
    assert count(rows) == rows
    assert count(rows - 1) == rows
    store.analytics.query_ids.clear()
    assert count(999) == 1_000
    reads = query_reads(store, store.analytics.query_ids[-1])
    # The LIMIT stops the read near the cap: no full scan, no GROUP BY.
    assert reads < rows // 4, reads
    (query, _, settings) = store.analytics.calls[-1]
    assert "GROUP BY" not in query and settings["max_memory_usage"] == 256 * 1024**2
    # A shorter retention narrows the window exactly like the backfill window.
    assert count(50_000, retention_days=5) == 0
    print("forward-history size count read_rows", reads, "of", rows)


@pytest.mark.integration
def test_forward_floor_sweep_never_nominates_or_reads_days_before_coverage(store):
    floor = NOW - timedelta(hours=6)
    for span, at in (
        ("before-old", OLD),
        ("before-floor", floor - timedelta(hours=1)),
        ("after-a", floor + timedelta(hours=1)),
        ("after-b", floor + timedelta(hours=3)),
    ):
        seed_source_span(store, span=span, start=at)
    store.analytics.calls.clear()
    pages = complete_repair(store, floor=floor, page_size=50)
    assert pages[-1].checkpoint.phase == "complete"
    swept = {k.span_id for page in pages for k in page.changes.source_keys}
    assert swept == {"after-a", "after-b"}
    sources = [
        params
        for query, params, _ in store.analytics.calls
        if "toStartOfHour(start_time) AS hour" in query
    ]
    assert sources
    for params in sources:
        lower = datetime.fromisoformat(params["day_start"]).replace(tzinfo=UTC)
        assert lower >= floor, params["day_start"]
    days = [
        params for query, params, _ in store.analytics.calls if "first_day" in query
    ]
    assert days and all("source_floor" in params for params in days)
    # Without a floor (any history version on the project) nothing changes.
    full = complete_repair(store, page_size=50)
    assert {k.span_id for page in full for k in page.changes.source_keys} == {
        "before-old",
        "before-floor",
        "after-a",
        "after-b",
    }


@pytest.mark.integration
def test_real_discovery_memory_limit_keeps_code_241_in_its_cause_chain(store):
    for i in range(50):
        seed_source_span(
            store,
            span=f"burst-{i}",
            start=NOW - timedelta(minutes=30),
            version=changes.ns(NOW - timedelta(seconds=30)),
        )
    with pytest.raises(arrivals.ContinuousCandidateReadError) as raised:
        discover(store, budget=MetricReadBudget(max_memory_bytes=4096))
    assert not isinstance(raised.value, arrivals.ContinuousCandidateOverflow)
    # The writer reclassifies exactly this failure as an overflow.
    assert changes.memory_limit_exceeded(raised.value)


def _version_fields(writer, head, definition):
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT unit_id, value_number FROM ({sql}) "
        "WHERE field_id = toUUID(%(test_field)s) AND field_version = %(test_version)s "
        "AND is_deleted = 0 ORDER BY unit_id",
        {
            **params,
            "test_field": str(definition.metric_id),
            "test_version": definition.version,
        },
    )


def _forward(definition, coverage):
    DerivedMetricVersion.objects.filter(id=definition.id).update(
        history_mode="forward", coverage_start=coverage
    )
    definition.refresh_from_db()
    return definition


@pytest.mark.django_db(transaction=True)
def test_forward_conversation_version_computes_only_units_from_coverage_start(
    live_writer, workspace, observe_project
):
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    coverage = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    forward = _forward(definition_for(writer, workspace, observe_project), coverage)
    history = DerivedMetricVersion.objects.create(
        metric=forward.metric,
        version=2,
        **{**forward.semantics(), "definition_hash": "b" * 64},
    )
    user = uuid4()
    seeded = []
    for name, at, value in (
        ("before", coverage - timedelta(minutes=40), 4),
        ("after", coverage + timedelta(minutes=20), 8),
    ):
        session, trace = uuid4(), uuid4()
        seed_span(
            writer.store.client,
            scope,
            f"{name}-span",
            session,
            user,
            at,
            resolved=value,
            trace_id=trace,
        )
        seeded.append(
            (
                session,
                Dependency(
                    str(trace),
                    f"{name}-span",
                    str(session),
                    str(session),
                    str(user),
                    str(user),
                    at,
                ),
            )
        )
    (before, before_dep), (after, after_dep) = seeded
    change = ChangeBatch(
        (SourceKey(before_dep.trace_id, before_dep.span_id),)
        + (SourceKey(after_dep.trace_id, after_dep.span_id),),
        (UnitKey("conversation", str(before)), UnitKey("conversation", str(after))),
        (),
        (before_dep, after_dep),
        (),
        {"watermark_ns": 1},
    )
    head = writer.refresh(
        change,
        [forward, history],
        scope,
        start=coverage - timedelta(days=1),
        end=coverage + timedelta(days=1),
        as_of=coverage + timedelta(hours=1, minutes=20),
    )
    assert _version_fields(writer, head, forward) == [(str(after), 8.0)]
    # The same pass and source still compute both units for a history version.
    assert sorted(_version_fields(writer, head, history)) == sorted(
        [(str(before), 4.0), (str(after), 8.0)]
    )


@pytest.mark.django_db(transaction=True)
def test_forward_span_version_computes_only_spans_from_coverage_start(
    live_writer, workspace, observe_project
):
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    coverage = datetime.now(UTC).replace(minute=7, second=0, microsecond=0) - timedelta(
        hours=3
    )
    span = _forward(span_definition_for(writer, workspace, observe_project), coverage)
    session, trace = uuid4(), uuid4()
    before_at = coverage - timedelta(minutes=30)
    after_at = coverage + timedelta(minutes=20)
    for name, at, value in (("before", before_at, 4), ("after", after_at, 6)):
        seed_span_revision(writer, scope, trace, name, session, at, value, revision=1)
    dependencies = [
        Dependency(str(trace), name, str(session), str(session), "", "", at)
        for name, at in (("before", before_at), ("after", after_at))
    ]
    head = writer.refresh(
        change_for(session, dependencies=dependencies),
        [span],
        scope,
        start=before_at - timedelta(days=1),
        end=after_at + timedelta(days=1),
        as_of=after_at + timedelta(hours=1),
    )
    rows = metric_bucket_rows(writer, head, span)
    assert len(rows) == 1, rows
    bucket = rows[0][0] if rows[0][0].tzinfo else rows[0][0].replace(tzinfo=UTC)
    # Only the span after coverage_start (its 15-minute bucket) is computed.
    assert bucket == after_at.replace(minute=after_at.minute // 15 * 15)
    assert rows[0][2:] == (1, 6.0, 1.0)
