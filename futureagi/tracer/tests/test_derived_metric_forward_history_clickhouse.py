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
def test_work_larger_than_a_pass_publishes_across_passes_and_never_leaves_it_open(
    live_writer, workspace, observe_project, settings, monkeypatch
):
    """10M bug 6: a BIG pass planned 107 captures (~3.5 s each), ran past its
    540 s lease and published nothing; the next pass repeated the same work.

    1,600 conversations, a capture budget of 2 and a fake clock on which each
    capture takes 150 s: the arrivals are paged through the burst sweep, a pass
    that runs late stops cleanly 200 s before its deadline (attempt aborted,
    its ledger carried), no pass ever ends with an open attempt, the head
    advances, and every conversation ends with its true value.
    """
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics import refresh
    from tracer.services.derived_metrics.lease import MetricPassDeadline

    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    definition = definition_for(writer, workspace, observe_project)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    enrolled = hour - timedelta(hours=3)
    DerivedMetricVersion.objects.filter(id=definition.id).update(created_at=enrolled)
    at = enrolled + timedelta(hours=1)
    truth = {}
    rows = []
    for index in range(1_600):
        session = uuid4()
        truth[str(session)] = float(index % 7)
        start = at + timedelta(seconds=index)
        rows.append(
            (
                f"span-{index}",
                str(uuid4()),
                observe_project.id,
                workspace.organization_id,
                session,
                start,
                start + timedelta(seconds=1),
                start,
                start,
                changes.ns(start),
                0,
                {"resolved": float(index % 7)},
                {"region": "west"},
            )
        )
    writer.store.client.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, is_deleted, "
        "attrs_number, attrs_string) VALUES",
        rows,
    )
    settings.METRICS_BACKFILL_MAX_SPANS = 2
    settings.METRICS_PASS_MAX_CAPTURES = 2
    analytics = Analytics(writer.store.client)
    assert backfill.enter_backfill(
        writer.lease, analytics, retention_days=lambda: 0
    ) == {str(definition.id): "forward"}
    clock = [0.0]
    writer.lease.ticker = lambda: clock[0]
    capture = refresh.capture_source_output

    def slow_capture(*args, **kwargs):
        clock[0] += 150
        return capture(*args, **kwargs)

    monkeypatch.setattr(refresh, "capture_source_output", slow_capture)
    heads, yields = [], 0
    for _ in range(14):
        # One pass: a fresh writer counter and a 540 s budget on the fake clock.
        writer.pass_captures = 0
        writer.lease.deadline = clock[0] + 540
        state = MetricProjectRefresh.for_project(observe_project.id)
        try:
            if state.pending_repair:
                backfill.BackfillRunner(writer, analytics, scope).run_repair_pages()
            else:
                admission.run_incremental(writer, analytics, scope, state)
        except MetricPassDeadline as stop:
            assert isinstance(stop, refresh.MetricPassYield)
            yields += 1
        # Every pass ends with its own attempt published or aborted.
        assert not MetricRefreshAttempt.objects.filter(
            project_id=observe_project.id,
            state__in=MetricRefreshAttempt.OPEN_STATES,
        ).exists()
        state = MetricProjectRefresh.for_project(observe_project.id)
        heads.append(state.head_manifest_id)
        if state.head_manifest_id and len(
            _version_fields(writer, state.head_manifest, definition)
        ) == len(truth):
            break
    else:
        pytest.fail(f"never completed: {len(set(heads))} heads, {yields} yields")
    assert yields >= 1  # the budget was really exceeded and the pass stopped
    assert len({h for h in heads if h}) >= 2  # progress over several passes
    assert dict(_version_fields(writer, state.head_manifest, definition)) == truth
    # A yield's ledger is carried by the next pass, exactly once: one more pass
    # (now fast) takes any still waiting, and the values stay true.
    monkeypatch.setattr(refresh, "capture_source_output", capture)
    writer.pass_captures = 0
    writer.lease.deadline = clock[0] + 540
    state = MetricProjectRefresh.for_project(observe_project.id)
    if state.pending_repair:
        backfill.BackfillRunner(writer, analytics, scope).run_repair_pages()
    else:
        admission.run_incremental(writer, analytics, scope, state)
    assert not MetricRefreshAttempt.objects.filter(
        project_id=observe_project.id, abort_reason=refresh.PASS_YIELD
    ).exists()
    assert (
        MetricRefreshAttempt.objects.filter(
            project_id=observe_project.id, abort_reason=refresh.PASS_YIELD_CARRIED
        ).count()
        == yields
    )
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert dict(_version_fields(writer, state.head_manifest, definition)) == truth


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


