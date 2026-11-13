# ruff: noqa: F811 -- shared PG state-machine fixtures
"""Start with no history: projects above METRICS_BACKFILL_MAX_SPANS.

PG state-machine proofs with the real lease, ledger, publication and CAS; only
payload preparation and ClickHouse reads are fakes (as in the backfill tests).
The live count, sweep floor, Code 241 cause chain and compute filter are proved
against ClickHouse in test_derived_metric_forward_history_clickhouse.py.
"""

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricGenerationPin,
    MetricProjectRefresh,
)
from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.serializers.derived_metric import (
    DerivedMetricVersionSerializer,
    MetricReadProvenanceSerializer,
)
from tracer.services.derived_metrics import (
    admission,
    backfill,
    changes,
    reads,
    snapshots,
)
from tracer.services.derived_metrics.backfill import (
    incremental_changes as real_incremental_changes,
)
from tracer.services.derived_metrics.deletion_protocol import lifecycle_lock_budget
from tracer.services.derived_metrics.lease import project_lease
from tracer.tests.test_derived_metric_api import (  # noqa: F401
    ROOT,
    run_body,
    saved,
)
from tracer.tests.test_derived_metric_backfill import (  # noqa: F401
    EMPTY,
    NOW,
    _named_version,
    _overflow,
    _two_page_sweep,
    make_version,
    setup_runner,
)


def _size(extra):
    """A size measurement exactly ``extra`` spans above the limit."""
    seen = []

    def measure(analytics, project_id, *, now, retention_days, limit):
        seen.append((str(project_id), now, retention_days, limit))
        return limit + extra

    measure.seen = seen
    return measure


def _forbid(*args, **kwargs):
    pytest.fail("a forward-only version must never start a history sweep")


def _enter(runner, **kwargs):
    return backfill.enter_backfill(
        runner.writer.lease, object(), retention_days=lambda: 2, **kwargs
    )


def _watermarks(project_id):
    state = MetricProjectRefresh.for_project(project_id)
    return tuple(getattr(state, f) for f in MetricProjectRefresh.MONOTONE_FIELDS)


def _publish_empty_head(runner):
    attempt = runner.writer.begin()
    runner.writer.seal(attempt)
    return runner.writer.stage_and_publish(attempt)


def _consume(runner, at, definitions=None):
    """One publication that consumes arrivals up to ``at`` (as discovery does)."""
    project_id = runner.writer.lease.project_id
    ceiling = changes.ns(at)
    return runner.writer.refresh(
        replace(
            EMPTY,
            cursor_updates=dict.fromkeys(MetricProjectRefresh.MONOTONE_FIELDS, ceiling),
        ),
        backfill.working_definitions(project_id)
        if definitions is None
        else definitions,
        runner.scope,
        start=at - timedelta(days=1),
        end=at,
        as_of=at,
        purpose="incremental",
        publication=None,
    )


def _prepared(manifest):
    return {item["id"] for item in manifest.published_by.metric_versions}


def _burst_sweep(calls, pages=1):
    """reconcile_page for a burst checkpoint: ``pages`` source pages, then done.

    Like the real one it keeps the last full pass (a burst is not a full pass)
    and records the checkpoint and floor every page was planned from.
    """

    def reconcile(analytics, *, now, checkpoint=None, floor=None, **kwargs):
        calls.append((checkpoint, floor))
        assert checkpoint is not None and checkpoint.floor is not None
        done = len(calls) % pages == 0
        cp = replace(
            checkpoint,
            phase="complete" if done else "sources",
            after=None
            if done
            else ("", "", "2026-10-31 06:00:00", "private-trace", "private-span"),
            day=None if done else checkpoint.floor.date(),
            last_day=None if done else checkpoint.floor.date(),
            scanned=checkpoint.scanned + 1,
            floor=None if done else checkpoint.floor,
        )
        batch = replace(
            EMPTY,
            cursor_updates={
                "reconciliation_cursor": cp.to_dict(),
                "reconciliation_started_at": cp.started_at,
                "reconciliation_completed_at": cp.completed_at,
                "pending_repair": not done,
            },
        )
        return changes.ReconciliationPage(batch, cp)

    return reconcile


