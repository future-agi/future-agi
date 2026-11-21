"""PG state-machine tests; production CH discovery/recompute is tested separately."""

import json
from datetime import UTC, date, datetime, timedelta
from types import MethodType, SimpleNamespace

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics import backfill, changes
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    collect_history,
    lifecycle_lock_budget,
)
from tracer.services.derived_metrics.lease import project_lease
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_refresh import Recorder

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
EMPTY = changes.ChangeBatch((), (), (), (), (), {})


class _EmptySession:
    """Native-session stand-in: scratch captures of an empty store need no replay."""

    def __init__(self):
        self.socket = object()


class EmptyPolicyRecorder(Recorder):
    def __init__(self):
        super().__init__()
        self.checkpoint_projects = set()
        self.checkpoint_calls = []
        self.client = SimpleNamespace(
            connection=_EmptySession(), disconnect=lambda: None
        )

    def _restore_transients(self):
        # The session never drops, so no registered capture needs a replay.
        pass

    def consolidate_checkpoint(self, project_id, *, deadline=None):
        # Empty physical ranges need no layout work. Still enforce the scope of
        # the real store's post-publication consolidation callback.
        assert not self.checkpoint_projects or self.checkpoint_projects == {
            str(project_id)
        }
        self.checkpoint_calls.append((str(project_id), deadline))

    def execute(self, query, params=None):
        # Pass-scoped session captures of an empty physical store: a native
        # session probe, scratch DDL, and the (empty) measure and digest reads.
        if query == "SELECT 1":
            return [(1,)]
        if query.startswith(
            ("CREATE TEMPORARY TABLE metric_capture_", "DROP TEMPORARY TABLE")
        ) and ("metric_capture_" in query.split("(", 1)[0]):
            return []
        if query.startswith("SELECT count(), sum(length(formatRow('RowBinary'"):
            return [(0, 0, "0")] if "cityHash64" in query else [(0, 0)]
        # Empty proposals have no oldest retained anchor.
        if query.startswith("SELECT minOrNull(unit_start) FROM ("):
            return [(None,)]
        if query.startswith("SELECT count()"):
            return [(0,)]
        # Checkpoint discovery now nominates the last key of a bounded range,
        # not a count. An empty relation has no last key (not a zero tuple).
        from tracer.services.derived_metrics.publication import MAX_COMPACTION_ROWS

        assert query.startswith("SELECT tuple(")
        assert "SELECT DISTINCT" in query
        assert "LIMIT %(checkpoint_limit)s" in query
        assert query.endswith("DESC LIMIT 1")
        assert params["checkpoint_limit"] == MAX_COMPACTION_ROWS
        assert params["checkpoint_org"] and params["checkpoint_project"]
        assert params.get("checkpoint_deltas") or params.get("checkpoint_base")
        self.checkpoint_projects.add(str(params["checkpoint_project"]))
        return []


@pytest.mark.parametrize(
    "query", ["SELECT * FROM field_values", "DELETE FROM field_values"]
)
def test_empty_policy_recorder_rejects_unbounded_or_mutating_queries(query):
    with pytest.raises(AssertionError):
        EmptyPolicyRecorder().execute(query, {})


def test_empty_policy_recorder_preserves_zero_count_result():
    assert EmptyPolicyRecorder().execute("SELECT count() FROM empty") == [(0,)]


def test_empty_policy_recorder_consolidates_only_its_nominated_project():
    recorder = EmptyPolicyRecorder()
    recorder.checkpoint_projects.add("owned-project")
    recorder.consolidate_checkpoint("owned-project")
    assert recorder.checkpoint_calls == [("owned-project", None)]
    with pytest.raises(AssertionError):
        recorder.consolidate_checkpoint("foreign-project", deadline=1234.5)
    assert recorder.checkpoint_calls == [("owned-project", None)]


def test_empty_policy_recorder_rejects_unsupported_callback_arguments():
    recorder = EmptyPolicyRecorder()
    with pytest.raises(TypeError):
        recorder.consolidate_checkpoint("owned-project", 1234.5)
    with pytest.raises(TypeError):
        recorder.consolidate_checkpoint("owned-project", unsupported=True)
    assert recorder.checkpoint_calls == []


@pytest.mark.django_db
def test_checkpoint_consolidation_forwards_deadline_and_defaults_on_replay(
    setup_runner, observe_project, monkeypatch
):
    from tracer.services.derived_metrics import refresh

    runner, _ = setup_runner
    writer = runner.writer
    initial = writer.begin()
    writer.seal(initial)
    writer.stage_and_publish(initial)
    assert writer.store.checkpoint_calls == []
    monkeypatch.setattr(refresh, "monotonic", lambda: 1234.5)
    head = writer.compact()
    assert writer.store.checkpoint_calls == [(str(observe_project.id), 1834.5)]
    # The temporary compaction budget must not leak into a later replay. This
    # calls the real publication path again, not the recorder directly.
    assert writer.stage_and_publish(head.published_by).id == head.id
    assert writer.store.checkpoint_calls == [
        (str(observe_project.id), 1834.5),
        (str(observe_project.id), None),
    ]


def scrubbed(original):
    """Expected cursor after a privacy scrub: positions gone, progress kept."""
    result = dict(original)
    if result.get("after") is not None:
        result.update(after=None, keyset_start=None)
    coverage = result.get("coverage")
    if isinstance(coverage, dict) and coverage.get("after") is not None:
        result["coverage"] = {**coverage, "after": None, "keyset_start": None}
    return result


def assert_scrubbed(stored, original):
    assert stored == scrubbed(original)
    assert "private" not in json.dumps(stored)
    assert not backfill.has_private_keyset(stored)


def make_version(project, workspace, *, active=False, metric=None, **overrides):
    metric = metric or DerivedMetric.objects.create(
        project=project,
        workspace=workspace,
        organization=workspace.organization,
        name="History metric",
    )
    number = metric.versions.count() + 1
    recipe = {
        "kind": "average",
        "level": "conversation",
        "formula": "mean(x)",
        "inputs": [
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "x",
                "reducer": "latest",
            }
        ],
        "selection_unit": "session",
        "weighting": "none",
        "definition_hash": str(number) * 64,
    }
    recipe.update(overrides)
    if active:
        recipe.update(backfill_status="complete", activated_at=NOW - timedelta(days=1))
    version = DerivedMetricVersion.objects.create(
        metric=metric, version=number, **recipe
    )
    metric.current_version = number
    if active:
        metric.active_version = version
    metric.save(update_fields=["current_version", "active_version", "updated_at"])
    MetricProjectRefresh.objects.update_or_create(
        project=project, defaults={"pending_backfill": not active}
    )
    return version