def _session_projection(writer):
    """The production proj_by_session lookup (021_session_and_user_projections.sql)
    on a ReplacingMergeTree, which (as 002_spans_v2.sql does) must rebuild it."""
    writer.store.execute(
        "ALTER TABLE spans MODIFY SETTING deduplicate_merge_projection_mode = 'rebuild'"
    )
    writer.store.execute(
        "ALTER TABLE spans ADD PROJECTION proj_by_session (SELECT project_id, "
        "trace_session_id, start_time ORDER BY project_id, trace_session_id, start_time)"
    )


def _version_index(writer):
    """The production spans _version minmax skip index (002_spans_v2.sql)."""
    writer.store.execute(
        "ALTER TABLE spans ADD INDEX auto_minmax_index__version _version "
        "TYPE minmax GRANULARITY 1"
    )


def _logged(writer, prefix, since):
    """(read_rows, projections) of finished statements with a query id prefix."""
    writer.store.client.execute("SYSTEM FLUSH LOGS")
    return writer.store.client.execute(
        "SELECT read_rows, projections FROM system.query_log "
        "WHERE type = 'QueryFinish' AND query_id LIKE %(prefix)s "
        "AND event_time >= %(since)s",
        {"prefix": prefix + "%", "since": since.replace(tzinfo=None)},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["forward", "history"])
def test_unit_source_capture_reads_the_burst_not_the_project_history(
    live_writer, workspace, observe_project, mode
):
    """The 10M BIG 241: unit capture scanned every span of the project.

    A recent conversation is captured on a project holding 60,000 old spans
    written before it. The floor is dated through proj_by_session, the guard
    for newer backdated revisions is pruned by the _version index, and the
    session nominee and both identity reads are bounded by the floor, so the
    capture reads the recent granules only, for a forward-only and a history
    version alike.
    """
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    _session_projection(writer)
    _version_index(writer)
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    if mode == "forward":
        definition = _forward(definition, now - timedelta(days=3))
    writer.store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "start_time, created_at, updated_at, attrs_number) "
        "SELECT concat('old-', toString(number)), toUUID(%(project_id)s), "
        "toUUID(%(org_id)s), concat('old-trace-', toString(intDiv(number, 4))), "
        "generateUUIDv4(), now64(6, 'UTC') - toIntervalDay(2) + toIntervalSecond(number), "
        "now64(6, 'UTC') - toIntervalDay(2), now64(6, 'UTC') - toIntervalDay(2), "
        "map('resolved', 1) FROM numbers(60000)",
        scope.parameters(),
    )
    session, trace = uuid4(), uuid4()
    at = now - timedelta(minutes=40)
    # Written after the history (old spans keep the default _version 1).
    seed_span_revision(writer, scope, trace, "recent", session, at, 7, revision=2)
    dependency = Dependency(
        str(trace), "recent", str(session), str(session), "", "", at
    )
    since = datetime.now(UTC) - timedelta(seconds=1)
    head = writer.refresh(
        change_for(session, dependencies=[dependency]),
        [definition],
        scope,
        start=now - timedelta(days=90),
        end=now,
        as_of=now,
    )
    assert _version_fields(writer, head, definition) == [(str(session), 7.0)]
    captures = _logged(writer, "metric-source-capture-", since)
    assert captures
    # Unbounded, a capture reads all 60,000 old spans (twice); bounded, only
    # the granules from the recent session's own start onward.
    assert max(rows for rows, _ in captures) < 20_000, captures
    floors = _logged(writer, "metric-source-floor-", since)
    versions = _logged(writer, "metric-source-version-", since)
    guards = _logged(writer, "metric-source-guard-", since)
    assert floors and versions and guards
    # The floor statements are bounded too: a projection lookup, a read from
    # the floor onward, and a guard the _version index prunes to recent writes.
    for logged in (floors, versions, guards):
        assert max(rows for rows, _ in logged) < 20_000, logged
    assert all(
        any("proj_by_session" in p for p in projections) for _, projections in floors
    )