def _dated(at, seen=None):
    """arrival_start_floor returning ``at``, recording what it was asked."""

    def date_arrivals(analytics, **kwargs):
        if seen is not None:
            seen.append(kwargs)
        return at

    return date_arrivals


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra, mode", [(-1, "history"), (0, "history"), (1, "forward")]
)
def test_size_limit_boundary_exact_limit_backfills_and_one_more_starts_forward(
    setup_runner, observe_project, workspace, monkeypatch, settings, extra, mode
):
    runner, _ = setup_runner
    settings.METRICS_BACKFILL_MAX_SPANS = 1000
    draft = make_version(observe_project, workspace)
    measure = _size(extra)
    monkeypatch.setattr(backfill, "history_window_spans", measure)
    assert _enter(runner) == {str(draft.id): mode}
    # One bounded measurement per pass, at the setting, over the policy window.
    assert measure.seen == [(str(observe_project.id), NOW, 2, 1000)]
    draft.refresh_from_db()
    draft.metric.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert draft.history_mode == mode
    if mode == "history":
        # Today's history backfill, unchanged: nothing else moved.
        assert draft.coverage_start is None and draft.activated_at is None
        assert draft.backfill_status == "pending" and draft.backfill_cursor is None
        assert draft.backfill_updated_at is None
        assert draft.metric.active_version_id is None
        assert state.pending_backfill and _watermarks(observe_project.id) == (0, 0, 0)
        runner.run_pass(max_chunks=1)
        draft.refresh_from_db()
        assert (draft.backfill_status, draft.backfill_days_done) == ("running", 1)
    else:
        # Decided, but served only by a publication that computes it.
        assert draft.coverage_start == NOW and draft.activated_at is None
        assert draft.backfill_status == "running"
        assert draft.metric.active_version_id is None
        assert not state.pending_backfill
    # A decided version is never measured or reclassified again.
    monkeypatch.setattr(backfill, "history_window_spans", _forbid)
    assert _enter(runner) == {}


def test_default_limit_is_150000_and_invalid_setting_fails_closed(settings):
    from django.core.exceptions import ImproperlyConfigured

    from tfc.settings.runtime_setting_specs import (
        RUNTIME_NUMERIC_SETTING_SPECS,
        load_numeric_settings,
    )

    assert (
        load_numeric_settings(RUNTIME_NUMERIC_SETTING_SPECS, source={})[
            "METRICS_BACKFILL_MAX_SPANS"
        ]
        == 150_000
    )
    if hasattr(settings, "METRICS_BACKFILL_MAX_SPANS"):
        del settings.METRICS_BACKFILL_MAX_SPANS
    assert backfill.max_backfill_spans() == 150_000
    settings.METRICS_BACKFILL_MAX_SPANS = 0
    with pytest.raises(ImproperlyConfigured):
        backfill.max_backfill_spans()


