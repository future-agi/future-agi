# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Start with no history, against a real ClickHouse 25 sidecar.

The size count stops at the limit, the forward-only sweep never reads before its
coverage start, a real Code 241 keeps its cause chain, and a forward-only
version computes only units (and spans) that start at or after coverage_start.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricProjectRefresh,
)
from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.services.derived_metrics import admission, backfill, changes, reads
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    Dependency,
    MetricReadBudget,
    ReconciliationCheckpoint,
    SourceKey,
    UnitKey,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.service import MetricUnavailableError
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_changes_clickhouse import (  # noqa: F401
    NOW,
    OLD,
    Analytics,
    complete_repair,
    discover,
    query_reads,
    seed_dependency,
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("enabled_metrics")
def test_fresh_large_project_first_pass_publishes_values_from_coverage_start(
    live_writer, workspace, observe_project, settings
):
    """The scheduled pass on a fresh large project: decide, discover, publish, read.

    enter_backfill then the same pass's run_incremental on an EMPTY head, with
    the real size count and real discovery: no history page runs, discovery
    starts at enrollment, only units from coverage_start get values, and reads
    go from "not calculated yet" to values once the first head is published.
    """
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    definition = definition_for(writer, workspace, observe_project)
    # Off a 15-minute boundary: the bucket holding it is only partly computed.
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    enrolled = hour - timedelta(hours=3) + timedelta(minutes=7, seconds=30)
    first_bucket = enrolled + timedelta(minutes=7, seconds=30)
    DerivedMetricVersion.objects.filter(id=definition.id).update(created_at=enrolled)
    sessions = {}
    for name, start, arrival, value in (
        # Ingested before enrollment: discovery never reads it.
        ("before", enrolled - timedelta(hours=1), enrolled - timedelta(hours=1), 2),
        # Ingested after enrollment but started before it: not this version's.
        ("late", enrolled - timedelta(minutes=30), enrolled + timedelta(hours=1), 4),
        ("after", enrolled + timedelta(hours=1), enrolled + timedelta(hours=1), 8),
    ):
        sessions[name] = uuid4()
        seed_span_revision(
            writer,
            scope,
            uuid4(),
            f"{name}-span",
            sessions[name],
            start,
            value,
            revision=changes.ns(arrival),
            arrival=arrival,
        )
    settings.METRICS_BACKFILL_MAX_SPANS = 2
    analytics = Analytics(writer.store.client)
    assert backfill.enter_backfill(
        writer.lease, analytics, retention_days=lambda: 0
    ) == {str(definition.id): "forward"}
    definition.refresh_from_db()
    assert definition.coverage_start == enrolled
    # run_project_pass therefore takes the incremental branch, not run_pass.
    assert not backfill.pending_versions(observe_project.id).exists()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id is None
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=definition.metric_id
    )
    # Decided, not computed: nothing is served yet.
    assert metric.active_version_id is None
    window = {"window_start": first_bucket, "window_end": datetime.now(UTC)}
    with pytest.raises(MetricUnavailableError) as early:
        reads.read_kpi(metric, window, store=writer.store)
    assert early.value.code == "metric_recalculating"

    admission.run_incremental(writer, analytics, scope, state)

    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id is not None
    assert state.watermark_ns > changes.ns(enrolled)
    # Served by the very head switch that first computed it.
    definition.refresh_from_db()
    assert definition.backfill_status == "complete" and definition.activated_at
    assert str(definition.id) in {
        item["id"] for item in state.head_manifest.published_by.metric_versions
    }
    assert _version_fields(writer, state.head_manifest, definition) == [
        (str(sessions["after"]), 8.0)
    ]
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=definition.metric_id
    )
    kpi = reads.read_kpi(metric, window, store=writer.store)
    assert kpi["current"]["value"] == pytest.approx(8.0)
    assert kpi["current"]["eligible_count"] == 1
    assert kpi["previous"] is None
    assert kpi["provenance"]["manifest_id"] == state.head_manifest_id
    assert kpi["provenance"]["history_mode"] == "forward"
    assert kpi["provenance"]["coverage_start"] == enrolled
    for start in (enrolled - timedelta(days=1), first_bucket - timedelta(minutes=15)):
        # Before coverage, and the partial bucket holding the coverage start.
        with pytest.raises(MetricUnavailableError) as before_coverage:
            reads.read_kpi(
                metric, {**window, "window_start": start}, store=writer.store
            )
        assert before_coverage.value.code == "window_not_calculated"