def install_empty_planners(monkeypatch):
    days = []

    def page(analytics, writer, head, cursor, **kwargs):
        days.append((writer.lease.project_id, cursor["day_end"]))
        done = cursor["done"] + 1
        return EMPTY, {
            **cursor,
            "done": done,
            "day_end": (
                datetime.fromisoformat(cursor["day_end"]) - timedelta(days=1)
            ).isoformat(),
            "phase": "coverage" if done == cursor["total"] else "sources",
        }

    def coverage(analytics, *, now, checkpoint=None, **kwargs):
        cp = changes.ReconciliationCheckpoint(
            started_at=checkpoint.started_at if checkpoint else now,
            completed_at=now,
            phase="complete",
            completed_passes=1,
            covered_through=now,
        )
        return changes.ReconciliationPage(
            changes.ChangeBatch(
                (),
                (),
                (),
                (),
                (),
                {
                    "reconciliation_cursor": cp.to_dict(),
                    "reconciliation_started_at": cp.started_at,
                    "reconciliation_completed_at": cp.completed_at,
                    "pending_repair": False,
                },
            ),
            cp,
        )

    monkeypatch.setattr(backfill, "day_page", page)
    monkeypatch.setattr(changes, "reconcile_page", coverage)
    monkeypatch.setattr(
        backfill,
        "version_coverage_page",
        lambda analytics, writer, head, cursor, *, now, **kwargs: coverage(
            analytics,
            now=now,
            checkpoint=changes.ReconciliationCheckpoint.from_dict(cursor["coverage"])
            if cursor["coverage"]
            else None,
        ),
    )
    monkeypatch.setattr(backfill, "incremental_changes", lambda *args, **kwargs: EMPTY)
    return days


def empty_refresh(self, change, definitions, scope, *, purpose, publication, **kwargs):
    """Only payload preparation is fake; leases, ledgers, publish and CAS are real."""
    self.abort_stale()
    head = MetricProjectRefresh.for_project(self.lease.project_id).head_manifest
    if head and head.delta_attempt_ids:
        checkpoint = self.begin(purpose="compaction")
        self.seal(checkpoint)
        self.stage_and_publish(checkpoint)
    attempt = self.begin(
        purpose=purpose,
        versions=[
            {
                "id": str(d.id),
                "metric_id": str(d.metric_id),
                "version": d.version,
                "level": d.level,
                "definition_hash": d.definition_hash,
            }
            for d in definitions
        ],
    )
    self.freeze(attempt, FIELD_VALUES, "SELECT frozen_test_row")
    self.seal(attempt, cursors=change.cursor_updates, publication=publication)
    return self.stage_and_publish(attempt)


@pytest.fixture
def setup_runner(observe_project, workspace, monkeypatch):
    install_empty_planners(monkeypatch)
    clock = [NOW]
    with project_lease(
        observe_project.id, holder="backfill-test", clock=lambda: clock[0]
    ) as lease:
        writer = RefreshWriter(
            lease,
            EmptyPolicyRecorder(),
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
            retention_days=lambda: 2,
        )
        writer.refresh = MethodType(empty_refresh, writer)
        runner = backfill.BackfillRunner(
            writer,
            object(),
            Scope(
                workspace.organization_id,
                workspace.id,
                observe_project.id,
            ),
        )
        # These PG state-machine tests drive a frozen lease clock (including
        # takeovers by a second worker). The lifecycle lock budget is covered by
        # the integration-fence tests; here it would fence fixture/ORM writes.
        with lifecycle_lock_budget(None):
            yield runner, clock


@pytest.mark.django_db
def test_progress_activation_atomic_retry_and_pin_cleanup(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(
        observe_project, workspace, metric=old.metric, value_labels={"match": "New"}
    )
    old_head = runner.writer.begin()
    runner.writer.seal(old_head)
    old_head = runner.writer.stage_and_publish(old_head)
    pin = capture_pin(
        observe_project.id,
        metric_id=old.metric_id,
        consumer_kind="snapshot",
        ttl=timedelta(hours=1),
        now=clock[0],
    )
    assert pin.definition_version_id == old.id
    assert draft.backfill_status == "pending"
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert (
        draft.backfill_status,
        draft.backfill_days_done,
        draft.backfill_days_total,
    ) == ("running", 1, 2)
    old.metric.refresh_from_db()
    assert old.metric.active_version_id == old.id
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert draft.backfill_days_done == 2 and draft.backfill_status == "running"
    assert draft.backfill_cursor["phase"] == "coverage"
    checkpoint = runner.writer.begin(purpose="compaction")
    runner.writer.seal(checkpoint)
    runner.writer.stage_and_publish(checkpoint)
    before = MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
    original = DerivedMetric.activate_version

    def fail(*args, **kwargs):
        raise RuntimeError("crash at atomic activation")

    monkeypatch.setattr(DerivedMetric, "activate_version", fail)
    with pytest.raises(RuntimeError, match="atomic activation"):
        runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert draft.backfill_status == "running"
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id == before
    )
    monkeypatch.setattr(DerivedMetric, "activate_version", original)
    final = runner.run_pass(max_chunks=1)[0]  # resumes sealed exact-byte plan
    draft.refresh_from_db()
    old.metric.refresh_from_db()
    assert draft.backfill_status == "complete" and draft.activated_at == clock[0]
    assert old.metric.active_version_id == draft.id
    assert old.metric.active_semantics()["value_labels"] == {"match": "New"}
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert not state.pending_backfill
    assert state.backfill_last_progress_at == clock[0]
    assert state.incremental_last_success_at is None
    assert pin.is_usable(now=clock[0])
    assert backfill.obsolete_versions(observe_project.id, now=clock[0]) == ()
    assert runner.writer.stage_and_publish(final.published_by).id == final.id
    # Expiry is explicit, never deletion of a still-usable queued capture.
    clock[0] += timedelta(hours=2)
    assert not pin.is_usable(now=clock[0])
    assert backfill.obsolete_versions(observe_project.id, now=clock[0]) == (
        (old.metric_id, 1),
    )
    # A checkpoint allows the existing bounded collector to remove unpinned
    # history only after its independent attempt TTL, not at activation time.
    # Recorder cannot execute a checkpoint SELECT; use a real PG empty one.
    from tracer.services.derived_metrics.lease import ProjectLease

    grant = MetricProjectRefresh.take_lease(
        observe_project.id, holder="cleanup", ttl=timedelta(minutes=5), now=clock[0]
    )
    runner.writer.lease = ProjectLease(grant, clock=lambda: clock[0])
    compact = runner.writer.begin(purpose="compaction")
    runner.writer.seal(compact)
    head = runner.writer.stage_and_publish(compact)
    MetricRefreshAttempt.objects.exclude(id=head.published_by_id).update(
        expires_at=clock[0] - timedelta(seconds=1)
    )
    assert collect_history(observe_project.id, now=clock[0]) is not None
    assert not MetricReadManifest.objects.filter(id=old_head.id).exists()
    assert MetricReadManifest.objects.count() == 1


