"""Eval-task selection, reconciliation and finalization against a lagging replica.

Every test opts the nine TH-3119 models in to the real read router and uses
``tfc.tests.lagging_replica``: a second PostgreSQL connection frozen in a
REPEATABLE READ snapshot. Rows the engine writes after the freeze are on the
primary only, as on a streaming standby that has not applied them yet.

Only ClickHouse boundaries are replaced: candidate discovery and classifier
execution (the real filter compiler still builds each statement), and the span
reader that maps row identities to entry foreign keys. ORM routing, the
reconcile transaction, materialization and the stored entries are real.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import DatabaseError
from django.utils import timezone

import tfc.routers as routers
from model_hub.models.error_localizer_model import ErrorLocalizerTask
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401  (fixture)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.selectors.eval_tasks import continuous_candidates, row_resolver
from tracer.selectors.eval_tasks.row_resolver import (
    ResolvedRowSet,
    TraceFilterWitness,
)
from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.eval_tasks import entries as entries_module
from tracer.services.eval_tasks import reconciler as reconciler_module
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import materialize_pending
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.tests._ch_seed import seed_ch_span
from tracer.tests.eval_task_factories import make_config, make_template

pytestmark = pytest.mark.django_db(transaction=True)

_STALE_HASH = "hash-before-config-edit"


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


# -- fixtures and ClickHouse boundaries ---------------------------------------


@pytest.fixture
def template(project):
    return make_template(organization=project.organization, workspace=project.workspace)


def _span(project, trace, *, name="replica span"):
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        parent_span_id=None,
        start_time=timezone.now() - timedelta(minutes=2),
    )


def _task(project, configs, *, run_type=RunType.CONTINUOUS, **fields):
    now = timezone.now()
    defaults = {
        "filters": {},
        "sampling_rate": 100.0,
        "spans_limit": 1_000,
        "status": EvalTaskStatus.RUNNING,
        "row_type": RowType.SPANS,
        "start_time": now - timedelta(hours=1),
    }
    if run_type == RunType.CONTINUOUS:
        defaults["continuous_cursor"] = now - timedelta(minutes=5)
    defaults.update(fields)
    task = EvalTask.objects.create(
        project=project,
        name="replica engine task",
        run_type=run_type,
        **defaults,
    )
    task.evals.add(*configs)
    return task


def _entry(task, span, config, *, status, **fields):
    return EvalLogger.objects.create(
        target_type=fields.pop("target_type", EvalTargetType.SPAN),
        observation_span=span,
        trace=span.trace,
        custom_eval_config=config,
        eval_task_id=str(task.id),
        status=status,
        **fields,
    )


def _primary_entries(task):
    return EvalLogger.objects.using("default").filter(
        eval_task_id=str(task.id), deleted=False
    )


class _SpanReader:
    """ClickHouse span reader stand-in: answers FK lookups from given spans."""

    def __init__(self, spans):
        self._spans = {str(span.id): span for span in spans}

    def list_by_ids(self, ids, *, project_id, columns=None, **_kwargs):
        return [
            {"id": span_id, "trace_id": str(self._spans[span_id].trace_id)}
            for span_id in ids
            if span_id in self._spans
        ]

    def list_root_spans_by_trace_ids(self, trace_ids, *, project_id, columns=None):
        roots = {str(span.trace_id): span for span in self._spans.values()}
        return {
            trace_id: {"id": str(roots[trace_id].id), "trace_id": trace_id}
            for trace_id in trace_ids
            if trace_id in roots
        }

    def close(self):
        pass


def _stub_span_reader(monkeypatch, spans):
    monkeypatch.setattr(entries_module, "get_reader", lambda: _SpanReader(spans))


def _param_values(params):
    values = set()
    for value in params.values():
        if isinstance(value, list | tuple | set | frozenset):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def _stub_continuous_clickhouse(monkeypatch, span, matches):
    """Discover ``span`` as the only arrival; classify it with ``matches``.

    The real v2 builder compiles every classifier statement from the metadata
    the resolver froze; ``matches(params)`` plays ClickHouse executing it.
    Returns the kwargs the resolver built the classifier with.
    """

    from tracer.services.clickhouse.v2 import dispatch, query_service

    real_get_v2_class = dispatch.get_v2_class
    built_with: dict = {}

    def recording_class(query_type):
        real_class = real_get_v2_class(query_type)

        class Recording(real_class):
            def __init__(self, **kwargs):
                built_with.update(kwargs)
                super().__init__(**kwargs)

        return Recording

    class ClickHouse:
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            rows = [{"id": str(span.id)}] if matches(params) else []
            return SimpleNamespace(data=rows)

    monkeypatch.setattr(
        continuous_candidates,
        "discover_continuous_candidates",
        lambda *_args, **_kwargs: continuous_candidates.ContinuousCandidates(
            (str(span.id),), (str(span.id),)
        ),
    )
    monkeypatch.setattr(dispatch, "get_v2_class", recording_class)
    monkeypatch.setattr(query_service, "V2AnalyticsQueryService", ClickHouse)
    return built_with


def _has_eval_filter(value):
    return {
        "column_id": "has_eval",
        "filter_config": {
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def _eval_metric_filter(config_id, *, filter_op="is_null"):
    return {
        "column_id": str(config_id),
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "text",
            "filter_op": filter_op,
            "filter_value": None,
        },
    }


def _stub_proof(monkeypatch, *results):
    """Replace the ClickHouse proof with one result per reconcile pass."""

    pending = list(results)
    seen_cursors = []

    def resolve(task, *, ceiling=None):
        seen_cursors.append(task.continuous_cursor)
        return pending.pop(0)

    monkeypatch.setattr(reconciler_module, "resolve_desired_rows", resolve)
    return seen_cursors


# -- C30: finalization sees work claimed after the replica froze ---------------


@pytest.mark.parametrize("status", [EvalEntryStatus.RUNNING, EvalEntryStatus.PENDING])
def test_c30_finalization_waits_for_entry_written_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    trace,
    template,
    status,
):
    """An entry left running by an interrupted run (or requeued pending) is
    undrained work; a lagging replica that has not seen it must not let the
    historical workflow mark the task completed underneath it."""

    from tfc.temporal.eval_tasks.activities import (
        _finalize_task_sync,
        _get_task_state_sync,
    )

    config = make_config(project=project, template=template, name="c30")
    span = _span(project, trace)
    task = _task(project, [config], run_type=RunType.HISTORICAL)
    lagging_replica.freeze()
    entry = _entry(task, span, config, status=status)

    state = _get_task_state_sync(str(task.id))
    assert state["active"] is True
    assert state["has_undrained_work"] is True

    assert _finalize_task_sync(str(task.id))["finalized"] is False
    stored = EvalTask.objects.using("default").get(id=task.id)
    assert stored.status == EvalTaskStatus.RUNNING
    assert stored.end_time is None

    EvalLogger.objects.using("default").filter(id=entry.id).update(
        status=EvalEntryStatus.COMPLETED
    )
    assert _get_task_state_sync(str(task.id))["has_undrained_work"] is False
    assert _finalize_task_sync(str(task.id))["finalized"] is True
    stored = EvalTask.objects.using("default").get(id=task.id)
    assert stored.status == EvalTaskStatus.COMPLETED
    assert stored.end_time is not None


# -- C31: the has_eval project fence ------------------------------------------


def test_c31_has_eval_fence_reads_primary_project_configs(
    lagging_replica,  # noqa: F811
    project,
    observe_project,
    template,
):
    deleted = make_config(project=project, template=template, name="c31-a")
    kept = make_config(project=project, template=template, name="c31-b")
    make_config(project=observe_project, template=template, name="c31-other")
    lagging_replica.freeze()
    CustomEvalConfig.objects.using("default").filter(id=deleted.id).update(deleted=True)
    created = make_config(project=project, template=template, name="c31-c")

    fence = row_resolver._eval_config_ids_for_filters(
        str(project.id), [_has_eval_filter(False)]
    )

    assert set(fence) == {str(kept.id), str(created.id)}
    assert row_resolver._eval_config_ids_for_filters(str(project.id), []) is None


def test_c31_reconcile_materializes_row_once_its_only_eval_config_is_deleted(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """has_eval=false: the span was evaluated only by config A. The user
    deletes A, so the span no longer has a live eval and task B must pick it
    up; a fence read from the replica still contains A and skips the row
    while the cursor moves past it."""

    config_a = make_config(project=project, template=template, name="c31-a")
    config_b = make_config(project=project, template=template, name="c31-b")
    span = _span(project, trace)
    task = _task(project, [config_b], filters={"filters": [_has_eval_filter(False)]})
    lagging_replica.freeze()
    CustomEvalConfig.objects.using("default").filter(id=config_a.id).update(
        deleted=True
    )
    _stub_span_reader(monkeypatch, [span])
    built_with = _stub_continuous_clickhouse(
        monkeypatch,
        span,
        # ClickHouse holds one eval result for the span, from config A.
        matches=lambda params: str(config_a.id) not in _param_values(params),
    )

    reconcile(task)

    stored = list(_primary_entries(task))
    assert [(e.observation_span_id, e.custom_eval_config_id) for e in stored] == [
        (span.id, config_b.id)
    ]
    assert stored[0].status == EvalEntryStatus.PENDING
    assert built_with["eval_config_ids"] == [str(config_b.id)]


# -- C40: eval-value filter metadata ------------------------------------------


def test_c40_eval_filter_metadata_resolves_new_config_on_primary(
    lagging_replica,  # noqa: F811
    project,
    template,
):
    from tracer.services.clickhouse.query_builders.filters import (
        resolve_eval_filter_metadata,
    )

    lagging_replica.freeze()
    created = make_config(project=project, template=template, name="c40")

    metadata = row_resolver._eval_filter_metadata_for_filters(
        str(project.id), [_eval_metric_filter(created.id)]
    )

    assert metadata == {
        str(created.id): EvalFilterMetadata((str(created.id),), "PASS_FAIL")
    }
    assert routers._force_primary_depth.get() == 0

    # Ordinary filter browsing calls the resolver directly: still replica-routed.
    with lagging_replica.capture() as replica_queries:
        browsing = resolve_eval_filter_metadata(str(created.id), [str(project.id)])
    assert browsing.config_ids == ()
    assert replica_queries.captured_queries


def test_c40_primary_context_is_restored_when_metadata_read_fails(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
):
    from tracer.services.clickhouse.query_builders import filters

    depths = []

    def failing_resolve(eval_id, project_ids):
        depths.append(routers._force_primary_depth.get())
        raise DatabaseError("primary unavailable")

    monkeypatch.setattr(filters, "resolve_eval_filter_metadata", failing_resolve)

    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded):
        row_resolver._eval_filter_metadata_for_filters(
            str(project.id), [_eval_metric_filter(uuid.uuid4())]
        )
    assert depths == [1]
    assert routers._force_primary_depth.get() == 0
    assert row_resolver._eval_filter_metadata_for_filters(str(project.id), []) is None


def test_c40_reconcile_applies_filter_on_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """Task B filters on "not yet scored by A" with A created moments ago.
    A replica that cannot see A resolves it to a known-empty config set, the
    compiler emits a no-match, and the eligible span is never materialized."""

    config_b = make_config(project=project, template=template, name="c40-b")
    span = _span(project, trace)
    task = _task(project, [config_b])
    lagging_replica.freeze()
    # The user creates the filter-only config, then points the task at it.
    config_a = make_config(project=project, template=template, name="c40-a")
    EvalTask.objects.filter(id=task.id).update(
        filters={"filters": [_eval_metric_filter(config_a.id)]}
    )
    _stub_span_reader(monkeypatch, [span])
    built_with = _stub_continuous_clickhouse(
        monkeypatch,
        span,
        # ClickHouse holds no result from A for the span: is_null over A's
        # results matches exactly when the statement is scoped to A.
        matches=lambda params: str(config_a.id) in _param_values(params),
    )

    reconcile(task)

    stored = list(_primary_entries(task))
    assert [(e.observation_span_id, e.custom_eval_config_id) for e in stored] == [
        (span.id, config_b.id)
    ]
    assert built_with["eval_filter_metadata"] == {
        str(config_a.id): EvalFilterMetadata((str(config_a.id),), "PASS_FAIL")
    }


# -- D09: trace filter witnesses ----------------------------------------------


def _witness(task, span, *, ordinal=0):
    return TraceFilterWitness(
        trace_id=str(span.trace_id),
        filter_ordinal=ordinal,
        column_id="final_status",
        col_type="SPAN_ATTRIBUTE",
        project_id=str(task.project_id),
        span_id=str(span.id),
        start_time=span.start_time,
    )


def _witness_metadata(task, span):
    witness = _witness(task, span)
    return {
        "_task_selection": {
            "filter_witnesses": [
                {
                    "filter_ordinal": witness.filter_ordinal,
                    "column_id": witness.column_id,
                    "col_type": witness.col_type,
                    "project_id": witness.project_id,
                    "trace_id": witness.trace_id,
                    "span_id": witness.span_id,
                    "start_time": witness.start_time.isoformat(),
                }
            ]
        }
    }


def test_d09_waiting_trace_entry_keeps_latest_witness(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """Witness A is replicated; a pass rewrites it to B on the primary, and the
    next pass proves A again. Compared against the replica's stale copy (A)
    the refresh looks like a no-op, leaving the primary on B."""

    config = make_config(project=project, template=template, name="d09")
    root = _span(project, trace, name="root")
    child_a = _span(project, trace, name="child a")
    child_b = _span(project, trace, name="child b")
    task = _task(project, [config], row_type=RowType.TRACES)
    entry = _entry(
        task,
        root,
        config,
        status=EvalEntryStatus.PENDING,
        target_type=EvalTargetType.TRACE,
        config_hash=resolved_config_hash(config),
        output_metadata=_witness_metadata(task, child_a),
    )
    lagging_replica.freeze()
    _stub_span_reader(monkeypatch, [root])

    materialize_pending(
        task, [str(trace.id)], trace_filter_witnesses=[_witness(task, child_b)]
    )
    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert stored.output_metadata == _witness_metadata(task, child_b)

    materialize_pending(
        task, [str(trace.id)], trace_filter_witnesses=[_witness(task, child_a)]
    )
    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert stored.output_metadata == _witness_metadata(task, child_a)
    assert _primary_entries(task).count() == 1


# -- D12: the reconcile pass's live count -------------------------------------


def test_d12_reconcile_counts_entries_it_inserts_and_stamps_last_run(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """The count runs inside the reconcile transaction. The replica cannot see
    the rows that transaction inserted even with zero lag, so a routed count
    reports nothing created and the list's "Last Run" never moves."""

    config = make_config(project=project, template=template, name="d12")
    span = _span(project, trace)
    task = _task(project, [config])
    lagging_replica.freeze()
    _stub_span_reader(monkeypatch, [span])
    _stub_proof(
        monkeypatch,
        ResolvedRowSet((str(span.id),), (str(span.id),), False),
    )

    result = reconcile(task)

    assert result.created == 1
    assert _primary_entries(task).count() == 1
    assert EvalTask.objects.using("default").get(id=task.id).last_run is not None