@pytest.mark.integration
def test_burst_sweep_starts_at_the_arrivals_and_visits_only_the_burst(store, settings):
    """Work follows the burst, not the time since enrollment.

    arrival_start_floor dates the unconsumed windows (span, eval and remap
    arrivals) with the same overlap as discovery; a burst checkpoint from that
    floor visits only spans starting there, has no committed phases, and does
    not count as a full pass.
    """
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    coverage = NOW - timedelta(days=3)
    watermark = changes.ns(NOW - timedelta(minutes=4))

    def floor(**kwargs):
        return changes.arrival_start_floor(
            store.analytics,
            org_id=store.org,
            project_id=store.project,
            committed_attempts=(store.attempt,),
            watermark_ns=watermark,
            eval_watermark_ns=watermark,
            remap_cursor_ns=watermark,
            ceiling=NOW,
            **kwargs,
        )

    assert floor() is None  # nothing arrived
    # A large retained project with a min/max skip index: its already-consumed
    # arrivals must be pruned while the burst floor is dated.
    _seed_rows(store, store.project, NOW - timedelta(days=2), 20_000, "retained-")
    store.analytics.calls.clear()
    store.analytics.query_ids.clear()
    assert floor() is None
    source_query = next(
        index
        for index, (query, _, _) in enumerate(store.analytics.calls)
        if "count() AS arrivals" in query
    )
    read_rows = query_reads(store, store.analytics.query_ids[source_query])
    assert read_rows < 1000, read_rows
    # Enrolled three days ago: two days of spans already consumed long ago.
    for i in range(30):
        start = NOW - timedelta(days=2) + timedelta(hours=i)
        seed_source_span(
            store,
            span=f"old-{i}",
            start=start,
            version=changes.ns(start + timedelta(seconds=1)),
        )
        seed_dependency(store, span=f"old-{i}")
    burst_start = NOW - timedelta(minutes=20)
    burst = {f"burst-{i}" for i in range(12)}
    for i in range(12):
        seed_source_span(
            store,
            span=f"burst-{i}",
            start=burst_start + timedelta(seconds=30 * i),
            version=changes.ns(NOW - timedelta(minutes=1)),
        )
    # Arrivals in the overlap tail before the watermark count, as in discovery;
    # an earlier arrival was consumed and does not, however recent its start.
    tail_start = NOW - timedelta(minutes=25)
    seed_source_span(
        store,
        span="overlap-tail",
        start=tail_start,
        version=changes.ns(NOW - timedelta(minutes=6)),
    )
    burst.add("overlap-tail")
    seed_source_span(
        store,
        span="consumed",
        start=NOW - timedelta(minutes=30),
        version=changes.ns(NOW - timedelta(minutes=10)),
    )
    burst_start = tail_start
    assert floor() == burst_start
    # An eval arriving now for a six-hour-old span reaches back to that span
    # (and, as discovery nominates them, every span of its trace and session).
    evaluated, evaluated_trace = NOW - timedelta(hours=6), str(uuid4())
    seed_source_span(
        store,
        span="evaluated",
        trace=evaluated_trace,
        session=store.b,
        start=evaluated,
        version=changes.ns(evaluated + timedelta(seconds=1)),
    )
    store.client.execute(
        "INSERT INTO tracer_eval_logger_v2 (id, trace_id, observation_span_id, "
        "trace_session_id, custom_eval_config_id, created_at, _version) VALUES",
        [
            (
                str(uuid4()),
                evaluated_trace,
                "evaluated",
                store.b,
                store.config,
                evaluated,
                changes.ns(NOW - timedelta(seconds=30)),
            )
        ],
    )
    assert floor() == burst_start  # this project's metrics use no eval config
    assert floor(eval_config_ids=(store.config,)) == evaluated
    # A session remap arriving now reaches every span of both sessions.
    remapped = NOW - timedelta(hours=3)
    old_id, new_id = sorted((str(uuid4()), str(uuid4())))
    seed_source_span(
        store,
        span="remapped",
        trace=str(uuid4()),
        session=old_id,
        start=remapped,
        version=changes.ns(remapped + timedelta(seconds=1)),
    )
    store.client.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [(old_id, new_id, NOW - timedelta(seconds=30))],
    )
    assert floor() == remapped
    assert floor(eval_config_ids=(store.config,)) == evaluated

    # The burst sweep itself: span arrivals only (no remap/eval in its window).
    store.client.execute("TRUNCATE TABLE trace_session_id_remap")
    first = floor()
    assert first == burst_start
    last_pass = NOW - timedelta(days=1)
    checkpoint = ReconciliationCheckpoint(
        NOW,
        completed_at=last_pass,
        completed_passes=4,
        covered_through=last_pass - timedelta(hours=1),
        floor=first,
    )
    store.analytics.calls.clear()
    pages = complete_repair(store, checkpoint, floor=coverage, page_size=5)
    swept = {k.span_id for page in pages for k in page.changes.source_keys}
    assert swept == burst
    assert len(pages) == 3 and pages[-1].checkpoint.scanned == len(burst)
    assert {page.checkpoint.phase for page in pages} == {"sources", "complete"}
    for query, params, _ in store.analytics.calls:
        assert "metric_unit_state" not in query
        if "toStartOfHour(start_time) AS hour" in query:
            lower = datetime.fromisoformat(params["day_start"]).replace(tzinfo=UTC)
            assert lower >= first, params["day_start"]
    done = pages[-1].checkpoint
    # Consumes its window (the caller moves arrivals to started_at) but is not
    # a full pass: the routine full repair stays due.
    assert (done.started_at, done.floor) == (NOW, None)
    assert (done.completed_at, done.completed_passes, done.covered_through) == (
        last_pass,
        4,
        last_pass - timedelta(hours=1),
    )
    assert pages[-1].changes.cursor_updates["reconciliation_completed_at"] == (
        last_pass
    )
    # The same project's full sweep from the coverage start reads everything
    # since enrollment, then the committed phases.
    full = complete_repair(store, floor=coverage, page_size=1000)
    assert len(full) > 3 * len(pages)
    assert {f"old-{i}" for i in range(30)} <= {
        k.span_id for page in full for k in page.changes.source_keys
    }
    assert full[-1].checkpoint.completed_passes == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("enabled_metrics")