@pytest.mark.django_db
def test_forward_version_is_served_from_its_first_computing_publication_without_history(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    enrolled = NOW - timedelta(hours=3)
    DerivedMetricVersion.objects.filter(id=draft.id).update(created_at=enrolled)
    for name in ("day_page", "version_coverage_page", "start_sweep"):
        monkeypatch.setattr(backfill, name, _forbid)
    monkeypatch.setattr(changes, "reconcile_page", _forbid)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    assert _enter(runner) == {str(draft.id): "forward"}
    draft.refresh_from_db()
    draft.metric.refresh_from_db()
    assert draft.history_mode == "forward" and draft.coverage_start == enrolled
    assert draft.backfill_status == "running" and draft.backfill_cursor is None
    assert (draft.backfill_days_done, draft.backfill_days_total) == (0, 0)
    # The progress token moved, fencing any sealed initial intent. Nothing has
    # computed the version yet, so nothing serves it.
    assert draft.backfill_updated_at == NOW and draft.activated_at is None
    assert draft.metric.active_version_id is None
    assert not MetricProjectRefresh.for_project(observe_project.id).pending_backfill
    assert not backfill.pending_versions(observe_project.id).exists()
    # No backfill chunk runs for it: there is nothing to backfill.
    assert runner.run_pass() == []
    working = backfill.working_definitions(observe_project.id)
    assert [d.id for d in working] == [draft.id]
    assert backfill.forward_floor(working) == enrolled
    # A publication that consumes no arrivals (a compaction-like head) does
    # not prove [coverage_start, ceiling) was computed: still not served.
    _publish_empty_head(runner)
    draft.metric.refresh_from_db()
    assert draft.metric.active_version_id is None
    # The first publication that computes it and consumes arrivals serves it,
    # in that same head switch.
    clock[0] = NOW + timedelta(seconds=30)
    head = _consume(runner, clock[0])
    draft.refresh_from_db()
    draft.metric.refresh_from_db()
    assert draft.metric.active_version_id == draft.id
    assert draft.activated_at == clock[0] and draft.backfill_status == "complete"
    assert str(draft.id) in _prepared(head)
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id == (
        head.id
    )


@pytest.mark.django_db
def test_fresh_forward_project_discovers_from_enrollment_not_the_epoch(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    # created_at is the real wall clock, after the frozen lease clock: the
    # coverage start is never after the pass that decided it.
    draft = make_version(observe_project, workspace)
    assert draft.created_at > NOW
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    _enter(runner)
    draft.refresh_from_db()
    assert draft.coverage_start == NOW
    start = changes.ns(NOW)
    assert _watermarks(observe_project.id) == (start, start, start)
    seen = {}

    def discover(analytics, **kwargs):
        seen.update(kwargs)
        return EMPTY

    monkeypatch.setattr(changes, "discover_changes", discover)
    clock[0] = NOW + timedelta(seconds=30)
    real_incremental_changes(object(), runner.writer, None, [draft], now=clock[0])
    assert (
        seen["watermark_ns"],
        seen["eval_watermark_ns"],
        seen["remap_cursor_ns"],
    ) == (start, start, start)


@pytest.mark.django_db
def test_enrolled_project_keeps_its_watermarks_when_a_forward_version_arrives(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, _ = setup_runner
    draft = make_version(observe_project, workspace)
    head = _publish_empty_head(runner)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    assert _enter(runner) == {str(draft.id): "forward"}
    state = MetricProjectRefresh.for_project(observe_project.id)
    # Something was published: watermarks are owned by publications, not here.
    assert state.head_manifest_id == head.id
    assert _watermarks(observe_project.id) == (0, 0, 0)


@pytest.mark.django_db
@pytest.mark.parametrize("later", ["new_metric", "new_version"])
def test_later_forward_version_on_enrolled_large_project_never_sweeps_history(
    setup_runner, observe_project, workspace, monkeypatch, later
):
    runner, _ = setup_runner
    old = make_version(observe_project, workspace, active=True)
    head = _publish_empty_head(runner)
    enrolled = changes.ns(NOW - timedelta(days=5))
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        **dict.fromkeys(MetricProjectRefresh.MONOTONE_FIELDS, enrolled)
    )
    draft = (
        _named_version(observe_project, workspace, "Large project metric")
        if later == "new_metric"
        else make_version(observe_project, workspace, metric=old.metric)
    )
    for name in ("day_page", "version_coverage_page", "start_sweep"):
        monkeypatch.setattr(backfill, name, _forbid)
    monkeypatch.setattr(backfill.BackfillRunner, "run_initial_sweep_page", _forbid)
    monkeypatch.setattr(changes, "reconcile_page", _forbid)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    assert _enter(runner) == {str(draft.id): "forward"}
    draft.refresh_from_db()
    draft.metric.refresh_from_db()
    # The committed head never computed the new version: it is not served yet,
    # and an edited metric keeps serving its previous version meanwhile.
    assert draft.metric.active_version_id == (None if later == "new_metric" else old.id)
    assert draft.coverage_start == NOW
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id == head.id and not state.pending_backfill
    assert _watermarks(observe_project.id) == (enrolled, enrolled, enrolled)
    assert runner.run_pass() == []
    working = backfill.working_definitions(observe_project.id)
    # The earlier history version still serves: a mixed project keeps the full
    # retained-history repair sweep (no forward floor).
    assert {d.id for d in working} == {old.id, draft.id}
    assert backfill.forward_floor(working) is None
    head = _consume(runner, NOW)
    draft.refresh_from_db()
    assert draft.metric.active_version_id == draft.id
    assert str(draft.id) in _prepared(head)
    working = backfill.working_definitions(observe_project.id)
    if later == "new_metric":
        assert {d.id for d in working} == {old.id, draft.id}
        assert backfill.forward_floor(working) is None
    else:
        assert [d.id for d in working] == [draft.id]
        assert backfill.forward_floor(working) == NOW


def _overflowing_forward_project(runner, observe_project, workspace, monkeypatch):
    """A forward-only project enrolled 2 hours ago whose discovery overflows."""
    draft = make_version(observe_project, workspace)
    coverage = NOW - timedelta(hours=2)
    DerivedMetricVersion.objects.filter(id=draft.id).update(created_at=coverage)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    _enter(runner)
    draft.refresh_from_db()
    assert draft.coverage_start == coverage
    _consume(runner, NOW)  # enrolled and served; arrivals consumed up to NOW
    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    return draft, coverage


@pytest.mark.django_db
def test_forward_overflow_sweeps_only_the_burst_never_since_enrollment(
    setup_runner, observe_project, workspace, monkeypatch
):
    """An overflow long after enrollment sweeps from the burst's own starts."""
    runner, clock = setup_runner
    draft, coverage = _overflowing_forward_project(
        runner, observe_project, workspace, monkeypatch
    )
    burst = NOW - timedelta(minutes=10)
    asked, calls, starts = [], [], []
    monkeypatch.setattr(
        changes, "arrival_start_floor", _dated(burst, asked), raising=False
    )
    monkeypatch.setattr(changes, "reconcile_page", _burst_sweep(calls, pages=2))
    publish = runner.writer.refresh

    def refresh(change, definitions, scope, *, start, **kwargs):
        starts.append(start)
        return publish(change, definitions, scope, start=start, **kwargs)

    runner.writer.refresh = refresh
    clock[0] = NOW + timedelta(minutes=1)
    swept_from = clock[0]
    for _ in range(2):
        state = MetricProjectRefresh.for_project(observe_project.id)
        admission.run_incremental(runner.writer, object(), runner.scope, state)
        clock[0] += timedelta(seconds=30)
    # The burst is dated once, over discovery's own windows and ceiling.
    consumed = changes.ns(NOW)
    assert len(asked) == 1
    assert {k: asked[0][k] for k in ("watermark_ns", "ceiling")} == {
        "watermark_ns": consumed,
        "ceiling": swept_from,
    }
    # Every page sweeps from the burst's earliest start, never the coverage
    # start two hours back (nor the epoch), within the coverage floor.
    assert [cp.floor for cp, _ in calls] == [burst, burst]
    assert [floor for _, floor in calls] == [coverage, coverage]
    assert calls[0][0].started_at == swept_from
    assert starts == [coverage, coverage]
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_repair
    assert state.reconciliation_cursor["phase"] == "complete"
    assert "floor" not in state.reconciliation_cursor
    # Its completion consumed the window up to the sweep's start.
    swept = changes.ns(swept_from)
    assert _watermarks(observe_project.id) == (swept, swept, swept)


@pytest.mark.django_db
def test_forward_overflow_preserves_an_existing_full_sweep(
    setup_runner, observe_project, workspace, monkeypatch
):
    """An existing full sweep owns earlier ambiguous arrivals and cannot be
    replaced by a dateable burst: the burst watermark would skip that debt."""
    runner, clock = setup_runner
    _, coverage = _overflowing_forward_project(
        runner, observe_project, workspace, monkeypatch
    )
    full = changes.ReconciliationCheckpoint(
        started_at=NOW + timedelta(seconds=30),
        completed_at=NOW - timedelta(days=2),
        phase="sources",
        after=("", "", "2026-10-31 06:00:00", "private-trace", "private-span"),
        scanned=7,
        completed_passes=3,
        day=NOW.date(),
        last_day=NOW.date(),
        covered_through=NOW - timedelta(days=2, hours=1),
    )
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        pending_repair=True,
        reconciliation_cursor=full.to_dict(),
        reconciliation_started_at=full.started_at,
        reconciliation_completed_at=full.completed_at,
    )
    monkeypatch.setattr(changes, "arrival_start_floor", _forbid, raising=False)
    calls = []
    page = _two_page_sweep([])

    def reconcile(analytics, **kwargs):
        calls.append((kwargs["checkpoint"], kwargs["floor"]))
        return page(analytics, **kwargs)

    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    clock[0] = NOW + timedelta(minutes=1)
    state = MetricProjectRefresh.for_project(observe_project.id)
    admission.run_incremental(runner.writer, object(), runner.scope, state)
    checkpoint, floor = calls[0]
    assert checkpoint == full and floor == coverage
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_repair
    # The existing sweep's own start, not the new burst's start, is consumed.
    assert _watermarks(observe_project.id) == (changes.ns(full.started_at),) * 3
    assert state.reconciliation_cursor["completed_passes"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("pending", [False, True])
def test_forward_overflow_that_cannot_be_dated_keeps_todays_full_sweep(
    setup_runner, observe_project, workspace, monkeypatch, pending
):
    runner, clock = setup_runner
    _, coverage = _overflowing_forward_project(
        runner, observe_project, workspace, monkeypatch
    )
    full = changes.ReconciliationCheckpoint(
        started_at=NOW - timedelta(minutes=20),
        phase="sources",
        scanned=7,
        day=NOW.date(),
        last_day=NOW.date(),
    )
    if pending:
        MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
            pending_repair=True, reconciliation_cursor=full.to_dict()
        )

    def too_many(analytics, **kwargs):
        raise arrivals.ContinuousCandidateOverflow("metric candidate cap exceeded")

    planned = []

    def reconcile(analytics, *, checkpoint=None, floor=None, **kwargs):
        planned.append((checkpoint, floor))
        return _two_page_sweep([])(analytics, checkpoint=checkpoint, **kwargs)

    monkeypatch.setattr(changes, "arrival_start_floor", too_many, raising=False)
    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    clock[0] = NOW + timedelta(minutes=1)
    state = MetricProjectRefresh.for_project(observe_project.id)
    admission.run_incremental(runner.writer, object(), runner.scope, state)
    # Undatable: a pending sweep continues where it was, none is reset; with
    # none pending, today's full sweep from the coverage start is queued.
    assert planned == [(full if pending else None, coverage)]


def _read_error(code):
    try:
        try:
            raise ServerException("synthetic", code=code)
        except ServerException as exc:
            raise arrivals.ContinuousCandidateReadError(type(exc).__name__) from exc
    except arrivals.ContinuousCandidateReadError as wrapped:
        return wrapped


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error, overflow",
    [
        (lambda: _read_error(241), True),
        (lambda: _read_error(159), False),
        (lambda: _read_error(160), False),
        (lambda: changes.MetricChangeReadError("metric result budget exceeded"), False),
    ],
    ids=["memory_limit_241", "timeout_159", "too_slow_160", "result_budget"],
)
def test_only_a_discovery_memory_limit_is_classified_as_overflow(
    setup_runner, monkeypatch, error, overflow
):
    runner, _ = setup_runner
    calls = []

    def discover(analytics, **kwargs):
        calls.append(kwargs)
        raise error()

    monkeypatch.setattr(changes, "discover_changes", discover)
    with pytest.raises(arrivals.ContinuousCandidateReadError) as raised:
        real_incremental_changes(object(), runner.writer, None, [], now=NOW)
    assert (type(raised.value) is arrivals.ContinuousCandidateOverflow) == overflow
    if overflow:
        assert changes.memory_limit_exceeded(raised.value)
    assert len(calls) == 1  # discovery is never retried inside the call