@pytest.mark.django_db
def test_crash_staging_leaves_progress_and_watermarks_unchanged(
    setup_runner, observe_project, workspace
):
    runner, _ = setup_runner
    draft = make_version(observe_project, workspace)
    runner.writer.store.fail_after = 0
    with pytest.raises(RuntimeError, match="injected"):
        runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert draft.backfill_cursor is None and draft.backfill_days_done == 0
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id is None and state.watermark_ns == 0
    assert state.pending_backfill
    runner.writer.store.fail_after = None
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert draft.backfill_days_done == 1
    assert MetricRefreshAttempt.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "retention,total", [(0, 90), (-1, 90), (7, 7), (90, 90), (180, 90)]
)
def test_newest_first_horizon_is_minimum_of_retention_and_90_days(
    observe_project, workspace, retention, total
):
    draft = make_version(observe_project, workspace)
    cp = backfill._initial_cursor(draft, now=NOW, retention_days=retention)
    assert cp["total"] == total
    assert datetime.fromisoformat(cp["end"]) - datetime.fromisoformat(
        cp["start"]
    ) == timedelta(days=total)
    assert cp["day_end"] == NOW.isoformat()


@pytest.mark.django_db
def test_every_working_recipe_has_own_grain_groups_labels_and_dependencies(
    observe_project, workspace
):
    old = make_version(
        observe_project,
        workspace,
        active=True,
        group_dims=["model"],
        value_labels={"match": "Old"},
    )
    draft = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        level="span",
        selection_unit="span",
        group_dims=["provider"],
        value_labels={"match": "New"},
    )
    got = backfill.working_definitions(observe_project.id, [old])
    assert {d.id for d in got} == {old.id, draft.id}
    assert next(d for d in got if d.id == old.id).semantics() == old.semantics()
    assert next(d for d in got if d.id == draft.id).semantics() == draft.semantics()
    newer = make_version(observe_project, workspace, metric=old.metric, group_dims=[])
    assert {d.id for d in backfill.pending_versions(observe_project.id)} == {newer.id}
    assert {d.id for d in backfill.working_definitions(observe_project.id)} == {
        old.id,
        newer.id,
    }


@pytest.mark.django_db
def test_superseded_staging_version_cannot_activate_or_drop_new_pending_flag(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, _ = setup_runner
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    runner.run_pass(max_chunks=2)
    original = runner.writer.store.insert
    newest = []

    def edit(table, payload):
        if not newest:
            newest.append(make_version(observe_project, workspace, metric=old.metric))
        original(table, payload)

    monkeypatch.setattr(runner.writer.store, "insert", edit)
    runner.run_pass(max_chunks=1)
    old.metric.refresh_from_db()
    draft.refresh_from_db()
    assert draft.backfill_status == "complete" and draft.activated_at is None
    assert old.metric.active_version_id == old.id
    assert MetricProjectRefresh.for_project(observe_project.id).pending_backfill
    assert list(backfill.pending_versions(observe_project.id)) == newest


@pytest.mark.django_db
def test_fake_clock_two_project_admission_progresses_90_days_without_idle_cadence(
    observe_project, workspace, monkeypatch
):
    from tracer.models.project import Project

    days = install_empty_planners(monkeypatch)
    second = Project.objects.create(
        name="Second history project",
        workspace=workspace,
        organization=workspace.organization,
        model_type=observe_project.model_type,
        trace_type=observe_project.trace_type,
    )
    drafts = [make_version(p, workspace) for p in (observe_project, second)]
    clock, rounds, incremental_work = (
        [NOW],
        0,
        {str(p.id): 0 for p in (observe_project, second)},
    )
    admissions = {str(p.id): 0 for p in (observe_project, second)}

    def arrivals(analytics, writer, head, definitions, **kwargs):
        key = str(writer.lease.project_id)
        incremental_work[key] += 1
        state = MetricProjectRefresh.for_project(writer.lease.project_id)
        return changes.ChangeBatch(
            (), (), (), (), (), {"watermark_ns": state.watermark_ns + 1}
        )

    monkeypatch.setattr(backfill, "incremental_changes", arrivals)
    while any(backfill.pending_versions(d.metric.project_id).exists() for d in drafts):
        rounds += 1
        assert rounds <= 32
        for draft in drafts:
            project_id = draft.metric.project_id
            with project_lease(
                project_id, holder="fair-test", clock=lambda: clock[0]
            ) as lease:
                writer = RefreshWriter(
                    lease,
                    EmptyPolicyRecorder(),
                    org_id=workspace.organization_id,
                    workspace_id=workspace.id,
                    retention_days=lambda: 0,
                )
                writer.refresh = MethodType(empty_refresh, writer)
                runner = backfill.BackfillRunner(
                    writer,
                    object(),
                    Scope(workspace.organization_id, workspace.id, project_id),
                )
                assert len(runner.run_pass()) <= 3
                admissions[str(project_id)] += 1  # competing admission every tick
        clock[0] += timedelta(seconds=60)
    assert rounds == 31
    assert (
        clock[0] - NOW
    ).total_seconds() == 1860  # includes every simulated scheduling gap
    assert all(v == 31 for v in admissions.values())
    assert all(v == 91 for v in incremental_work.values())
    for draft in drafts:
        draft.refresh_from_db()
        assert draft.backfill_days_done == draft.backfill_days_total == 90
        ends = [
            datetime.fromisoformat(end)
            for project, end in days
            if project == draft.metric.project_id
        ]
        assert ends == [NOW - timedelta(days=i) for i in range(90)]
        state = MetricProjectRefresh.for_project(draft.metric.project_id)
        assert state.incremental_last_success_at is None
        assert state.watermark_ns == 91


@pytest.mark.django_db
def test_bulk_repair_uses_persisted_full_coverage_and_rejects_inflight_reset(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    make_version(observe_project, workspace, active=True)
    seen = []

    def reconcile(analytics, *, now, checkpoint, **kwargs):
        seen.append(checkpoint)
        cp = changes.ReconciliationCheckpoint(
            started_at=checkpoint.started_at if checkpoint else now,
            phase="units" if checkpoint is None else "complete",
            after=None,
            scanned=1 if checkpoint is None else 2,
            completed_at=now if checkpoint else None,
            completed_passes=int(checkpoint is not None),
            covered_through=now if checkpoint else None,
        )
        batch = changes.ChangeBatch(
            (),
            (),
            (),
            (),
            (),
            {
                "reconciliation_cursor": cp.to_dict(),
                "pending_repair": cp.phase != "complete",
                "reconciliation_started_at": cp.started_at,
                "reconciliation_completed_at": cp.completed_at,
            },
        )
        return changes.ReconciliationPage(batch, cp)

    monkeypatch.setattr(changes, "reconcile_page", reconcile)
    backfill.queue_bulk_change(observe_project.id, now=clock[0])
    runner.run_repair_page()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.pending_repair and state.reconciliation_cursor["phase"] == "units"
    checkpoint = runner.writer.begin(purpose="compaction")
    runner.writer.seal(checkpoint)
    prior = runner.writer.stage_and_publish(checkpoint).id
    original = runner.writer.store.insert

    def reset(table, payload):
        clock[0] += timedelta(seconds=1)
        backfill.queue_bulk_change(observe_project.id, now=clock[0])
        original(table, payload)

    monkeypatch.setattr(runner.writer.store, "insert", reset)
    with pytest.raises(MetricStateConflict, match="invalidated"):
        runner.run_repair_page()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id == prior and state.pending_repair
    assert state.reconciliation_cursor is None
    monkeypatch.setattr(runner.writer.store, "insert", original)
    runner.run_repair_page()
    runner.run_repair_page()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert (
        not state.pending_repair and state.reconciliation_cursor["phase"] == "complete"
    )
    assert seen[0] is None and seen[1].phase == "units" and seen[2] is None


@pytest.mark.django_db
@pytest.mark.parametrize("chunks", [0, 4])
def test_admission_never_runs_unbounded_chunks(setup_runner, chunks):
    runner, _ = setup_runner
    with pytest.raises(ValueError, match="one to three"):
        runner.run_pass(max_chunks=chunks)


@pytest.mark.django_db
def test_takeover_replans_unpublished_day_without_skipping_coverage(
    setup_runner, observe_project, workspace
):
    from tracer.models.derived_metric import MetricFencingError
    from tracer.services.derived_metrics.lease import ProjectLease

    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    old_writer = runner.writer
    old_writer.store.fail_after = 0
    with pytest.raises(RuntimeError, match="injected"):
        runner.run_pass(max_chunks=1)
    abandoned = MetricRefreshAttempt.objects.get(state="staging")
    clock[0] += timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        observe_project.id, holder="takeover", ttl=timedelta(minutes=5), now=clock[0]
    )
    old_writer.store.fail_after = None
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: clock[0]),
        old_writer.store,
        org_id=workspace.organization_id,
        workspace_id=workspace.id,
        retention_days=lambda: 2,
    )
    replacement.refresh = MethodType(empty_refresh, replacement)
    recovered = backfill.BackfillRunner(replacement, object(), runner.scope)
    recovered.run_pass(max_chunks=1)
    draft.refresh_from_db()
    abandoned.refresh_from_db()
    assert draft.backfill_days_done == 1 and abandoned.state == "aborted"
    assert draft.backfill_cursor["done"] == 1
    with pytest.raises(MetricFencingError):
        old_writer.stage_and_publish(abandoned)