def test_forward_overflow_long_after_enrollment_sweeps_only_the_bursts(
    live_writer, workspace, observe_project, settings
):
    """Sustained ingest crosses an overflow hours after enrollment.

    Real enrollment, discovery, overflow, burst sweep and publication: each
    overflowing window is swept from its own arrivals (never the spans consumed
    since enrollment), completes in one page, consumes its window and leaves the
    routine full repair due.
    """
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN IF NOT EXISTS observation_type String DEFAULT ''"
    )
    definition = definition_for(writer, workspace, observe_project)
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        settle_minutes=1
    )
    enrolled = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    DerivedMetricVersion.objects.filter(id=definition.id).update(created_at=enrolled)
    analytics = Analytics(writer.store.client)
    sessions = {}

    def seed(name, start, arrival, value):
        sessions[name] = uuid4()
        seed_span_revision(
            writer,
            scope,
            uuid4(),
            name,
            sessions[name],
            start,
            value,
            revision=changes.ns(arrival),
            arrival=arrival,
        )
        return name

    old = {
        seed(f"old-{i}", at, at + timedelta(seconds=1), 2)
        for i in range(8)
        for at in [enrolled + timedelta(minutes=5 + 10 * i)]
    }
    settings.METRICS_BACKFILL_MAX_SPANS = 2
    assert backfill.enter_backfill(
        writer.lease, analytics, retention_days=lambda: 0
    ) == {str(definition.id): "forward"}
    state = MetricProjectRefresh.for_project(observe_project.id)
    admission.run_incremental(writer, analytics, scope, state)
    metric = DerivedMetric.objects.get(id=definition.metric_id)
    assert metric.active_version_id == definition.id
    head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert len(_version_fields(writer, head, definition)) == len(old)

    published = []
    publish = writer.refresh

    def refresh(change, definitions, scope_, **kwargs):
        if kwargs.get("purpose") == "repair":
            # run_repair_page uses RefreshWriter's default unless the scheduler
            # wrapper supplies the enrolled project's settle interval.
            kwargs.setdefault("settle_minutes", 1)
        published.append(
            (kwargs.get("purpose"), {k.span_id for k in change.source_keys})
        )
        return publish(change, definitions, scope_, **kwargs)

    writer.refresh = refresh
    writer.incremental_unit_limit = 3
    bursts = set()
    # Each overflow lands on a different repair state: none pending, a queued
    # full repair with no cursor yet, a full sweep since enrollment in flight
    # (routine repair is due again after every burst), and that same state on
    # the repair lane. None of them may sweep the spans consumed long ago.
    rounds = (
        (None, "incremental"),
        ("queued", "incremental"),
        ("midflight", "incremental"),
        ("midflight", "repair"),
    )
    for round_no, (pending, lane) in enumerate(rounds):
        consumed = MetricProjectRefresh.for_project(observe_project.id).watermark_ns
        at = changes.from_ns(consumed)
        if pending:
            full = (
                None
                if pending == "queued"
                else ReconciliationCheckpoint(
                    started_at=at - timedelta(minutes=1),
                    phase="sources",
                    scanned=3,
                    day=enrolled.date(),
                    last_day=at.date(),
                ).to_dict()
            )
            MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
                pending_repair=True,
                reconciliation_cursor=full,
                reconciliation_started_at=at - timedelta(minutes=1),
                reconciliation_completed_at=None,
            )
        # Five already-settled conversations arriving in the overlap tail,
        # more than the cap of three.
        bursts |= {
            seed(
                f"burst{round_no}-{i}",
                at - timedelta(minutes=40) + timedelta(seconds=i),
                at - timedelta(minutes=2) + timedelta(seconds=i),
                8,
            )
            for i in range(5)
        }
        published.clear()
        state = MetricProjectRefresh.for_project(observe_project.id)
        if lane == "incremental":
            admission.run_incremental(writer, analytics, scope, state)
        else:
            backfill.BackfillRunner(writer, analytics, scope).run_repair_page()
        state = MetricProjectRefresh.for_project(observe_project.id)
        # One pass: the burst sweep's single page completes and publishes.
        assert [purpose for purpose, _ in published] == ["repair"]
        ((_, swept),) = published
        assert swept <= bursts and not swept & old
        assert {f"burst{round_no}-{i}" for i in range(5)} <= swept
        assert not state.pending_repair
        cursor = state.reconciliation_cursor
        assert cursor["phase"] == "complete" and "floor" not in cursor
        assert cursor["completed_passes"] == 0 and cursor["covered_through"] is None
        assert state.reconciliation_completed_at is None  # routine repair due
        assert state.watermark_ns > consumed
        values = dict(_version_fields(writer, state.head_manifest, definition))
        assert {str(sessions[name]) for name in bursts} <= values.keys()
        assert values[str(sessions[f"burst{round_no}-0"])] == pytest.approx(8.0)