@pytest.mark.django_db
def test_memory_limited_discovery_makes_one_durable_repair_page_per_pass(
    setup_runner, observe_project, workspace, monkeypatch
):
    """241 -> overflow cannot loop: one discovery and one page per pass."""
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    _enter(runner)
    draft.refresh_from_db()
    discoveries = []

    def discover(analytics, **kwargs):
        discoveries.append(kwargs["watermark_ns"])
        raise _read_error(241)

    calls = []
    monkeypatch.setattr(backfill, "incremental_changes", real_incremental_changes)
    monkeypatch.setattr(changes, "discover_changes", discover)
    monkeypatch.setattr(changes, "arrival_start_floor", _dated(None), raising=False)
    monkeypatch.setattr(changes, "reconcile_page", _burst_sweep(calls, pages=2))
    clock[0] = NOW + timedelta(seconds=30)
    started = clock[0]
    heads = []
    for _ in range(2):
        state = MetricProjectRefresh.for_project(observe_project.id)
        admission.run_incremental(runner.writer, object(), runner.scope, state)
        heads.append(
            MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        )
        clock[0] += timedelta(seconds=30)
    # Each pass: exactly one discovery, one published repair page, progress kept.
    assert discoveries == [changes.ns(NOW)] * 2
    # Nothing could be dated as arriving: the burst sweep starts at its own
    # start, never before the coverage start.
    assert [cp.floor for cp, _ in calls] == [started, started]
    assert [floor for _, floor in calls] == [draft.coverage_start] * 2
    assert len(set(heads)) == 2 and None not in heads
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_repair
    # The completed sweep consumed arrivals to its start: the window shrank.
    assert _watermarks(observe_project.id) == (changes.ns(started),) * 3