def test_d12_full_proof_drops_pending_entry_the_replica_has_not_applied(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """A pass moments ago materialized a pending entry; the task then stops
    selecting that row. A zero count from the replica takes the "first pass"
    branch and never runs the drop, so the stale entry is evaluated (billed)."""

    config = make_config(project=project, template=template, name="d12-drop")
    span = _span(project, trace)
    task = _task(project, [config])
    lagging_replica.freeze()
    entry = _entry(
        task,
        span,
        config,
        status=EvalEntryStatus.PENDING,
        config_hash=resolved_config_hash(config),
    )
    _stub_span_reader(monkeypatch, [span])
    _stub_proof(monkeypatch, ResolvedRowSet((), (), True))

    result = reconcile(task)

    assert result.dropped == 1
    assert EvalLogger.all_objects.using("default").get(id=entry.id).deleted is True


# -- D13: requeue reads the entries' current state ---------------------------


def test_d13_full_proof_requeues_entry_completed_after_freeze(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """The config changed (hash H1) while an entry was still running on H0.
    The worker completes it on H0 after the freeze; the one full proof for the
    new revision must requeue it. Read from the replica it is still RUNNING, so
    it is skipped, the revision is stamped, and later delta passes never
    revisit it: the stale H0 result stands permanently."""

    config = make_config(project=project, template=template, name="d13")
    span = _span(project, trace)
    task = _task(project, [config], reclassified_evals_revision="before-edit")
    entry = _entry(
        task,
        span,
        config,
        status=EvalEntryStatus.RUNNING,
        config_hash=_STALE_HASH,
    )
    lagging_replica.freeze()
    EvalLogger.objects.using("default").filter(id=entry.id).update(
        status=EvalEntryStatus.COMPLETED
    )
    _stub_span_reader(monkeypatch, [span])
    seen_cursors = _stub_proof(
        monkeypatch,
        ResolvedRowSet((str(span.id),), (str(span.id),), True),
        ResolvedRowSet((), (), False),
    )

    first = reconcile(task)

    assert seen_cursors == [None]  # the revision's cursor-less full proof
    assert first.requeued == 1
    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert (stored.status, stored.config_hash) == (
        EvalEntryStatus.PENDING,
        resolved_config_hash(config),
    )

    lagging_replica.thaw()
    reconcile(task)  # the next poll is an ordinary delta

    assert seen_cursors[1] is not None
    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert (stored.status, stored.config_hash) == (
        EvalEntryStatus.PENDING,
        resolved_config_hash(config),
    )


# -- E01: the eval set and configs the worker reads ---------------------------
#
# A task's eval set (the ``task.evals`` links) and its configs are written by
# the create / edit request a moment before the workflow's first activity
# reads them, so every test here writes them after the freeze.


@pytest.mark.parametrize(
    "configs_after_freeze", [False, True], ids=["saved-configs", "new-configs"]
)
def test_e01_reconcile_activity_materializes_task_created_after_freeze(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
    configs_after_freeze,
):
    """A historical task is reconciled once, right after the request that
    created it. A replica that has not applied the task's eval links returns
    no evals: nothing is materialized, nothing is claimed, and the drain marks
    the task completed with no results and no error."""

    from tfc.temporal.eval_tasks.activities import (
        _claim_batch_sync,
        _finalize_task_sync,
        _reconcile_sync,
    )

    def new_configs():
        return [
            make_config(project=project, template=template, name=f"e01-{name}")
            for name in ("a", "b")
        ]

    span = _span(project, trace)
    configs = [] if configs_after_freeze else new_configs()
    lagging_replica.freeze()
    if configs_after_freeze:
        configs = new_configs()
    task = _task(project, configs, run_type=RunType.HISTORICAL)
    _stub_span_reader(monkeypatch, [span])
    _stub_proof(monkeypatch, ResolvedRowSet((str(span.id),), (str(span.id),), True))

    result = _reconcile_sync(str(task.id))

    assert result["created"] == 2
    assert routers._force_primary_depth.get() == 0
    assert sorted(
        (e.observation_span_id, e.custom_eval_config_id) for e in _primary_entries(task)
    ) == sorted((span.id, config.id) for config in configs)
    assert len(_claim_batch_sync(str(task.id), 10)["entry_ids"]) == 2
    assert _finalize_task_sync(str(task.id))["finalized"] is False
    assert (
        EvalTask.objects.using("default").get(id=task.id).status
        == EvalTaskStatus.RUNNING
    )


def test_e01_reconcile_after_edit_runs_the_new_eval_set(
    lagging_replica,  # noqa: F811
    monkeypatch,
    project,
    trace,
    template,
):
    """Edit & rerun swaps eval A for eval B just before the worker reconciles.
    From a replica still holding A, the pass keeps A's pending entry (the eval
    the user removed runs and is billed) and never creates B's."""

    config_a = make_config(project=project, template=template, name="e01-a")
    config_b = make_config(project=project, template=template, name="e01-b")
    span = _span(project, trace)
    task = _task(project, [config_a], run_type=RunType.HISTORICAL)
    entry_a = _entry(
        task,
        span,
        config_a,
        status=EvalEntryStatus.PENDING,
        config_hash=resolved_config_hash(config_a),
    )
    lagging_replica.freeze()
    task.evals.set([config_b])
    _stub_span_reader(monkeypatch, [span])
    _stub_proof(monkeypatch, ResolvedRowSet((str(span.id),), (str(span.id),), True))

    result = reconcile(task)

    assert (result.created, result.dropped) == (1, 1)
    assert routers._force_primary_depth.get() == 0
    assert [
        (e.observation_span_id, e.custom_eval_config_id) for e in _primary_entries(task)
    ] == [(span.id, config_b.id)]
    assert EvalLogger.all_objects.using("default").get(id=entry_a.id).deleted is True


def test_e01_workflow_labels_count_evals_linked_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    template,
):
    from tfc.temporal.eval_tasks.activities import _get_workflow_labels_sync

    configs = [
        make_config(project=project, template=template, name=f"e01-{name}")
        for name in ("a", "b")
    ]
    lagging_replica.freeze()
    task = _task(project, configs, run_type=RunType.HISTORICAL)

    labels = _get_workflow_labels_sync(str(task.id))

    assert labels["config_summary"].endswith("evals=2")


def test_e01_instant_sizing_reads_configs_linked_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    template,
):
    """Instant eval creates the question's config and task in one request and
    starts the workflow when it commits. Read from a replica that has not
    applied them, the task has no evals and the run is sized as a regular
    task, a few rows at a time, while a person waits for the answers."""

    from tfc.temporal.eval_tasks.client import (
        INSTANT_BATCH_SIZE,
        INSTANT_HEARTBEAT_S,
        INSTANT_MAX_CONCURRENT,
        _instant_sizing,
    )

    lagging_replica.freeze()
    config = CustomEvalConfig.objects.create(
        name="e01-instant",
        project=project,
        eval_template=template,
        config={"instant_eval": True},
        mapping={},
        filters={},
    )
    task = _task(project, [config], run_type=RunType.HISTORICAL, spans_limit=500)

    assert _instant_sizing(task) == {
        "batch_size": INSTANT_BATCH_SIZE,
        "max_concurrent": INSTANT_MAX_CONCURRENT,
        "heartbeat_s": INSTANT_HEARTBEAT_S,
    }