def _named_version(project, workspace, name, **overrides):
    metric = DerivedMetric.objects.create(
        project=project,
        workspace=workspace,
        organization=workspace.organization,
        name=name,
    )
    return make_version(project, workspace, metric=metric, **overrides)


def _overflow(*args, **kwargs):
    raise changes.arrivals.ContinuousCandidateOverflow("metric candidate cap exceeded")


def _two_page_sweep(pages):
    """A retained-history pass that publishes one private page, then completes."""

    def reconcile(analytics, *, now, checkpoint=None, **kwargs):
        pages.append(checkpoint)
        done = checkpoint is not None
        cp = changes.ReconciliationCheckpoint(
            started_at=checkpoint.started_at if done else now,
            completed_at=now if done else None,
            phase="complete" if done else "sources",
            after=None
            if done
            else ("", "", "2026-10-31 06:00:00", "private-trace", "private-span"),
            scanned=2 if done else 1,
            completed_passes=int(done),
            day=None if done else date(2026, 10, 31),
            last_day=None if done else date(2026, 10, 31),
            covered_through=checkpoint.started_at if done else None,
        )
        batch = changes.ChangeBatch(
            (),
            (),
            (),
            (),
            (),
            {
                # The project's repair cursor fields: the sweep must drop them.
                "reconciliation_cursor": cp.to_dict(),
                "reconciliation_started_at": cp.started_at,
                "reconciliation_completed_at": cp.completed_at,
                "pending_repair": not done,
            },
        )
        return changes.ReconciliationPage(batch, cp)

    return reconcile


@pytest.mark.django_db
def test_overflowing_initial_history_publishes_definition_progress_not_repair(
    setup_runner, observe_project, workspace, monkeypatch
):
    """Initial discovery above the admission cap is backfill, never repair.

    Every page publishes ALL pending versions' durable progress. No arrival
    cursor moves until the completing page, which activates every participant
    in its own head switch and consumes arrivals only up to the pass START.
    """
    runner, clock = setup_runner
    first = make_version(observe_project, workspace)
    second = _named_version(observe_project, workspace, "Second history metric")
    pages = []
    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    monkeypatch.setattr(changes, "reconcile_page", _two_page_sweep(pages))
    started = clock[0]
    runner.run_pass(max_chunks=1)
    state = MetricProjectRefresh.for_project(observe_project.id)
    head = state.head_manifest
    assert head.published_by.purpose == "backfill"
    for draft in (first, second):
        draft.refresh_from_db()
        assert draft.backfill_status == "running" and draft.activated_at is None
        assert draft.backfill_cursor["phase"] == backfill.SWEEP_PHASE
        assert draft.backfill_cursor["coverage"]["phase"] == "sources"
        assert backfill.has_private_keyset(draft.backfill_cursor)
        assert draft.backfill_days_done == 1 and draft.backfill_days_total == 2
    assert first.backfill_cursor["coverage"] == second.backfill_cursor["coverage"]
    assert (state.watermark_ns, state.eval_watermark_ns, state.remap_cursor_ns) == (
        0,
        0,
        0,
    )
    assert state.reconciliation_cursor is None and not state.pending_repair
    assert state.pending_backfill and state.backfill_last_progress_at
    clock[0] += timedelta(seconds=30)
    runner.run_pass(max_chunks=1)
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id != head.id
    assert state.head_manifest.published_by.purpose == "backfill"
    assert pages[0] is None and pages[1].started_at == started
    for draft in (first, second):
        draft.refresh_from_db()
        draft.metric.refresh_from_db()
        assert draft.backfill_status == "complete"
        assert draft.metric.active_version_id == draft.id
        assert draft.activated_at == clock[0]
        assert draft.backfill_days_done == draft.backfill_days_total == 2
        assert not backfill.has_private_keyset(draft.backfill_cursor)
    ceiling = changes.ns(started)
    assert (state.watermark_ns, state.eval_watermark_ns, state.remap_cursor_ns) == (
        ceiling,
        ceiling,
        ceiling,
    )
    assert state.reconciliation_cursor is None and not state.pending_repair
    assert not state.pending_backfill