@pytest.mark.django_db
def test_serializers_and_read_window_expose_forward_coverage(
    observe_project, workspace
):
    version = make_version(observe_project, workspace, active=True)
    coverage = NOW - timedelta(minutes=7)
    DerivedMetricVersion.objects.filter(id=version.id).update(
        history_mode="forward", coverage_start=coverage
    )
    version.refresh_from_db()
    data = DerivedMetricVersionSerializer(version).data
    assert data["history_mode"] == "forward"
    assert (
        datetime.fromisoformat(data["coverage_start"].replace("Z", "+00:00"))
        == coverage
    )
    manifest = SimpleNamespace(
        id=uuid4(), generation=3, published_by=SimpleNamespace(committed_at=NOW)
    )
    provenance = reads.provenance(manifest, version)
    assert provenance["history_mode"] == "forward"
    assert provenance["coverage_start"] == coverage
    wire = json.loads(json.dumps(provenance, default=str))
    checked = MetricReadProvenanceSerializer(data=wire)
    assert checked.is_valid(), checked.errors
    assert checked.validated_data["coverage_start"] == coverage
    # Only units starting at or after 11:53 are computed, so the 11:45 bucket
    # that holds coverage is partial. Values are claimed only from the first
    # whole bucket, 12:00 (the next boundary at or after coverage).
    assert coverage == datetime.fromisoformat("2026-10-31T11:53:00+00:00")
    assert reads.window_is_calculated(version, {"window_start": NOW})
    assert not reads.window_is_calculated(
        version, {"window_start": NOW - timedelta(minutes=15)}
    )
    # A coverage start exactly on a boundary claims that bucket itself.
    exact = NOW - timedelta(minutes=15)
    DerivedMetricVersion.objects.filter(id=version.id).update(coverage_start=exact)
    version.refresh_from_db()
    assert reads.window_is_calculated(version, {"window_start": exact})
    assert not reads.window_is_calculated(
        version, {"window_start": exact - timedelta(minutes=15)}
    )
    legacy = _named_version(observe_project, workspace, "Legacy metric", active=True)
    legacy_wire = json.loads(
        json.dumps(reads.provenance(manifest, legacy), default=str)
    )
    assert legacy_wire["history_mode"] is None
    assert legacy_wire["coverage_start"] is None
    assert MetricReadProvenanceSerializer(data=legacy_wire).is_valid()