def test_e01_run_entry_runs_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    observation_span,
    eval_template,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """An entry whose config was created moments ago (an Instant eval
    question, a new eval added to a task) must run, not fail as missing."""

    from tracer.services.eval_tasks.run_entry import run_entry

    lagging_replica.freeze()
    config = CustomEvalConfig.objects.create(
        name="e01-new",
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    # In bulk, as materialize_pending writes entries: a single-row save runs
    # EvalLogger.full_clean, whose config lookup is not what this test is about.
    (entry,) = EvalLogger.objects.bulk_create(
        [
            EvalLogger(
                target_type=EvalTargetType.SPAN,
                observation_span=observation_span,
                trace=observation_span.trace,
                custom_eval_config=config,
                eval_task_id=str(eval_task.id),
                status=EvalEntryStatus.RUNNING,
            )
        ]
    )

    assert run_entry(entry) == EvalEntryStatus.COMPLETED

    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert (stored.status, stored.config_hash) == (
        EvalEntryStatus.COMPLETED,
        resolved_config_hash(config),
    )
    assert [request.runtime_config for request in stub_run_eval.requests] == [
        {"threshold": 0.8}
    ]


def test_e01_run_entry_runs_and_stamps_config_edited_after_freeze(
    lagging_replica,  # noqa: F811
    observation_span,
    custom_eval_config,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """The user edits a config and reruns. The run must evaluate the edited
    config and stamp its hash. The old config stamped with the old hash is
    re-run later on a continuous task; the old config stamped with the new
    hash would never be."""

    from tracer.services.eval_tasks.run_entry import run_entry

    entry = _entry(
        eval_task,
        observation_span,
        custom_eval_config,
        status=EvalEntryStatus.RUNNING,
    )
    lagging_replica.freeze()
    CustomEvalConfig.objects.using("default").filter(id=custom_eval_config.id).update(
        config={"threshold": 0.3}
    )
    edited = CustomEvalConfig.objects.using("default").get(id=custom_eval_config.id)

    assert run_entry(entry) == EvalEntryStatus.COMPLETED

    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert stored.config_hash == resolved_config_hash(edited)
    assert [request.runtime_config for request in stub_run_eval.requests] == [
        {"threshold": 0.3}
    ]


def _localized_entry(project, span, eval_template, eval_task):
    """A RUNNING entry, written in bulk as materialize_pending writes it, whose
    config has the error localizer on."""
    config = CustomEvalConfig.objects.create(
        name="e01-localized",
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
        error_localizer=True,
    )
    (entry,) = EvalLogger.objects.bulk_create(
        [
            EvalLogger(
                target_type=EvalTargetType.SPAN,
                observation_span=span,
                trace=span.trace,
                custom_eval_config=config,
                eval_task_id=str(eval_task.id),
                status=EvalEntryStatus.RUNNING,
            )
        ]
    )
    return entry


def _localizer_tasks(entry):
    return ErrorLocalizerTask.no_workspace_objects.filter(source_id=entry.id)


def test_e01_run_entry_localizes_result_of_entry_written_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    observation_span,
    eval_template,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """The engine hands the entry it just wrote its result onto to the error
    localizer. Re-read from a replica that has not applied the entry, that is
    nothing: the localizer logs an error and no task is ever created."""

    from tracer.services.eval_tasks.run_entry import run_entry

    lagging_replica.freeze()
    entry = _localized_entry(project, observation_span, eval_template, eval_task)

    assert run_entry(entry) == EvalEntryStatus.COMPLETED

    assert [task.organization_id for task in _localizer_tasks(entry)] == [
        project.organization_id
    ]


def test_e01_run_entry_localizes_result_for_span_ingested_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    trace,
    eval_template,
    eval_task,
    stub_run_eval,
    stub_cost_log,
):
    """A span ingested moments ago is evaluated straight away. The localizer
    reads the result's span to find its project: from a replica that has not
    applied the span, that read fails and no task is created."""

    from tracer.services.eval_tasks.run_entry import run_entry

    lagging_replica.freeze()
    # A second batch of the trace, ingested after the replica froze.
    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="replica span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi there"}}]},
    )
    seed_ch_span(span)
    entry = _localized_entry(project, span, eval_template, eval_task)

    assert run_entry(entry) == EvalEntryStatus.COMPLETED

    assert [task.organization_id for task in _localizer_tasks(entry)] == [
        project.organization_id
    ]