@pytest.mark.django_db
def test_overflow_sweep_crash_then_privacy_reset_replans_instead_of_replaying_stale_cas(
    setup_runner,
    observe_project,
    workspace,
    monkeypatch,
):
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    runner.writer.store.fail_after = 0
    with pytest.raises(RuntimeError, match="injected"):
        runner.run_pass(max_chunks=1)
    failed = MetricRefreshAttempt.objects.get(state="staging")
    assert failed.purpose == "backfill"
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert draft.backfill_cursor is None and draft.activated_at is None
    assert state.watermark_ns == 0 and state.head_manifest_id is None
    clock[0] += timedelta(seconds=1)
    # An erasure-style scrub moves the progress token of the sealed intent.
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    runner.writer.store.fail_after = None
    runner.run_pass(max_chunks=1)
    failed.refresh_from_db()
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert failed.state == "aborted"
    assert draft.backfill_status == "complete" and draft.activated_at == clock[0]
    # The replanned pass started after the reset; it consumed only to its start.
    assert state.watermark_ns == changes.ns(clock[0])
    assert not state.pending_repair and not state.pending_backfill


@pytest.mark.django_db
def test_overflow_with_serving_versions_keeps_history_progress_beside_repair(
    setup_runner, observe_project, workspace, monkeypatch
):
    """Repair must not starve a new version: the backfill lane still publishes."""
    runner, clock = setup_runner
    make_version(observe_project, workspace, active=True)
    draft = _named_version(observe_project, workspace, "New history metric")
    monkeypatch.setattr(backfill, "incremental_changes", _overflow)
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest.published_by.purpose == "backfill"
    assert draft.backfill_days_done == 1 and draft.backfill_status == "running"
    # Arrivals were rerouted to the durable repair sweep, never consumed here.
    assert state.pending_repair and state.reconciliation_cursor is None
    assert state.watermark_ns == 0


@pytest.mark.django_db
@pytest.mark.parametrize("interleaving", ["activation", "pin", "attempt", "current"])
def test_omission_rechecks_eligibility_at_final_publication(
    setup_runner, observe_project, workspace, monkeypatch, interleaving
):
    runner, clock = setup_runner
    writer = runner.writer
    old = make_version(observe_project, workspace, active=True)
    initial = writer.begin()
    writer.seal(initial)
    before = writer.stage_and_publish(initial)
    current = make_version(observe_project, workspace, metric=old.metric, active=True)
    publish, captured = writer.stage_and_publish, []

    def interleave(attempt):
        assert backfill.obsolete_versions(observe_project.id, now=clock[0]) == (
            (old.metric_id, 1),
        )
        if interleaving in {"activation", "pin"}:
            DerivedMetric.activate_version(
                old.metric_id, old.id, expected_active_id=current.id, now=clock[0]
            )
            if interleaving == "pin":
                captured.append(
                    capture_pin(
                        observe_project.id,
                        metric_id=old.metric_id,
                        consumer_kind="snapshot",
                        ttl=timedelta(hours=1),
                        now=clock[0],
                    )
                )
                DerivedMetric.activate_version(
                    old.metric_id, current.id, expected_active_id=old.id, now=clock[0]
                )
        elif interleaving == "attempt":
            writer.begin(versions=[{"id": str(old.id)}])
        else:
            old.metric.current_version = old.version
            old.metric.save(update_fields=["current_version", "updated_at"])
        return publish(attempt)

    monkeypatch.setattr(writer, "stage_and_publish", interleave)
    with pytest.raises(MetricStateConflict, match="eligibility changed"):
        writer.compact()
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        == before.id
    )
    old.refresh_from_db()
    assert old.pins_revoked_at is None and old.backfill_status == "complete"
    if captured:
        assert captured[0].is_usable(now=clock[0])


@pytest.mark.django_db
def test_retirement_and_omission_roll_back_together_then_fence_activation(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    writer = runner.writer
    old = make_version(observe_project, workspace, active=True)
    initial = writer.begin()
    writer.seal(initial)
    before = writer.stage_and_publish(initial)
    current = make_version(observe_project, workspace, metric=old.metric, active=True)
    publish = MetricReadManifest.publish

    def crash(*args, **kwargs):
        assert DerivedMetricVersion.objects.get(id=old.id).pins_revoked_at is not None
        raise RuntimeError("head switch failed")

    monkeypatch.setattr(MetricReadManifest, "publish", crash)
    with pytest.raises(RuntimeError, match="head switch failed"):
        writer.compact()
    old.refresh_from_db()
    assert old.pins_revoked_at is None
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        == before.id
    )
    monkeypatch.setattr(MetricReadManifest, "publish", publish)
    writer.abort_stale()
    writer.compact()
    old.refresh_from_db()
    assert old.pins_revoked_at == clock[0]
    assert old.backfill_status == "complete" and old.activated_at is not None
    with pytest.raises(MetricStateConflict):
        DerivedMetric.activate_version(
            old.metric_id, old.id, expected_active_id=current.id
        )
    with pytest.raises(MetricStateConflict):
        capture_pin(
            observe_project.id,
            definition_version_id=old.id,
            consumer_kind="request",
            ttl=timedelta(minutes=1),
            now=clock[0],
        )


@pytest.mark.django_db
def test_superseded_draft_keyset_is_scrubbed_on_edit_and_draft_is_collectible(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    private = backfill._initial_cursor(draft, now=clock[0], retention_days=2)
    private.update(
        after=["", "", "2026-10-31 06:00:00", "private-trace", "private-span"],
        coverage={"after": ["private-unit"]},
    )
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=private)
    newest = make_version(observe_project, workspace, metric=old.metric)
    draft.refresh_from_db()
    assert draft.backfill_cursor is None and draft.backfill_updated_at is not None
    assert backfill.obsolete_versions(observe_project.id, now=clock[0]) == (
        (old.metric_id, draft.version),
    )
    assert list(backfill.pending_versions(observe_project.id)) == [newest]


