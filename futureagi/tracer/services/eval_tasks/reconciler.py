"""The reconciler — one idempotent engine that makes a task's live entries
match its desired state. Covers create, add/remove eval, config edit, and
scope change; running it twice is a no-op.
"""

from __future__ import annotations

import json
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from itertools import chain

from django.db import transaction
from django.db.models import Case, CharField, Value, When
from django.utils import timezone

from tracer.models.eval_task import EvalTask, RowType, RunType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.selectors.eval_tasks.row_resolver import (
    ResolvedRowSet,
    resolve_desired_rows,
)
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.cursor_policy import CONTINUOUS_CURSOR_OVERLAP
from tracer.services.eval_tasks.entries import materialize_pending

# How far behind "now" the continuous cursor is parked after each pass: the
# window each reconcile re-scans to catch late CH arrivals and version changes.
# The unique index makes the re-scan free of duplicates; this only
# needs to exceed normal ingestion lag (pause/downtime gaps are covered by the
# persisted cursor, not this overlap).
_CONTINUOUS_CURSOR_OVERLAP = CONTINUOUS_CURSOR_OVERLAP

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
    if isinstance(task, EvalTask):
        # Callers may retain the model instance across passes while the previous
        # pass advances ``continuous_cursor`` with a queryset UPDATE.  Resolve
        # from a fresh database snapshot so that ordinary sequential passes do
        # not look like concurrent edits to the revision fence.
        task = EvalTask.objects.get(id=task.id)

    now = timezone.now()
    force_full = _continuous_requires_full_reclassification(task)
    resolver_task = task
    if force_full:
        # Do not mutate the DB watermark before the proof succeeds. A shallow
        # model copy is enough to make the resolver start at task.start_time and
        # mark this one result as FULL.
        resolver_task = copy(task)
        resolver_task.continuous_cursor = None
    revision = _task_revision(task)
    resolved = resolve_desired_rows(resolver_task, ceiling=now)

    if not isinstance(task, EvalTask):
        # Narrow unit-test compatibility; production always receives EvalTask.
        return _apply_resolved(task, resolved=resolved, now=now)

    with transaction.atomic():
        locked = EvalTask.objects.select_for_update().get(id=task.id)
        # Lock configs while materialization stamps their hashes. This makes an
        # independent config edit wait for this transaction instead of landing
        # midway through a reconcile pass.
        list(locked.evals.select_for_update().all())
        if _task_revision(locked) != revision:
            raise EvalTaskRevisionChanged(
                "Evaluation task changed while its row set was being resolved."
            )
        return _apply_resolved(locked, resolved=resolved, now=now)


class EvalTaskRevisionChanged(RuntimeError):
    """A concurrent task/config edit invalidated a buffered CH proof."""


def _apply_resolved(
    task: EvalTask,
    *,
    resolved: ResolvedRowSet,
    now: datetime,
) -> ReconcileResult:
    before = _live_count(task)
    if resolved.trace_filter_witnesses:
        materialize_pending(
            task,
            resolved.matched_ids,
            trace_filter_witnesses=resolved.trace_filter_witnesses,
        )
    else:
        materialize_pending(task, resolved.matched_ids)
    created = _live_count(task) - before
    if before == 0:
        result = ReconcileResult(created=created)
    else:
        requeued, dropped = _requeue_and_drop(task, resolved=resolved)
        result = ReconcileResult(created=created, requeued=requeued, dropped=dropped)
    # Candidate overflow catch-up may prove only a strict prefix of the frozen
    # arrival window. Advance from that exact proof ceiling, in this same DB
    # transaction, never from the later wall-clock ceiling the pass requested.
    _advance_continuous_cursor(task, resolved.covered_through or now)
    return result


def _advance_continuous_cursor(task: EvalTask, now: datetime) -> None:
    """Park the continuous task's forward watermark at ``now - overlap``.

    ``now`` is frozen at the start of the reconcile pass (not read here) so the
    watermark tracks what the scan actually covered, never jumping past rows that
    arrived during a slow materialize. Only ever moves forward, and never before
    the task's start floor — parking at ``now - overlap`` unclamped would, for a
    task younger than the overlap, pull the floor before its start and re-admit
    pre-start history.
    """
    if task.run_type != RunType.CONTINUOUS:
        return
    start_floor = task.start_time or task.created_at
    parked = now - _CONTINUOUS_CURSOR_OVERLAP
    if start_floor is not None and parked < start_floor:
        parked = start_floor
    if task.continuous_cursor is not None and parked <= task.continuous_cursor:
        return
    task.continuous_cursor = parked
    EvalTask.objects.filter(id=task.id).update(continuous_cursor=parked)