@pytest.mark.parametrize("activity", ["_fail_entry_sync", "_requeue_lost_entry_sync"])
def test_e01_lost_run_stamps_hash_of_config_edited_after_freeze(
    lagging_replica,  # noqa: F811
    project,
    trace,
    template,
    activity,
):
    """An entry errored after its run was lost carries the hash of the config
    run_entry read, the primary's, as without a replica."""

    from tfc.temporal.eval_tasks import activities

    config = make_config(project=project, template=template, name="e01-lost")
    span = _span(project, trace)
    task = _task(project, [config], run_type=RunType.HISTORICAL)
    entry = _entry(
        task,
        span,
        config,
        status=EvalEntryStatus.RUNNING,
        attempts=1,
        config_hash=resolved_config_hash(config),
    )
    lagging_replica.freeze()
    CustomEvalConfig.objects.using("default").filter(id=config.id).update(
        mapping={"input": "edited"}
    )
    edited = CustomEvalConfig.objects.using("default").get(id=config.id)

    result = getattr(activities, activity)(str(entry.id))

    assert result["status"] == str(EvalEntryStatus.ERRORED)
    stored = EvalLogger.objects.using("default").get(id=entry.id)
    assert (stored.status, stored.config_hash) == (
        EvalEntryStatus.ERRORED,
        resolved_config_hash(edited),
    )