@pytest.mark.django_db
def test_forward_metric_before_its_first_publication_reads_as_not_calculated_yet(
    auth_client, observe_project, workspace, enabled_metrics
):
    """Decided forward-only, not yet computed: nothing claims to be active.

    The metric has no active definition, so the page shows its first
    calculation (with where values will begin) and every read answers the
    retryable "not calculated yet" code without touching ClickHouse.
    """
    from tracer.tests.test_derived_metric_api import body

    created = auth_client.post(ROOT, body(observe_project), format="json")
    assert created.status_code == 201, created.data
    metric_id = created.data["id"]
    with patch.object(backfill, "history_window_spans", _size(1)):
        with project_lease(
            observe_project.id, holder="forward-read", clock=lambda: NOW
        ) as lease:
            with lifecycle_lock_budget(None):
                decided = backfill.enter_backfill(
                    lease, object(), retention_days=lambda: 2
                )
    version = DerivedMetricVersion.objects.get(metric_id=metric_id, version=1)
    assert decided == {str(version.id): "forward"}
    detail = auth_client.get(f"{ROOT}{metric_id}/").data
    assert detail["definition"] is None
    draft = detail["draft_definition"]
    assert (draft["history_mode"], draft["backfill_status"]) == ("forward", "running")
    assert datetime.fromisoformat(draft["coverage_start"].replace("Z", "+00:00")) == (
        version.coverage_start
    )
    query = {k: v for k, v in run_body().items() if k.startswith("window_")}
    with patch("tracer.services.derived_metrics.reads.read_store") as store:
        for action in ("kpi", "trend", "drivers"):
            result = auth_client.get(f"{ROOT}{metric_id}/{action}/", query)
            assert result.status_code == 503, (action, result.data)
            assert result.data["code"] == "metric_recalculating", action
            assert result.data["retryable"] is True and "value" not in result.data
        store.assert_not_called()