@pytest.mark.django_db
@pytest.mark.parametrize("reason", ["erased", "retention_expired"])
def test_privacy_reset_scrubs_nested_keysets_and_rejects_sealed_initial_intent(
    setup_runner, observe_project, workspace, reason
):
    from tracer.services.derived_metrics.deletion_protocol import revoke_generations

    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    runner.writer.store.fail_after = 0
    with pytest.raises(RuntimeError, match="injected"):
        runner.run_pass(max_chunks=1)
    stale = MetricRefreshAttempt.objects.get(state="staging")
    # expected=None must still become stale even though no cursor was published.
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    runner.writer.store.fail_after = None
    with pytest.raises(MetricStateConflict, match="progress changed"):
        runner.writer.stage_and_publish(stale)
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id is None
    runner.run_pass(max_chunks=1)
    stale.refresh_from_db()
    draft.refresh_from_db()
    assert stale.state == "aborted" and draft.backfill_days_done == 1
    private = {
        **draft.backfill_cursor,
        "coverage": {"after": ["private-trace", "private-span"]},
    }
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=private)
    repair = {"phase": "units", "after": ["conversation", "private-unit"]}
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        reconciliation_cursor=repair,
        pending_repair=True,
    )
    tokens = (
        DerivedMetricVersion.objects.get(id=draft.id).backfill_updated_at,
        MetricProjectRefresh.for_project(observe_project.id).reconciliation_started_at,
    )
    revoke_generations(observe_project.id, reason=reason, now=clock[0])
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    # Every private position goes; the published day count and phase survive.
    assert_scrubbed(draft.backfill_cursor, private)
    assert draft.backfill_cursor["done"] == 1 and draft.backfill_days_done == 1
    assert_scrubbed(state.reconciliation_cursor, repair)
    assert state.pending_repair
    assert draft.backfill_updated_at > tokens[0]
    assert state.reconciliation_started_at > tokens[1]


@pytest.mark.django_db(transaction=True)
def test_retention_scrubs_stranded_keysets_even_without_field_rows(
    setup_runner, observe_project, workspace
):
    from tracer.services.derived_metrics.retention import expire_project

    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    cursor = backfill._initial_cursor(
        draft, now=clock[0] - timedelta(days=3), retention_days=2
    )
    cursor["coverage"] = {"after": ["private-trace", "private-span"]}
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=cursor)
    repair = {
        "started_at": (clock[0] - timedelta(days=3)).isoformat(),
        "after": ["conversation", "private-unit"],
    }
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        reconciliation_cursor=repair,
        pending_repair=True,
    )
    expire_project(runner.writer, [draft], retention_days=lambda: 1, now=clock[0])
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    # Undated stranded keysets are conservatively expired; progress is not.
    assert_scrubbed(draft.backfill_cursor, cursor)
    assert_scrubbed(state.reconciliation_cursor, repair)
    assert state.pending_backfill and state.pending_repair


@pytest.mark.django_db
def test_supersession_during_private_page_staging_rejects_then_replans(
    setup_runner, observe_project, workspace, monkeypatch
):
    runner, clock = setup_runner
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)

    def private_page(analytics, writer, head, cursor, **kwargs):
        return EMPTY, {
            **cursor,
            "after": ["", "", "2026-10-31 06:00:00", "private-trace", "private-span"],
        }

    monkeypatch.setattr(backfill, "day_page", private_page)
    insert, newest = runner.writer.store.insert, []

    def supersede(table, payload):
        if not newest:
            newest.append(make_version(observe_project, workspace, metric=old.metric))
        insert(table, payload)

    monkeypatch.setattr(runner.writer.store, "insert", supersede)
    with pytest.raises(MetricStateConflict, match="progress changed"):
        runner.run_pass(max_chunks=1)
    stale = MetricRefreshAttempt.objects.get(state="staging")
    draft.refresh_from_db()
    assert draft.backfill_cursor is None and draft.activated_at is None
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id is None
    monkeypatch.setattr(runner.writer.store, "insert", insert)
    runner.run_pass(max_chunks=1)
    stale.refresh_from_db()
    newest[0].refresh_from_db()
    assert stale.state == "aborted"
    assert newest[0].backfill_cursor["after"][-1] == "private-span"
    assert backfill.pending_versions(observe_project.id).first().id == newest[0].id


@pytest.mark.django_db
def test_privacy_reset_rejects_sealed_initial_repair_intent_without_cursor_aba(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    make_version(observe_project, workspace, active=True)
    backfill.queue_bulk_change(observe_project.id, now=clock[0])
    runner.writer.store.fail_after = 0
    with pytest.raises(RuntimeError, match="injected"):
        runner.run_repair_page()
    stale = MetricRefreshAttempt.objects.get(state="staging")
    before = MetricProjectRefresh.for_project(observe_project.id)
    assert before.reconciliation_cursor is None
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    after = MetricProjectRefresh.for_project(observe_project.id)
    assert after.reconciliation_cursor is None
    assert after.reconciliation_started_at > before.reconciliation_started_at
    runner.writer.store.fail_after = None
    with pytest.raises(MetricStateConflict, match="invalidated"):
        runner.writer.stage_and_publish(stale)
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id is None
    runner.run_repair_page()
    stale.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert stale.state == "aborted" and not state.pending_repair
    assert state.reconciliation_cursor["phase"] == "complete"


@pytest.mark.django_db
@pytest.mark.parametrize("repair_phase", ["sources", "units"])
def test_retention_resets_nested_source_keys_and_undated_repair_keys_on_shrink(
    setup_runner, observe_project, workspace, repair_phase
):
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    cursor = backfill._initial_cursor(draft, now=clock[0], retention_days=1)
    cursor.update(
        phase="coverage",
        coverage={
            "phase": "sources",
            "after": ["", "", "2026-10-28 06:00:00", "private-trace", "private-span"],
        },
    )
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=cursor)
    cp = {
        "started_at": clock[0].isoformat(),
        "phase": repair_phase,
        "retention_days": 2,
        "after": ["", "", "2026-10-28 06:00:00", "private-trace", "private-span"]
        if repair_phase == "sources"
        else ["conversation", "private-unit"],
    }
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        reconciliation_cursor=cp, pending_repair=True
    )
    # The shrunk horizon is the cutoff each keyset is dated against.
    backfill.scrub_expired_progress(
        observe_project.id,
        cutoff=clock[0] - timedelta(days=1),
        now=clock[0],
    )
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert_scrubbed(draft.backfill_cursor, cursor)
    assert_scrubbed(state.reconciliation_cursor, cp)
    assert state.pending_repair


