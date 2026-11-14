"""Green scenario matrix cells (S1–S3, S8–S9, S11, S13, S15): parity and
cheap-path pins against current engine code. Every expected count comes from
the loadgen manifest or a direct CH probe — never hardcoded.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.stress import ch_asserts
from tests.stress.budgets import (
    DESIRED_STREAM_MAX_MEMORY,
    DESIRED_STREAM_MAX_READ_ROWS_FACTOR,
    REAP_PROGRESS_FINALIZE_MAX_PG_QUERIES,
)
from tests.stress.conftest import SEED_WINDOW
from tracer.models.eval_task import RowType, RunType
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    ObservationType,
)
from tracer.selectors.eval_tasks.progress import count_by_status, has_undrained_work
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.services.eval_tasks.entries import claim_pending_batch
from tracer.services.eval_tasks.reaper import reap_stale_running
from tracer.services.eval_tasks.reconciler import reconcile
from tracer.services.eval_tasks.run_entry import run_entry

pytestmark = pytest.mark.stress

_TERMINAL = {
    EvalEntryStatus.COMPLETED,
    EvalEntryStatus.ERRORED,
    EvalEntryStatus.SKIPPED,
}

# Per-test continuous projects (S8/S9 seed their own increments; the four
# shared stress_dataset projects stay read-only).
S8_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000008"
S9_PROJECT_ID = "5712e5ac-0000-4000-8000-000000000009"


def _live(task, **f):
    return EvalLogger.objects.filter(eval_task_id=str(task.id), **f)


def _drain(task, batch_size: int = 100) -> list[str]:
    statuses = []
    while True:
        batch = claim_pending_batch(task, batch_size)
        if not batch:
            return statuses
        statuses.extend(run_entry(entry) for entry in batch)


def _window():
    return (
        datetime.fromisoformat(SEED_WINDOW[0].replace("Z", "+00:00")),
        datetime.fromisoformat(SEED_WINDOW[1].replace("Z", "+00:00")),
    )


def _probe_ids(sql: str, params: dict) -> set[str]:
    client = ch_asserts._client()
    try:
        return {r[0] for r in client.query(sql, parameters=params).result_rows}
    finally:
        client.close()


@pytest.mark.django_db
def test_s1_historical_spans_full_drain(
    stress_dataset, eval_task_factory, stub_run_eval, stub_cost_log
):
    m = stress_dataset.target
    limit = 200
    assert m.span_count > limit
    task = eval_task_factory(m.project_id, RowType.SPANS, spans_limit=limit)

    result = reconcile(task)
    assert result.created == limit

    statuses = _drain(task)
    assert len(statuses) == limit
    assert set(statuses) <= _TERMINAL
    assert has_undrained_work(task) is False
    counts = count_by_status(task)
    assert counts.get(EvalEntryStatus.COMPLETED, 0) == limit  # stubbed engine passes

    assert {str(t) for t in _live(task).values_list("trace_id", flat=True)} <= set(
        m.trace_ids
    )


@pytest.mark.django_db
def test_s2_selective_filter_matches_manifest(stress_dataset, eval_task_factory):
    m = stress_dataset.target
    expected = m.observation_type_counts[ObservationType.LLM]
    assert 0 < expected < m.span_count
    task = eval_task_factory(
        m.project_id,
        RowType.SPANS,
        filters={
            "observation_type": [ObservationType.LLM],
            "date_range": list(SEED_WINDOW),
        },
    )
    result = reconcile(task)
    assert result.created == expected


@pytest.mark.django_db
def test_s3_sampling_matches_cityhash_subset(stress_dataset, eval_task_factory):
    m = stress_dataset.target
    limit = 2000
    task = eval_task_factory(
        m.project_id, RowType.SPANS, sampling_rate=50.0, spans_limit=limit
    )
    reconcile(task)

    start, end = _window()
    # Same hash+order+limit the resolver applies; cityHash64 evaluated by CH,
    # never reimplemented in Python.
    expected = _probe_ids(
        "SELECT id FROM spans FINAL "
        "WHERE project_id = %(p)s AND is_deleted = 0 "
        "AND start_time >= %(s)s AND start_time < %(e)s "
        "AND modulo(cityHash64(%(salt)s, toString(id)), 100) < %(rate)s "
        "ORDER BY id LIMIT %(lim)s",
        {
            "p": m.project_id,
            "s": start,
            "e": end,
            "salt": str(task.id),
            "rate": 50.0,
            "lim": limit,
        },
    )
    materialized = set(_live(task).values_list("observation_span_id", flat=True))
    assert materialized == expected


@pytest.mark.django_db
def test_s8_continuous_spans_incremental_arrival(
    stress_dataset, eval_task_factory, loadgen_run
):
    # Pre-start history (back-dated default --end anchor): never backfilled.
    loadgen_run(
        S8_PROJECT_ID, traces=30, spans_per_trace=4, sessions=4, shape="llm", seed=460
    )
    task = eval_task_factory(S8_PROJECT_ID, RowType.SPANS, run_type=RunType.CONTINUOUS)
    # Back-date the start so the cursor has park room (a task younger than the
    # reconciler's 5-min overlap clamps to its start floor and can't advance).
    from tracer.models.eval_task import EvalTask

    EvalTask.objects.filter(id=task.id).update(
        start_time=timezone.now() - timedelta(hours=1)
    )
    task.refresh_from_db()

    r1 = reconcile(task)
    assert r1.created == 0
    task.refresh_from_db()
    cursor1 = task.continuous_cursor
    assert cursor1 is not None

    time.sleep(2)
    inc = loadgen_run(
        S8_PROJECT_ID,
        traces=5,
        spans_per_trace=3,
        sessions=2,
        shape="llm",
        seed=461,
        end=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        time_range="1s",
        trickle=100,
    )
    # Child spans start up to (spans_per_trace-1)s after --end; wait for the
    # resolver's start_time < now() upper bound to pass them.
    time.sleep(3)

    r2 = reconcile(task)
    assert r2.created == inc.span_count
    assert {str(t) for t in _live(task).values_list("trace_id", flat=True)} == set(
        inc.trace_ids
    )
    task.refresh_from_db()
    assert task.continuous_cursor > cursor1


@pytest.mark.django_db
def test_s9_continuous_traces_root_resolution(
    stress_dataset, eval_task_factory, loadgen_run
):
    # Traces window their continuous floor on the ROOT's created_at (arrival),
    # not start_time — a back-dated pre-seed wave would legitimately be in
    # scope. So the "only new rows" pin here is a second arrival wave, not a
    # pre-start contrast (that's S8's, where spans floor on start_time).
    task = eval_task_factory(S9_PROJECT_ID, RowType.TRACES, run_type=RunType.CONTINUOUS)
    from tracer.models.eval_task import EvalTask

    EvalTask.objects.filter(id=task.id).update(
        start_time=timezone.now() - timedelta(hours=1)
    )
    task.refresh_from_db()

    r1 = reconcile(task)
    assert r1.created == 0  # nothing arrived yet
    task.refresh_from_db()
    cursor1 = task.continuous_cursor

    time.sleep(2)
    inc1 = loadgen_run(
        S9_PROJECT_ID,
        traces=5,
        spans_per_trace=3,
        sessions=2,
        shape="llm",
        seed=471,
        end=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        time_range="1s",
        trickle=100,
    )
    time.sleep(3)

    r2 = reconcile(task)
    assert r2.created == len(inc1.trace_ids)
    task.refresh_from_db()
    assert task.continuous_cursor > cursor1

    time.sleep(2)
    inc2 = loadgen_run(
        S9_PROJECT_ID,
        traces=5,
        spans_per_trace=3,
        sessions=2,
        shape="llm",
        seed=472,
        end=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        time_range="1s",
        trickle=100,
    )
    time.sleep(3)

    r3 = reconcile(task)
    assert r3.created == len(inc2.trace_ids)  # first wave not re-created

    # Root parity: each entry anchors on its manifest's root span.
    roots = {**inc1.root_span_id_by_trace, **inc2.root_span_id_by_trace}
    entries = list(_live(task))
    assert len(entries) == len(roots)
    for entry in entries:
        assert entry.observation_span_id == roots[str(entry.trace_id)]


@pytest.mark.django_db
def test_s11_narrow_filter_drops_pending_keeps_completed(
    stress_dataset, eval_task_factory
):
    m = stress_dataset.target
    task = eval_task_factory(m.project_id, RowType.SPANS, spans_limit=400)
    reconcile(task)
    assert _live(task).count() == 400

    completed_ids = list(_live(task).values_list("id", flat=True)[:100])
    EvalLogger.objects.filter(id__in=completed_ids).update(
        status=EvalEntryStatus.COMPLETED
    )

    task.filters = {
        "observation_type": [ObservationType.LLM],
        "date_range": list(SEED_WINDOW),
    }
    task.save()
    result = reconcile(task)

    assert result.dropped > 0
    # Paid data kept: no completed entry was soft-deleted.
    assert (
        EvalLogger.all_objects.filter(
            eval_task_id=str(task.id),
            status=EvalEntryStatus.COMPLETED,
            deleted=True,
        ).count()
        == 0
    )
    assert _live(task, status=EvalEntryStatus.COMPLETED).count() == 100
    # Surviving pending entries all reference in-filter (llm) spans.
    llm_ids = _probe_ids(
        "SELECT id FROM spans FINAL WHERE project_id = %(p)s "
        "AND is_deleted = 0 AND observation_type = %(ot)s",
        {"p": m.project_id, "ot": str(ObservationType.LLM)},
    )
    pending = set(
        _live(task, status=EvalEntryStatus.PENDING).values_list(
            "observation_span_id", flat=True
        )
    )
    assert pending <= llm_ids


@pytest.mark.django_db
def test_s13_desired_stream_budget_on_fat_project(stress_dataset, eval_task_factory):
    m = stress_dataset.noise  # mixed: carries the fat-attrs + voice traces
    task = eval_task_factory(m.project_id, RowType.SPANS)
    with ch_asserts.ch_query_budget("stress:S13:desired-stream") as b:
        streamed = sum(len(batch) for batch in iter_desired_rows(task))
    assert streamed == m.span_count
    assert b.total("read_rows") <= m.span_count * DESIRED_STREAM_MAX_READ_ROWS_FACTOR
    assert b.max("memory_usage") <= DESIRED_STREAM_MAX_MEMORY


@pytest.mark.django_db(transaction=True)
def test_s15_reap_progress_finalize_statement_floor(stress_dataset, eval_task_factory):
    # transaction=True: finalize's close_old_connections would kill the
    # default test-transaction connection.
    from tfc.temporal.eval_tasks.activities import _finalize_task_sync

    m = stress_dataset.target
    task = eval_task_factory(m.project_id, RowType.SPANS, spans_limit=20)
    reconcile(task)
    claimed = claim_pending_batch(task, 20)
    assert len(claimed) == 20
    EvalLogger.objects.filter(eval_task_id=str(task.id)).update(
        updated_at=timezone.now() - timedelta(hours=1)
    )

    with CaptureQueriesContext(connection) as ctx:
        requeued, failed = reap_stale_running(
            task, older_than_seconds=600, max_attempts=3
        )
        undrained = has_undrained_work(task)
        finalize = _finalize_task_sync(str(task.id))

    assert (requeued, failed) == (20, 0)
    assert undrained is True
    assert finalize["finalized"] is False  # still pending work
    assert len(ctx.captured_queries) <= REAP_PROGRESS_FINALIZE_MAX_PG_QUERIES