@pytest.mark.django_db
def test_forward_version_on_an_old_head_is_not_readable_or_snapshotable(
    setup_runner, observe_project, workspace, enabled_metrics
):
    """An already-enrolled project's old head has no values for a new version.

    Even a persisted forward version from the pre-atomic-activation behavior
    must wait until a head after activation or one prepared with its version.
    The read and snapshot both fail before touching ClickHouse or creating a pin.
    """
    runner, _ = setup_runner
    old = make_version(observe_project, workspace, active=True)
    head = _publish_empty_head(runner)
    forward = make_version(observe_project, workspace, metric=old.metric)
    activated = datetime.now().astimezone() + timedelta(minutes=1)
    DerivedMetricVersion.objects.filter(id=forward.id).update(
        history_mode="forward",
        coverage_start=NOW,
        backfill_status="complete",
        activated_at=None,
    )
    DerivedMetric.activate_version(
        forward.metric_id,
        forward.id,
        expected_active_id=old.id,
        now=activated,
    )
    forward.refresh_from_db()
    metric = DerivedMetric.objects.select_related("active_version", "project").get(
        id=forward.metric_id
    )
    assert head.published_by.committed_at < activated
    assert not any(
        item.get("id") == str(forward.id) for item in head.published_by.metric_versions
    )

    def pinned(connection, project_id, *, metric_id=None):
        assert str(project_id) == str(observe_project.id)
        assert str(metric_id) == str(metric.id)
        return nullcontext((head, forward))

    with patch.object(reads, "pinned_read", side_effect=pinned):
        with pytest.raises(reads.MetricUnavailableError) as read_error:
            with reads.metric_read(metric, store=object()):
                pass
    assert read_error.value.code == "metric_recalculating"
    with pytest.raises(reads.MetricUnavailableError) as snapshot:
        snapshots.request_snapshot(
            metric,
            {
                "window_start": NOW - timedelta(days=1),
                "window_end": NOW,
                "idempotency_key": str(uuid4()),
            },
            None,
        )
    assert snapshot.value.code == "metric_recalculating"
    assert not MetricGenerationPin.objects.filter(
        project_id=observe_project.id, consumer_kind="snapshot"
    ).exists()