@pytest.mark.django_db
def test_newer_never_activated_definition_is_not_a_superseded_draft(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    old = make_version(observe_project, workspace, active=True)
    newer = DerivedMetricVersion.objects.create(
        metric=old.metric, version=2, **old.semantics()
    )
    cursor = backfill._initial_cursor(newer, now=clock[0], retention_days=2)
    cursor["after"] = ["", "", "2026-10-31 06:00:00", "private-trace", "private-span"]
    DerivedMetricVersion.objects.filter(id=newer.id).update(backfill_cursor=cursor)
    # Native callers may explicitly compute a newer immutable version before
    # promoting the metric's draft pointer. Only a LOWER version is abandoned.
    backfill.scrub_superseded_progress(observe_project.id)
    assert backfill.obsolete_versions(observe_project.id, now=clock[0]) == ()
    attempt = runner.writer.begin()
    runner.writer.seal(attempt)
    runner.writer.stage_and_publish(attempt)
    runner.writer.compact()
    newer.refresh_from_db()
    assert newer.pins_revoked_at is None and newer.backfill_cursor == cursor


@pytest.mark.django_db
@pytest.mark.parametrize("phase", ["sources", "dependencies", "units"])
def test_retained_coverage_uses_its_own_checkpoint_not_exhausted_daily_boundary(
    setup_runner, observe_project, workspace, phase
):
    runner, clock = setup_runner
    draft = make_version(observe_project, workspace)
    cursor = backfill._initial_cursor(draft, now=clock[0], retention_days=1)
    cursor.update(
        phase="coverage",
        done=1,
        day_end=cursor["start"],
        coverage={
            "phase": phase,
            "started_at": clock[0].isoformat(),
            "after": ["", "", "2026-10-31 06:00:00", "private-trace", "private-span"]
            if phase == "sources"
            else ["private-trace", "private-span"],
        },
    )
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=cursor)
    backfill.scrub_expired_progress(
        observe_project.id,
        cutoff=clock[0] - timedelta(days=1),
        now=clock[0],
    )
    draft.refresh_from_db()
    assert draft.backfill_cursor == cursor
    token = draft.backfill_updated_at
    clock[0] += timedelta(days=2)
    backfill.scrub_expired_progress(
        observe_project.id,
        cutoff=clock[0] - timedelta(days=1),
        now=clock[0],
    )
    draft.refresh_from_db()
    assert_scrubbed(draft.backfill_cursor, cursor)
    assert draft.backfill_updated_at != token


