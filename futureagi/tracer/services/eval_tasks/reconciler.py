"""The reconciler — one idempotent engine that makes a task's live entries
match its desired state (§4). Covers create, add/remove eval, config edit, and
scope change; running it twice is a no-op.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.utils import timezone

from tracer.models.eval_task import RowType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.services.eval_tasks.config_hash import resolved_config_hash
from tracer.services.eval_tasks.entries import materialize_pending

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask


@dataclass
class ReconcileResult:
    created: int = 0
    requeued: int = 0
    dropped: int = 0


def reconcile(task: EvalTask) -> ReconcileResult:
    """Make the task's live entries match its desired config + row set.

    Creates missing pending entries (streamed), re-queues stale / errored /
    skipped in-scope entries, and drops out-of-scope *pending* entries while
    keeping out-of-scope *completed* results (paid data).
    """
    before = _live_count(task)
    materialize_pending(task)
    created = _live_count(task) - before
    if before == 0:
        # Pure create — nothing pre-existing to re-queue or drop.
        return ReconcileResult(created=created)
    requeued, dropped = _requeue_and_drop(task)
    return ReconcileResult(created=created, requeued=requeued, dropped=dropped)


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
    for cfg_id, ids in requeue_by_cfg.items():
        requeued += EvalLogger.objects.filter(id__in=ids).update(
            status=EvalEntryStatus.PENDING,
            config_hash=hashes[cfg_id],
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