@pytest.mark.django_db(transaction=True)
def test_forward_conversation_straddling_coverage_stays_excluded_on_a_burst(
    live_writer, workspace, observe_project
):
    """A conversation that began before coverage_start never gets a forward
    value, even when only its post-coverage spans are nominated: the capture
    floor is the session's own earliest span, never coverage_start."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    coverage = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    forward = _forward(definition_for(writer, workspace, observe_project), coverage)
    session, trace = uuid4(), uuid4()
    early, late = coverage - timedelta(minutes=30), coverage + timedelta(minutes=30)
    for name, at, value in (("early", early, 2), ("late", late, 9)):
        seed_span_revision(writer, scope, trace, name, session, at, value, revision=1)
    only_late = Dependency(str(trace), "late", str(session), str(session), "", "", late)
    head = writer.refresh(
        change_for(session, dependencies=[only_late]),
        [forward],
        scope,
        start=coverage - timedelta(days=1),
        end=coverage + timedelta(hours=1, minutes=30),
        as_of=coverage + timedelta(hours=1, minutes=30),
    )
    assert _version_fields(writer, head, forward) == []


@pytest.mark.django_db(transaction=True)
def test_span_moved_to_another_session_and_backdated_leaves_its_old_unit(
    live_writer, workspace, observe_project
):
    """A later revision moves span K from conversation U to S2 AND backdates it
    below every span of U. Recapturing U in the same pass as the move, and
    again in a later pass after the move was committed, never counts K in U
    (the floor covers the new revision both times) and never counts it twice.
    The value is the latest span's (reducer "latest"), so a stale K, the latest
    span of U, would show as U's value."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    u, s2, trace = uuid4(), uuid4(), uuid4()
    a_at, j_at, k_at = (now - timedelta(minutes=m) for m in (60, 55, 50))
    moved_at = now - timedelta(hours=3)

    def dep(name, session, at):
        return Dependency(str(trace), name, str(session), str(session), "", "", at)

    def refresh(keys, units, after, before, as_of):
        return writer.refresh(
            ChangeBatch(
                tuple(SourceKey(str(trace), k) for k in keys),
                tuple(UnitKey("conversation", str(x)) for x in units),
                (),
                tuple(after),
                tuple(before),
                {"watermark_ns": changes.ns(as_of)},
            ),
            [definition],
            scope,
            start=now - timedelta(days=1),
            end=as_of,
            as_of=as_of,
        )

    seed_span_revision(writer, scope, trace, "a", u, a_at, 2, revision=1)
    seed_span_revision(writer, scope, trace, "k", u, k_at, 8, revision=1)
    head = refresh(["a", "k"], [u], [dep("a", u, a_at), dep("k", u, k_at)], [], now)
    assert dict(_version_fields(writer, head, definition)) == {str(u): 8.0}
    # K moves to S2 with a start three hours earlier than any span of U.
    seed_span_revision(writer, scope, trace, "k", s2, moved_at, 8, revision=2)
    head = refresh(["k"], [u, s2], [dep("k", s2, moved_at)], [dep("k", u, k_at)], now)
    assert dict(_version_fields(writer, head, definition)) == {
        str(u): 2.0,
        str(s2): 8.0,
    }
    # A later pass recaptures U alone (a new span J), after the move committed:
    # K's old revision still nominates it, its new one is far below U's spans.
    seed_span_revision(writer, scope, trace, "j", u, j_at, 4, revision=1)
    head = refresh(["j"], [u], [dep("j", u, j_at)], [], now)
    assert dict(_version_fields(writer, head, definition)) == {
        str(u): 4.0,
        str(s2): 8.0,
    }


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["forward", "history"])
def test_span_capture_reads_the_dirty_window_not_the_project_history(
    live_writer, workspace, observe_project, mode
):
    """The 10M BIG 395: a span page's capture read 105M rows of history.

    The span window's revisions are read from the window's floor onward (its
    start, or the start of a newer backdated revision of one of its spans), so
    a span page on a project with 60,000 old spans written before it reads
    only the recent granules, for a forward-only and a history version alike.
    """
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    _version_index(writer)
    span = span_definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(minute=7, second=0, microsecond=0)
    if mode == "forward":
        span = _forward(span, now - timedelta(days=3))
    writer.store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, attrs_number) "
        "SELECT concat('old-', toString(number)), toUUID(%(project_id)s), "
        "toUUID(%(org_id)s), concat('old-trace-', toString(intDiv(number, 4))), "
        "generateUUIDv4(), now64(6, 'UTC') - toIntervalDay(2) + toIntervalSecond(number), "
        "now64(6, 'UTC') - toIntervalDay(2) + toIntervalSecond(number + 1), "
        "now64(6, 'UTC') - toIntervalDay(2), now64(6, 'UTC') - toIntervalDay(2), "
        "map('resolved', 1) FROM numbers(60000)",
        scope.parameters(),
    )
    session, trace = uuid4(), uuid4()
    at = now - timedelta(hours=1)
    seed_span_revision(writer, scope, trace, "recent", session, at, 6, revision=2)
    dependency = Dependency(
        str(trace), "recent", str(session), str(session), "", "", at
    )
    since = datetime.now(UTC) - timedelta(seconds=1)
    head = writer.refresh(
        change_for(session, dependencies=[dependency]),
        [span],
        scope,
        start=now - timedelta(days=90),
        end=now,
        as_of=now,
    )
    rows = metric_bucket_rows(writer, head, span)
    assert [r[2:] for r in rows] == [(1, 6.0, 1.0)], rows
    writer.store.client.execute("SYSTEM FLUSH LOGS")
    captures = writer.store.client.execute(
        "SELECT read_rows FROM system.query_log WHERE type = 'QueryFinish' "
        "AND query LIKE 'INSERT INTO metric_span_capture_%%' AND event_time >= %(since)s",
        {"since": since.replace(tzinfo=None)},
    )
    assert captures
    # Unbounded, the window's revision reads scan all 60,000 old spans.
    assert max(r for (r,) in captures) < 20_000, captures
    versions = _logged(writer, "metric-span-version-", since)
    floors = _logged(writer, "metric-span-floor-", since)
    assert versions and floors
    for logged in (versions, floors):
        assert max(r for r, _ in logged) < 20_000, logged