@pytest.mark.django_db
def test_privacy_reset_redacts_legacy_attempt_keyset_but_keeps_frozen_hashes(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    make_version(observe_project, workspace)
    attempt = runner.writer.begin(
        ceilings={
            "cursor_updates": {
                "watermark_ns": 42,
                "reconciliation_cursor": {
                    "phase": "units",
                    "after": ["conversation", "private-unit"],
                },
            }
        }
    )
    before_hash = attempt.prepared_sha256
    runner.writer.seal(attempt)
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    attempt.refresh_from_db()
    assert attempt.source_ceilings["cursor_updates"] == {"watermark_ns": 42}
    assert attempt.prepared_sha256 == before_hash
    assert attempt.ledger_chunks.exists()


@pytest.mark.django_db
def test_repair_publication_cannot_rewind_privacy_token_and_reuse_empty_cursor_cas(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    make_version(observe_project, workspace, active=True)
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    before = MetricProjectRefresh.for_project(observe_project.id)
    stale = {
        "repair": {
            "cursor": None,
            "started_at": before.reconciliation_started_at.isoformat(),
        }
    }
    checkpoint = changes.ReconciliationCheckpoint(
        started_at=clock[0] - timedelta(microseconds=1),
        completed_at=clock[0],
        phase="complete",
        completed_passes=1,
    )
    attempt = runner.writer.begin(purpose="repair")
    runner.writer.freeze(attempt, FIELD_VALUES, "SELECT frozen_test_row")
    runner.writer.seal(
        attempt,
        cursors={
            "reconciliation_cursor": checkpoint.to_dict(),
            "reconciliation_started_at": checkpoint.started_at,
        },
        publication=stale,
    )
    runner.writer.stage_and_publish(attempt)
    after = MetricProjectRefresh.for_project(observe_project.id)
    assert after.reconciliation_started_at >= before.reconciliation_started_at
    backfill.scrub_private_progress(observe_project.id, now=clock[0])
    reset = MetricProjectRefresh.for_project(observe_project.id)
    # The completed pass holds no private position, so its coverage survives.
    assert reset.reconciliation_cursor == after.reconciliation_cursor
    assert not backfill.has_private_keyset(reset.reconciliation_cursor)
    assert reset.reconciliation_started_at > after.reconciliation_started_at
    assert reset.reconciliation_started_at > before.reconciliation_started_at
    with pytest.raises(MetricStateConflict, match="invalidated"):
        backfill.check_repair_publication(runner.writer, stale)


@pytest.mark.django_db
def test_exact_keyset_age_does_not_expire_the_retained_remainder_of_an_hour(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    clock[0] = NOW.replace(minute=30)
    draft = make_version(observe_project, workspace)
    cursor = backfill._initial_cursor(draft, now=clock[0], retention_days=1)
    retained = clock[0] - timedelta(days=1) + timedelta(minutes=5)
    cursor.update(
        phase="coverage",
        done=1,
        day_end=cursor["start"],
        coverage={
            "phase": "sources",
            "started_at": clock[0].isoformat(),
            "after": ["", "", "2026-10-30 12:00:00", "private-trace", "private-span"],
            "keyset_start": retained.isoformat(),
        },
    )
    DerivedMetricVersion.objects.filter(id=draft.id).update(backfill_cursor=cursor)
    backfill.scrub_expired_progress(
        observe_project.id,
        cutoff=clock[0] - timedelta(days=1),
        now=clock[0],
    )
    draft.refresh_from_db()
    assert draft.backfill_cursor == cursor
    clock[0] += timedelta(minutes=6)
    backfill.scrub_expired_progress(
        observe_project.id,
        cutoff=clock[0] - timedelta(days=1),
        now=clock[0],
    )
    draft.refresh_from_db()
    assert_scrubbed(draft.backfill_cursor, cursor)


@pytest.mark.django_db
def test_logical_expiry_publication_drops_only_aged_keysets_and_keeps_progress(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    writer = runner.writer
    floor = clock[0] - timedelta(days=2)
    retained = (floor + timedelta(hours=1)).isoformat()
    aged = (floor - timedelta(hours=1)).isoformat()
    draft = make_version(observe_project, workspace)
    other = make_version(
        observe_project,
        workspace,
        metric=DerivedMetric.objects.create(
            project=observe_project,
            workspace=workspace,
            organization=workspace.organization,
            name="Other history metric",
        ),
    )
    keep = backfill._initial_cursor(other, now=clock[0], retention_days=2)
    keep.update(
        after=["", "", "2026-10-30 06:00:00", "retained-trace", "retained-span"],
        keyset_start=retained,
    )
    DerivedMetricVersion.objects.filter(id=other.id).update(backfill_cursor=keep)
    start = backfill._initial_cursor(draft, now=clock[0], retention_days=2)
    DerivedMetricVersion.objects.filter(id=draft.id).update(
        backfill_cursor=start, backfill_updated_at=clock[0]
    )
    # Planned before the expiry checkpoint: one day done, a keyset it then aged.
    planned = {
        **start,
        "done": 1,
        "day_end": (clock[0] - timedelta(days=1)).isoformat(),
        "phase": "fields",
        "after": ["private-unit"],
        "keyset_start": aged,
    }
    proposal = {
        **changes.ReconciliationCheckpoint(
            started_at=clock[0],
            phase="dependencies",
            after=("private-trace", "private-span"),
            scanned=7,
        ).to_dict(),
        "keyset_start": aged,
    }
    attempt = writer.begin(
        purpose="compaction",
        ceilings={"logical_expiry": True, "retention_floor": floor.isoformat()},
    )
    writer.seal(
        attempt,
        cursors={
            "reconciliation_cursor": proposal,
            "reconciliation_started_at": clock[0],
            "pending_repair": True,
        },
        publication={
            "backfill": {
                "version_id": str(draft.id),
                "expected": start,
                "expected_updated_at": clock[0].isoformat(),
                "next": planned,
            }
        },
    )
    head = writer.stage_and_publish(attempt)
    assert head.published_by.source_ceilings["logical_expiry"]
    draft.refresh_from_db()
    other.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    # Progress survives the expiry; only the aged private positions are gone.
    assert_scrubbed(draft.backfill_cursor, planned)
    assert draft.backfill_cursor["done"] == draft.backfill_days_done == 1
    assert draft.backfill_status == "running" and draft.backfill_updated_at > clock[0]
    assert other.backfill_cursor == keep and other.backfill_updated_at is None
    assert_scrubbed(state.reconciliation_cursor, proposal)
    assert state.reconciliation_cursor["scanned"] == 7 and state.pending_repair
    assert state.reconciliation_cursor["phase"] == "dependencies"


@pytest.mark.django_db
def test_omission_receipt_is_writer_only_and_history_gc_discharges_it_without_expiry(
    setup_runner, observe_project, workspace
):
    from tracer.models.derived_metric import MetricErasureRequest

    runner, clock = setup_runner
    writer = runner.writer
    writer.retention_days = lambda: 0  # unlimited: the expiry scrub never runs
    old = make_version(observe_project, workspace, active=True)
    initial = writer.begin()
    writer.seal(initial)
    writer.stage_and_publish(initial)
    make_version(observe_project, workspace, metric=old.metric, active=True)
    head = writer.compact()
    debt = MetricErasureRequest.objects.get(store_payload__protocol="metric_omission")
    ttl = timedelta(minutes=5)
    assert MetricErasureRequest.claim(debt.id, ttl=ttl, now=clock[0]) is None
    # The omitted rows still live in the older, unexpired namespace.
    assert backfill.discharge_omission_debts(observe_project.id, now=clock[0]) == 0
    debt.refresh_from_db()
    assert debt.status == "queued"
    clock[0] += timedelta(hours=1)
    MetricRefreshAttempt.objects.exclude(id=head.published_by_id).update(
        expires_at=clock[0] - timedelta(seconds=1)
    )
    assert collect_history(observe_project.id, now=clock[0]) is not None
    assert backfill.discharge_omission_debts(observe_project.id, now=clock[0]) == 1
    debt.refresh_from_db()
    assert debt.status == "done"
    assert debt.receipts == {
        "history_collected": {"through_generation": head.generation}
    }
    assert MetricErasureRequest.claim(debt.id, ttl=ttl, now=clock[0]) is None


@pytest.mark.django_db
def test_sealed_intents_cannot_publish_keysets_that_aged_before_publication(
    setup_runner, observe_project, workspace
):
    runner, clock = setup_runner
    writer = runner.writer
    draft = make_version(observe_project, workspace, active=False)
    start = backfill._initial_cursor(draft, now=clock[0], retention_days=2)
    DerivedMetricVersion.objects.filter(id=draft.id).update(
        backfill_cursor=start, backfill_updated_at=clock[0]
    )
    aged = (clock[0] - timedelta(days=2, minutes=1)).isoformat()
    planned = {
        **start,
        "phase": "dependencies",
        "after": ["private-trace", "private-span"],
        "keyset_start": aged,
    }
    attempt = writer.begin(purpose="backfill")
    writer.freeze(attempt, FIELD_VALUES, "SELECT frozen_test_row")
    writer.seal(
        attempt,
        publication={
            "backfill": {
                "version_id": str(draft.id),
                "expected": start,
                "expected_updated_at": clock[0].isoformat(),
                "next": planned,
            }
        },
    )
    head = writer.stage_and_publish(attempt)
    assert not head.published_by.source_ceilings.get("logical_expiry")
    draft.refresh_from_db()
    assert_scrubbed(draft.backfill_cursor, planned)
    assert draft.backfill_cursor["phase"] == "dependencies"
    proposal = {
        **changes.ReconciliationCheckpoint(
            started_at=clock[0], phase="units", after=("conversation", "private-unit")
        ).to_dict(),
        "keyset_start": aged,
    }
    repair = writer.begin(purpose="repair")
    writer.freeze(repair, FIELD_VALUES, "SELECT frozen_test_row")
    writer.seal(
        repair,
        cursors={"reconciliation_cursor": proposal, "pending_repair": True},
        publication={"repair": {"cursor": None, "started_at": None}},
    )
    writer.stage_and_publish(repair)
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert_scrubbed(state.reconciliation_cursor, proposal)
    assert state.reconciliation_cursor["phase"] == "units" and state.pending_repair