@pytest.mark.django_db
def test_forward_snapshot_waits_until_its_version_is_in_the_committed_head(
    setup_runner, observe_project, workspace, enabled_metrics
):
    runner, _ = setup_runner
    old = make_version(observe_project, workspace, active=True)
    head = _publish_empty_head(runner)
    forward = make_version(observe_project, workspace, metric=old.metric)
    activated = datetime.now().astimezone() + timedelta(minutes=1)
    DerivedMetricVersion.objects.filter(id=forward.id).update(
        history_mode="forward",
        coverage_start=NOW,
        backfill_status="complete",
        activated_at=None,
    )
    DerivedMetric.activate_version(
        forward.metric_id,
        forward.id,
        expected_active_id=old.id,
        now=activated,
    )
    metric = DerivedMetric.objects.select_related("active_version").get(
        id=forward.metric_id
    )
    assert head.published_by.committed_at < activated
    assert not any(
        item.get("id") == str(forward.id) for item in head.published_by.metric_versions
    )
    with pytest.raises(reads.MetricUnavailableError) as snapshot:
        snapshots.request_snapshot(
            metric,
            {
                "window_start": NOW - timedelta(days=1),
                "window_end": NOW,
                "idempotency_key": str(uuid4()),
            },
            None,
        )
    assert snapshot.value.code == "metric_recalculating"
    assert not MetricGenerationPin.objects.filter(
        project_id=observe_project.id, consumer_kind="snapshot"
    ).exists()


@pytest.mark.django_db
def test_history_initial_sweep_covers_only_the_window_the_size_gate_counted(
    setup_runner, observe_project, workspace, monkeypatch
):
    """A project at or below the limit in the history window is swept only
    over that window: older retained spans never extend its first sweep."""
    runner, clock = setup_runner
    first = make_version(observe_project, workspace)
    second = _named_version(observe_project, workspace, "Second history metric")
    measured = []

    def measure(analytics, project_id, *, now, retention_days, limit):
        measured.append(now - timedelta(days=backfill.history_days(retention_days)))
        return limit

    monkeypatch.setattr(backfill, "history_window_spans", measure)
    assert _enter(runner) == {str(first.id): "history", str(second.id): "history"}
    floors = []
    sweep = _two_page_sweep([])

    def reconcile(analytics, **kwargs):
        floors.append(kwargs.get("floor"))
        return sweep(analytics, **kwargs)

    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    runner.run_pass(max_chunks=1)
    clock[0] += timedelta(seconds=30)
    runner.run_pass(max_chunks=1)
    first.refresh_from_db()
    window = datetime.fromisoformat(first.backfill_cursor["start"])
    # The size gate counted [now - 2 days, now) under a 2-day retention; the
    # sweep's every page is bounded to the same instant.
    assert measured == [window] and window == NOW - timedelta(days=2)
    assert floors == [window, window]
    assert first.backfill_cursor["phase"] == "complete"