@pytest.mark.django_db(transaction=True)
def test_uncommitted_backdated_move_out_of_a_unit_is_not_counted_in_it(
    live_writer, workspace, observe_project
):
    """K moves from U to S2 with a start below every span of U, and NO pass has
    seen the move. A pass that recaptures U alone (a new span J) must not count
    K in U: no dependency records the new start, so only the guard for newer
    backdated revisions of U's spans finds it. Same result as an unbounded read.
    The value is the latest span's: a stale K would show as U's value, 8."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    u, s2, trace = uuid4(), uuid4(), uuid4()
    a_at, j_at, k_at = (now - timedelta(minutes=m) for m in (60, 55, 50))
    moved_at = now - timedelta(days=1, hours=3)

    def dep(name, at):
        return Dependency(str(trace), name, str(u), str(u), "", "", at)

    def refresh(keys, deps, cursor):
        return writer.refresh(
            ChangeBatch(
                tuple(SourceKey(str(trace), k) for k in keys),
                (UnitKey("conversation", str(u)),),
                (),
                tuple(deps),
                (),
                {"watermark_ns": cursor},
            ),
            [definition],
            scope,
            start=now - timedelta(days=2),
            end=now,
            as_of=now,
        )

    seed_span_revision(writer, scope, trace, "a", u, a_at, 2, revision=1)
    seed_span_revision(writer, scope, trace, "k", u, k_at, 8, revision=1)
    head = refresh(["a", "k"], [dep("a", a_at), dep("k", k_at)], 1)
    assert dict(_version_fields(writer, head, definition)) == {str(u): 8.0}
    seed_span_revision(writer, scope, trace, "k", s2, moved_at, 8, revision=2)
    seed_span_revision(writer, scope, trace, "j", u, j_at, 4, revision=1)
    head = refresh(["j"], [dep("j", j_at)], 2)
    assert dict(_version_fields(writer, head, definition)) == {str(u): 4.0}


@pytest.mark.django_db(transaction=True)
def test_uncommitted_backdated_move_out_of_a_span_window_leaves_it(
    live_writer, workspace, observe_project
):
    """Span X moves from window W to a start a day earlier, and NO pass has seen
    the move. A pass dirtied by another span Z in W must not count X in W: its
    new quarter is not dirty and no dependency records it, so only the guard for
    newer backdated revisions finds it. Same result as an unbounded read."""
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    span = span_definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(minute=7, second=0, microsecond=0)
    window = now.replace(minute=0) - timedelta(hours=1)
    session, trace = uuid4(), uuid4()
    x_at, y_at, z_at = (window + timedelta(minutes=m) for m in (1, 2, 3))

    def dep(name, at):
        return Dependency(str(trace), name, str(session), str(session), "", "", at)

    def refresh(deps, cursor):
        return writer.refresh(
            change_for(session, cursor=cursor, dependencies=deps),
            [span],
            scope,
            start=now - timedelta(days=2),
            end=now,
            as_of=now,
        )

    seed_span_revision(writer, scope, trace, "x", session, x_at, 2, revision=1)
    seed_span_revision(writer, scope, trace, "y", session, y_at, 4, revision=1)
    head = refresh([dep("x", x_at), dep("y", y_at)], 1)
    assert [r[2:] for r in metric_bucket_rows(writer, head, span)] == [(2, 6.0, 2.0)]
    seed_span_revision(
        writer, scope, trace, "x", session, now - timedelta(days=1), 2, revision=2
    )
    seed_span_revision(writer, scope, trace, "z", session, z_at, 10, revision=1)
    head = refresh([dep("z", z_at)], 2)
    rows = metric_bucket_rows(writer, head, span)
    assert [(r[0].replace(tzinfo=UTC), *r[2:]) for r in rows] == [
        (window, 2, 14.0, 2.0)
    ], rows


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("enabled_metrics")
@pytest.mark.parametrize("lane", ["incremental", "repair"])
def test_forward_burst_over_a_pending_repair_reads_the_burst_not_the_history(
    live_writer, workspace, observe_project, settings, lane
):
    """OVER2 on the 10M VM: a burst of conversations, some begun before
    coverage, lands over an in-flight routine repair of a forward project with
    history. Every unit capture of the burst sweep read the whole project
    (about 1.74M rows, Code 241). Bounded by the batch's own first span, each
    capture reads the burst only, and the straddling units stay excluded."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN IF NOT EXISTS observation_type String DEFAULT ''"
    )
    _session_projection(writer)
    _version_index(writer)
    definition = definition_for(writer, workspace, observe_project)
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        settle_minutes=1
    )
    enrolled = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    DerivedMetricVersion.objects.filter(id=definition.id).update(created_at=enrolled)
    # Two days of history, ingested as it happened (_version = start).
    writer.store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number) "
        "SELECT concat('hist-', toString(number)), toUUID(%(project_id)s), "
        "toUUID(%(org_id)s), concat('hist-trace-', toString(intDiv(number, 4))), "
        "toUUID(concat('00000000-0000-0000-0000-', leftPad(toString(intDiv(number, 4)), 12, '0'))), "
        "start, start + toIntervalSecond(1), start, start, "
        "toUInt64(toUnixTimestamp64Nano(start)), map('resolved', 1) FROM ("
        "SELECT number, now64(6, 'UTC') - toIntervalDay(2) + toIntervalSecond(number) "
        "AS start FROM numbers(60000))",
        scope.parameters(),
    )
    # Conversations since enrollment give the first publication.
    for i in range(2):
        start = enrolled + timedelta(minutes=5 + 10 * i)
        seed_span_revision(
            writer,
            scope,
            uuid4(),
            f"early-{i}",
            uuid4(),
            start,
            2,
            revision=changes.ns(start + timedelta(seconds=1)),
            arrival=start + timedelta(seconds=1),
        )
    analytics = Analytics(writer.store.client)
    settings.METRICS_BACKFILL_MAX_SPANS = 2
    assert backfill.enter_backfill(
        writer.lease, analytics, retention_days=lambda: 0
    ) == {str(definition.id): "forward"}
    state = MetricProjectRefresh.for_project(observe_project.id)
    admission.run_incremental(writer, analytics, scope, state)
    assert (
        DerivedMetric.objects.get(id=definition.metric_id).active_version_id
        == definition.id
    )
    consumed = MetricProjectRefresh.for_project(observe_project.id).watermark_ns
    at = changes.from_ns(consumed)
    # A routine full repair is in flight when the burst lands.
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        pending_repair=True,
        reconciliation_cursor=ReconciliationCheckpoint(
            started_at=at - timedelta(minutes=1),
            phase="sources",
            scanned=3,
            day=enrolled.date(),
            last_day=at.date(),
        ).to_dict(),
        reconciliation_started_at=at - timedelta(minutes=1),
        reconciliation_completed_at=None,
    )
    sessions = {}

    def seed(name, starts, value):
        sessions[name] = session = uuid4()
        trace = uuid4()
        for offset, start in enumerate(starts):
            arrival = at - timedelta(minutes=2) + timedelta(seconds=offset)
            seed_span_revision(
                writer,
                scope,
                trace,
                f"{name}-{offset}",
                session,
                start,
                value,
                revision=changes.ns(arrival),
                arrival=arrival,
            )

    # Five conversations began before coverage and continue after it; three
    # began after it. Eight already-settled arrivals overflow a cap of three.
    for i in range(5):
        seed(
            f"straddle-{i}",
            [enrolled - timedelta(minutes=10, seconds=i), at - timedelta(minutes=40)],
            8,
        )
    for i in range(3):
        seed(f"fresh-{i}", [at - timedelta(minutes=40, seconds=i)], 8)
    writer.incremental_unit_limit = 3
    since = datetime.now(UTC) - timedelta(seconds=1)
    state = MetricProjectRefresh.for_project(observe_project.id)
    if lane == "incremental":
        admission.run_incremental(writer, analytics, scope, state)
    else:
        backfill.BackfillRunner(writer, analytics, scope).run_repair_page()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_repair
    assert state.reconciliation_cursor["phase"] == "complete"
    values = dict(_version_fields(writer, state.head_manifest, definition))
    assert {str(sessions[f"fresh-{i}"]) for i in range(3)} <= values.keys()
    assert not {str(sessions[f"straddle-{i}"]) for i in range(5)} & values.keys()
    captures = _logged(writer, "metric-source-capture-", since)
    assert captures
    # Unbounded, every capture reads the 60,000 spans of history.
    assert max(rows for rows, _ in captures) < 20_000, captures