def _live_count(task: EvalTask) -> int:
    return EvalLogger.objects.filter(eval_task_id=str(task.id)).count()


def _requeue_and_drop(
    task: EvalTask,
    *,
    resolved: ResolvedRowSet,
) -> tuple[int, int]:
    hashes = {cfg.id: resolved_config_hash(cfg) for cfg in task.evals.all()}
    current_eval_ids = set(hashes)
    desired = set(resolved.matched_ids)
    candidates = set(resolved.candidate_ids)

    requeue_by_cfg: dict[object, list] = defaultdict(list)
    drop_ids: list = []
    full_state = resolved.full_state
    # Stream the live entries — we only collect ids, never hold all objects.
    for entry in EvalLogger.objects.filter(eval_task_id=str(task.id)).iterator():
        cfg_id = entry.custom_eval_config_id
        current_eval = cfg_id in current_eval_ids
        identity = _entry_identity(entry, task.row_type)
        in_desired_read = identity in desired
        affected = full_state or identity in candidates

        if (
            current_eval
            and in_desired_read
            and entry.status == EvalEntryStatus.COMPLETED
        ):
            # Empty config_hash = a legacy row not yet baseline-stamped;
            # treat as not-stale so a reconcile mid-backfill can't re-run
            # all history. A cursor-less continuous reconcile is a full-state
            # pass; a normal delta must not requeue an old entity that may no
            # longer satisfy the current task filters.
            if entry.config_hash and entry.config_hash != hashes[cfg_id]:
                requeue_by_cfg[cfg_id].append(entry.id)
        elif (
            current_eval
            and in_desired_read
            and entry.status in (EvalEntryStatus.ERRORED, EvalEntryStatus.SKIPPED)
        ):
            requeue_by_cfg[cfg_id].append(entry.id)
        elif (
            current_eval
            and in_desired_read
            and entry.status == EvalEntryStatus.PENDING
            and entry.config_hash != hashes[cfg_id]
        ):
            requeue_by_cfg[cfg_id].append(entry.id)
        elif entry.status == EvalEntryStatus.PENDING and (
            not current_eval or (affected and not in_desired_read)
        ):
            # A continuous desired read is only the latest arrival delta. Its
            # absence is not proof that older pending work left task scope.
            # A cursor-less continuous reconcile is a full-state pass;
            # removed eval configs are PG-known and remain safe to drop in
            # either mode.
            drop_ids.append(entry.id)

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


def _continuous_requires_full_reclassification(task: EvalTask) -> bool:
    """Detect config/eval-set changes even when edited outside task views."""

    if not isinstance(task, EvalTask):
        return False
    if task.run_type != RunType.CONTINUOUS or task.continuous_cursor is None:
        return False
    evals = list(task.evals.all())
    if not evals:
        return False
    task_entries = EvalLogger.objects.filter(eval_task_id=str(task.id))
    if task_entries.exists() and any(
        not task_entries.filter(custom_eval_config_id=cfg.id).exists() for cfg in evals
    ):
        return True
    for cfg in evals:
        expected_hash = resolved_config_hash(cfg)
        if (
            task_entries.filter(custom_eval_config_id=cfg.id)
            .exclude(config_hash__in=("", expected_hash))
            .exists()
        ):
            return True
    return False


def _task_revision(task: EvalTask) -> str:
    """Stable task selection/config snapshot used to fence writes."""

    if not isinstance(task, EvalTask):
        return "unit-test-task"
    eval_revisions = sorted(
        (str(cfg.id), resolved_config_hash(cfg)) for cfg in task.evals.all()
    )
    payload = {
        "id": str(task.id),
        "filters": task.filters,
        "sampling_rate": task.sampling_rate,
        "spans_limit": task.spans_limit,
        "run_type": task.run_type,
        "row_type": task.row_type,
        "start_time": task.start_time,
        "continuous_cursor": task.continuous_cursor,
        "evals": eval_revisions,
    }
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def _entry_identity(entry: EvalLogger, row_type: str) -> str:
    if row_type in (RowType.SPANS, RowType.VOICE_CALLS):
        return entry.observation_span_id
    if row_type == RowType.TRACES:
        return str(entry.trace_id)
    if row_type == RowType.SESSIONS:
        return str(entry.trace_session_id)
    raise ValueError(f"Unsupported row_type: {row_type!r}")
