"""The reconciler — one idempotent engine that makes a task's live entries
match its desired state. Covers create, add/remove eval, config edit, and
scope change; running it twice is a no-op.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from itertools import chain

from django.db.models import Case, CharField, Value, When
from django.utils import timezone

from tracer.models.eval_task import EvalTask, RowType, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import materialize_pending

# How far behind "now" the continuous cursor is parked after each pass: the
# window each reconcile re-scans to catch rows whose CH arrival lagged their
# created_at. The unique index makes the re-scan free of duplicates; this only
# needs to exceed normal ingestion lag (pause/downtime gaps are covered by the
# persisted cursor, not this overlap).
_CONTINUOUS_CURSOR_OVERLAP = timedelta(minutes=5)

# Max entry ids per requeue UPDATE — bounds the WHERE id IN (...) list size.
_REQUEUE_CHUNK = 10_000


@dataclass
class ReconcileResult:
    created: int = 0
    requeued: int = 0
    dropped: int = 0


def reconcile(task: EvalTask) -> ReconcileResult:
    """Make the task's live entries match its desired config + row set.

    Creates missing pending entries (streamed), re-queues stale / errored /
    skipped in-scope entries, and drops out-of-scope *pending* entries while
    keeping out-of-scope *completed* results (paid data). For continuous tasks,
    advances the forward cursor so the next pass scans only the new tail.
    """
    before = _live_count(task)
    materialize_pending(task)
    created = _live_count(task) - before
    if before == 0:
        # Pure create — nothing pre-existing to re-queue or drop.
        result = ReconcileResult(created=created)
    else:
        requeued, dropped = _requeue_and_drop(task)
        result = ReconcileResult(created=created, requeued=requeued, dropped=dropped)
    _advance_continuous_cursor(task)
    return result


def _advance_continuous_cursor(task: EvalTask) -> None:
    """Park the continuous task's forward watermark just behind now().

    Only ever moves forward, and never before the task's start floor — parking
    at ``now() - overlap`` unclamped would, for a task younger than the overlap,
    pull the floor back before its start and re-admit pre-start history. Advanced
    after materialize/requeue so both read the same floor within a pass; the next
    pass then floors its desired set here instead of re-scanning the whole
    history.
    """
    if task.run_type != RunType.CONTINUOUS:
        return
    start_floor = task.start_time or task.created_at
    parked = timezone.now() - _CONTINUOUS_CURSOR_OVERLAP
    if start_floor is not None and parked < start_floor:
        parked = start_floor
    if task.continuous_cursor is not None and parked <= task.continuous_cursor:
        return
    task.continuous_cursor = parked
    EvalTask.objects.filter(id=task.id).update(continuous_cursor=parked)


def _live_count(task: EvalTask) -> int:
    return EvalLogger.objects.filter(eval_task_id=str(task.id)).count()


def _requeue_and_drop(task: EvalTask) -> tuple[int, int]:
    hashes = {cfg.id: resolved_config_hash(cfg) for cfg in task.evals.all()}
    current_eval_ids = set(hashes)
    desired: set[str] = set()
    for batch in iter_desired_rows(task):
        desired.update(batch)

    requeue_by_cfg: dict[object, list] = defaultdict(list)
    drop_ids: list = []
    # Stream the live entries — we only collect ids, never hold all objects.
    for entry in EvalLogger.objects.filter(eval_task_id=str(task.id)).iterator():
        cfg_id = entry.custom_eval_config_id
        in_scope = (
            _entry_identity(entry, task.row_type) in desired
            and cfg_id in current_eval_ids
        )
        if in_scope:
            if entry.status == EvalEntryStatus.COMPLETED:
                # Empty config_hash = a legacy row not yet baseline-stamped;
                # treat as not-stale so a reconcile mid-backfill can't re-run
                # all history.
                if entry.config_hash and entry.config_hash != hashes[cfg_id]:
                    requeue_by_cfg[cfg_id].append(entry.id)  # stale result
            elif entry.status in (EvalEntryStatus.ERRORED, EvalEntryStatus.SKIPPED):
                requeue_by_cfg[cfg_id].append(entry.id)
            # PENDING / RUNNING in-scope: leave as-is.
        elif entry.status == EvalEntryStatus.PENDING:
            drop_ids.append(entry.id)  # out of scope, no result yet

    requeued = 0
    # Flatten {cfg_id: [entry_id, ...]} into one flat [entry_id, ...] list.
    all_ids = list(chain.from_iterable(requeue_by_cfg.values()))
    if all_ids:
        hash_case = Case(
            *[
                When(custom_eval_config_id=cfg_id, then=Value(hashes[cfg_id]))
                for cfg_id in requeue_by_cfg
            ],
            output_field=CharField(),
        )
        for chunk_start in range(0, len(all_ids), _REQUEUE_CHUNK):
            chunk = all_ids[chunk_start : chunk_start + _REQUEUE_CHUNK]
            requeued += EvalLogger.objects.filter(id__in=chunk).update(
                status=EvalEntryStatus.PENDING,
                config_hash=hash_case,
                error=False,
                skipped_reason=None,
            )
    dropped = 0
    if drop_ids:
        dropped = EvalLogger.objects.filter(id__in=drop_ids).update(
            deleted=True, deleted_at=timezone.now()
        )
    return requeued, dropped


def _entry_identity(entry: EvalLogger, row_type: str) -> str:
    if row_type in (RowType.SPANS, RowType.VOICE_CALLS):
        return entry.observation_span_id
    if row_type == RowType.TRACES:
        return str(entry.trace_id)
    if row_type == RowType.SESSIONS:
        return str(entry.trace_session_id)
    raise ValueError(f"Unsupported row_type: {row_type!r}")