@pytest.mark.django_db(transaction=True)
def test_dense_quarter_above_the_span_budget_is_paged_with_bounded_memory(
    live_writer, workspace, observe_project
):
    """10M BIG 395: one 15-minute quarter held more spans than the 50,000-row
    span capture budget, so every pass failed. The quarter is now captured in
    key-ordered identity pages, each within the budget and well under the
    512 MiB statement cap, and the bucket counts every span exactly once."""
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    _version_index(writer)
    span = span_definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(minute=7, second=0, microsecond=0)
    quarter = now.replace(minute=0) - timedelta(hours=2)
    dense = 110_000
    writer.store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number, "
        "attrs_string) "
        "SELECT concat('dense-', toString(number)), toUUID(%(project_id)s), "
        "toUUID(%(org_id)s), concat('dense-trace-', toString(intDiv(number, 4))), "
        "generateUUIDv4(), %(quarter)s + toIntervalMillisecond(number * 8), "
        "%(quarter)s + toIntervalMillisecond(number * 8 + 500), %(quarter)s, "
        "%(quarter)s, toUInt64(2), map('resolved', 1), map('region', 'west') "
        "FROM numbers(%(dense)s)",
        {**scope.parameters(), "quarter": quarter, "dense": dense},
    )
    session, trace = uuid4(), uuid4()
    at = quarter + timedelta(seconds=1)
    seed_span_revision(writer, scope, trace, "anchor", session, at, 1, revision=3)
    dependency = Dependency(
        str(trace), "anchor", str(session), str(session), "", "", at
    )
    since = datetime.now(UTC) - timedelta(seconds=1)
    head = writer.refresh(
        change_for(session, dependencies=[dependency]),
        [span],
        scope,
        start=now - timedelta(days=1),
        end=now,
        as_of=now,
    )
    rows = metric_bucket_rows(writer, head, span)
    total = dense + 1
    assert [r[2:] for r in rows] == [(total, float(total), float(total))], rows
    writer.store.client.execute("SYSTEM FLUSH LOGS")
    pages = writer.store.client.execute(
        "SELECT written_rows, memory_usage FROM system.query_log "
        "WHERE type = 'QueryFinish' "
        "AND query LIKE 'INSERT INTO metric_span_capture_%%' AND event_time >= %(since)s",
        {"since": since.replace(tzinfo=None)},
    )
    # Unpaged, the single capture of the quarter fails its 50,000-row budget.
    assert len(pages) >= 3, pages
    assert sum(r for r, _ in pages) == total, pages
    assert max(r for r, _ in pages) <= 50_000, pages
    assert max(m for _, m in pages) < 256 * 1024 * 1024, pages


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["history", "forward"])
def test_capture_page_over_many_parts_stays_well_under_the_statement_cap(
    live_writer, workspace, observe_project, mode
):
    """10M Bug 2: one capture page over 773 active spans parts peaked at
    1.19 GiB (Code 241 at the 512 MiB cap) while reading 34k rows. Parallel
    primary-key/skip-index analysis keeps untracked per-thread memory
    proportional to parts x IN-set size; one index-analysis thread bounds it.
    A ~480-conversation page over ~1,200 parts, captured twice (the second time
    with its committed preimage pairs), stays well under the cap."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    if mode == "forward":
        definition = _forward(definition, now - timedelta(days=3))
    at = now - timedelta(hours=2)
    sessions = [uuid4() for _ in range(480)]
    traces = {session: uuid4() for session in sessions}
    spans = [
        (traces[session], f"{n}-{i}", session, at + timedelta(seconds=n * 5 + i))
        for n, session in enumerate(sessions)
        for i in range(5)
    ]
    writer.store.execute("SYSTEM STOP MERGES spans")
    try:
        for offset in range(0, len(spans), 2):
            writer.store.execute(
                "INSERT INTO spans (id, trace_id, project_id, org_id, "
                "trace_session_id, start_time, end_time, created_at, updated_at, "
                "attrs_number, attrs_string) VALUES",
                [
                    {
                        "id": span,
                        "trace_id": str(trace),
                        "project_id": scope.project,
                        "org_id": scope.organization,
                        "trace_session_id": session,
                        "start_time": start,
                        "end_time": start + timedelta(seconds=1),
                        "created_at": start,
                        "updated_at": start,
                        "attrs_number": {"resolved": 1.0},
                        "attrs_string": {"region": "west"},
                    }
                    for trace, span, session, start in spans[offset : offset + 2]
                ],
            )
        parts = writer.store.execute(
            "SELECT count() FROM system.parts WHERE active AND table = 'spans' "
            "AND database = currentDatabase()"
        )[0][0]
        assert parts >= 1_000, parts
        dependencies = [
            Dependency(str(trace), span, str(session), str(session), "", "", start)
            for trace, span, session, start in spans
        ]
        since = datetime.now(UTC) - timedelta(seconds=1)
        for cursor in (1, 2):
            head = writer.refresh(
                ChangeBatch(
                    tuple(SourceKey(d.trace_id, d.span_id) for d in dependencies),
                    tuple(UnitKey("conversation", str(s)) for s in sessions),
                    (),
                    tuple(dependencies),
                    (),
                    {"watermark_ns": cursor},
                ),
                [definition],
                scope,
                start=now - timedelta(days=1),
                end=now,
                as_of=now,
            )
    finally:
        writer.store.execute("SYSTEM START MERGES spans")
    assert len(_version_fields(writer, head, definition)) == len(sessions)
    writer.store.client.execute("SYSTEM FLUSH LOGS")
    captures = writer.store.client.execute(
        "SELECT memory_usage, Settings['max_threads_for_indexes'] FROM "
        "system.query_log WHERE type = 'QueryFinish' "
        "AND query_id LIKE 'metric-source-capture-%%' AND event_time >= %(since)s",
        {"since": since.replace(tzinfo=None)},
    )
    assert len(captures) >= 2, captures
    assert all(setting == "1" for _, setting in captures), captures
    assert max(memory for memory, _ in captures) < 256 * 1024 * 1024, captures


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("enabled_metrics")
def test_reads_answer_only_final_buckets_never_partial_or_open_ones(
    live_writer, workspace, observe_project
):
    """10M: an ok bucket must equal its span truth. OVER2's [00:15, 00:30)
    straddled the burst floor and said ok 377 of 51,515; OVER's 00:15 bucket
    said ok while it held open units, and its 00:30 bucket said no_data with
    1,498 open units.

    A bucket is final only if it ends by the settled bound (the calculated cut
    less settle) and holds no unit refresh still keeps open, however long ago
    that unit started. Here a conversation L starts in bucket B1 two hours ago
    and is still receiving spans (open), longer than the gap from its start to
    the cut; A (earlier) and C (later) are settled. The KPI is computed over
    the window's final buckets only (A and C, never L's bucket nor any bucket
    from the settled bound), disclosing complete_through B1, partial, the
    non-final buckets and the open unit; one open session never freezes the
    rest of the window. The trend answers A's and C's buckets, marks B1 and
    every bucket from the settled bound settling and every bucket from the
    calculated bound not_calculated, and an empty final bucket no_data. Only a
    window with no final bucket is not final yet; a pending burst floor makes
    later buckets not_calculated."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    now = datetime.now(UTC).replace(microsecond=0)

    def floor15(value):
        return value.replace(minute=value.minute // 15 * 15, second=0, microsecond=0)

    start = floor15(now) - timedelta(hours=4)
    b1 = start + timedelta(hours=2)
    b2 = start + timedelta(hours=3) - timedelta(minutes=30)
    definition = _forward(
        definition_for(writer, workspace, observe_project), now - timedelta(days=1)
    )
    sessions = {name: uuid4() for name in ("a", "l", "c")}
    trace = uuid4()
    dependencies = []
    for name, session, at, arrival, value in (
        ("a", sessions["a"], start + timedelta(minutes=5), None, 2),
        ("l", sessions["l"], b1 + timedelta(minutes=1), None, 4),
        ("l2", sessions["l"], b1 + timedelta(minutes=2), now - timedelta(minutes=5), 4),
        ("c", sessions["c"], b2 + timedelta(minutes=1), None, 6),
    ):
        seed_span_revision(
            writer,
            scope,
            trace,
            name,
            session,
            at,
            value,
            revision=changes.ns(arrival or at),
            arrival=arrival,
        )
        dependencies.append(
            Dependency(str(trace), name, str(session), str(session), "", "", at)
        )
    head = writer.refresh(
        ChangeBatch(
            tuple(SourceKey(d.trace_id, d.span_id) for d in dependencies),
            tuple(UnitKey("conversation", str(s)) for s in sessions.values()),
            (),
            tuple(dependencies),
            (),
            {"watermark_ns": changes.ns(now)},
        ),
        [definition],
        scope,
        start=start - timedelta(days=1),
        end=now,
        as_of=now,
    )
    assert head is not None
    metric = DerivedMetric.objects.get(id=definition.metric_id)
    DerivedMetricVersion.objects.filter(id=definition.id).update(
        backfill_status="complete"
    )
    DerivedMetric.activate_version(metric.id, definition.id, expected_active_id=None)
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=metric.id
    )
    calculated, settled = floor15(now), floor15(now - timedelta(minutes=30))
    assert reads.cut_terms(head, definition) == (calculated, settled)

    window = {"window_start": start, "window_end": calculated}
    kpi = reads.read_kpi(metric, window, store=writer.store)
    assert kpi["complete_through"] == b1 and kpi["partial"] is True
    assert kpi["open_units"] == 1
    # B1 (L, open) and the two settle buckets before the calculated bound.
    assert kpi["nonfinal_buckets"] == 1 + 2
    assert kpi["current"]["eligible_count"] == 2  # A and C: never L
    assert kpi["current"]["value"] == pytest.approx(4.0)
    # A window starting at L's open bucket still answers its final buckets.
    later = reads.read_kpi(
        metric, {"window_start": b1, "window_end": calculated}, store=writer.store
    )
    assert later["complete_through"] == b1 and later["partial"] is True
    assert later["current"]["eligible_count"] == 1  # C
    assert later["current"]["value"] == pytest.approx(6.0)

    trend = reads.read_trend(metric, {**window, "points": 16}, store=writer.store)
    statuses = {r["window_start"]: r["point_status"] for r in trend["results"]}
    counts = {r["window_start"]: r["eligible_count"] for r in trend["results"]}
    assert trend["partial"] is True
    assert statuses[start] == "ok" and counts[start] == 1
    assert statuses[start + timedelta(minutes=15)] == "no_data"
    assert statuses[b1] == "settling"
    assert statuses[b2] == "ok" and counts[b2] == 1
    assert all(
        status == "settling"
        for at, status in statuses.items()
        if settled <= at < calculated
    )
    assert "ok" not in {statuses[at] for at in statuses if at >= settled}

    # No final bucket at all: within the settle interval of the cut.
    with pytest.raises(MetricUnavailableError) as settling:
        reads.read_kpi(
            metric,
            {"window_start": settled, "window_end": calculated},
            store=writer.store,
        )
    assert settling.value.code == "window_not_calculated_yet"
    assert settling.value.reason == "settling"
    assert settling.value.calculated_through == settled

    # A burst sweep pending from B2 has not re-read units from there.
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        pending_repair=True,
        reconciliation_cursor=ReconciliationCheckpoint(
            started_at=now, floor=b2
        ).to_dict(),
    )
    trend = reads.read_trend(metric, {**window, "points": 16}, store=writer.store)
    statuses = {r["window_start"]: r["point_status"] for r in trend["results"]}
    assert statuses[b2] == "not_calculated"
    with pytest.raises(MetricUnavailableError) as burst:
        reads.read_kpi(
            metric, {"window_start": b2, "window_end": calculated}, store=writer.store
        )
    assert burst.value.reason == "not_calculated"
