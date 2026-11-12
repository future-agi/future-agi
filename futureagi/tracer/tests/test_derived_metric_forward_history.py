# ruff: noqa: F811 -- shared PG state-machine fixtures
"""Start with no history: projects above METRICS_BACKFILL_MAX_SPANS.

PG state-machine proofs with the real lease, ledger, publication and CAS; only
payload preparation and ClickHouse reads are fakes (as in the backfill tests).
The live count, sweep floor, Code 241 cause chain and compute filter are proved
against ClickHouse in test_derived_metric_forward_history_clickhouse.py.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.derived_metric import DerivedMetricVersion, MetricProjectRefresh
from tracer.selectors.eval_tasks import continuous_candidates as arrivals
from tracer.serializers.derived_metric import (
    DerivedMetricVersionSerializer,
    MetricReadProvenanceSerializer,
)
from tracer.services.derived_metrics import admission, backfill, changes, reads
from tracer.services.derived_metrics.backfill import (
    incremental_changes as real_incremental_changes,
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


@pytest.mark.django_db
@pytest.mark.parametrize("extra, mode", [(-1, "history"), (0, "history"), (1, "forward")])
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
        assert draft.coverage_start == NOW and draft.activated_at == NOW
        assert draft.metric.active_version_id == draft.id
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
def test_forward_version_activates_from_enrollment_without_any_history_page(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, _ = setup_runner
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
    assert draft.backfill_status == "complete" and draft.backfill_cursor is None
    assert (draft.backfill_days_done, draft.backfill_days_total) == (0, 0)
    # The progress token moved, fencing any sealed initial intent.
    assert draft.backfill_updated_at == NOW and draft.activated_at == NOW
    assert draft.metric.active_version_id == draft.id
    assert not MetricProjectRefresh.for_project(observe_project.id).pending_backfill
    assert not backfill.pending_versions(observe_project.id).exists()
    # No backfill chunk runs for it: there is nothing left to backfill.
    assert runner.run_pass() == []
    working = backfill.working_definitions(observe_project.id)
    assert [d.id for d in working] == [draft.id]
    assert backfill.forward_floor(working) == enrolled


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
    assert draft.metric.active_version_id == draft.id
    assert draft.coverage_start == NOW
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id == head.id and not state.pending_backfill
    assert _watermarks(observe_project.id) == (enrolled, enrolled, enrolled)
    assert runner.run_pass() == []
    working = backfill.working_definitions(observe_project.id)
    if later == "new_metric":
        # The earlier history metric still serves; a mixed project keeps the
        # full retained-history repair sweep (no forward floor).
        assert {d.id for d in working} == {old.id, draft.id}
        assert backfill.forward_floor(working) is None
    else:
        assert [d.id for d in working] == [draft.id]
        assert backfill.forward_floor(working) == NOW


@pytest.mark.django_db
def test_forward_only_burst_overflow_sweeps_from_coverage_start_never_epoch(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    coverage = NOW - timedelta(hours=2)
    DerivedMetricVersion.objects.filter(id=draft.id).update(created_at=coverage)
    monkeypatch.setattr(backfill, "history_window_spans", _size(1))
    _enter(runner)
    floors, starts = [], []
    sweep = _two_page_sweep([])

    def reconcile(analytics, **kwargs):
        floors.append(kwargs["floor"])
        return sweep(analytics, **kwargs)

    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    publish = runner.writer.refresh

    def refresh(change, definitions, scope, *, start, **kwargs):
        starts.append(start)
        return publish(change, definitions, scope, start=start, **kwargs)

    runner.writer.refresh = refresh
    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    clock[0] = NOW + timedelta(seconds=30)
    swept_from = clock[0]
    for _ in range(2):
        state = MetricProjectRefresh.for_project(observe_project.id)
        admission.run_incremental(runner.writer, object(), runner.scope, state)
        clock[0] += timedelta(seconds=30)
    # Bounded to [coverage_start, ceiling): never the epoch nor 90 days back.
    assert floors == [coverage, coverage] and starts == [coverage, coverage]
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_repair
    assert state.reconciliation_cursor["phase"] == "complete"
    swept = changes.ns(swept_from)
    assert _watermarks(observe_project.id) == (swept, swept, swept)


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
    discoveries, floors = [], []

    def discover(analytics, **kwargs):
        discoveries.append(kwargs["watermark_ns"])
        raise _read_error(241)

    sweep = _two_page_sweep([])

    def reconcile(analytics, **kwargs):
        floors.append(kwargs["floor"])
        return sweep(analytics, **kwargs)

    monkeypatch.setattr(backfill, "incremental_changes", real_incremental_changes)
    monkeypatch.setattr(changes, "discover_changes", discover)
    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    clock[0] = NOW + timedelta(seconds=30)
    started = clock[0]
    heads = []
    for _ in range(2):
        state = MetricProjectRefresh.for_project(observe_project.id)
        admission.run_incremental(runner.writer, object(), runner.scope, state)
        heads.append(MetricProjectRefresh.for_project(observe_project.id).head_manifest_id)
        clock[0] += timedelta(seconds=30)
    # Each pass: exactly one discovery, one published repair page, progress kept.
    assert discoveries == [changes.ns(NOW)] * 2
    assert floors == [draft.coverage_start] * 2
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
    # Values exist from the coverage start's own 15-minute bucket onward.
    bucket = NOW - timedelta(minutes=15)
    assert reads.window_is_calculated(version, {"window_start": bucket})
    assert not reads.window_is_calculated(
        version, {"window_start": bucket - timedelta(minutes=15)}
    )
    legacy = make_version(observe_project, workspace, active=True)
    legacy_wire = json.loads(
        json.dumps(reads.provenance(manifest, legacy), default=str)
    )
    assert legacy_wire["history_mode"] is None
    assert legacy_wire["coverage_start"] is None
    assert MetricReadProvenanceSerializer(data=legacy_wire).is_valid()
