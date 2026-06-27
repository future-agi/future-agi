"""Entry-store write primitives for eval tasks.

``materialize_pending`` turns a task's desired row set (streamed by the
resolver) into pending ``EvalLogger`` entries — one per ``(row, eval)`` —
resolving the per-target_type FK shape and stamping the config hash. Idempotent
via the per-target_type unique indexes (``bulk_create(ignore_conflicts=True)``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from tracer.models.eval_task import RowType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.selectors.eval_tasks.row_resolver import iter_desired_rows
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tasks.config_hash import resolved_config_hash

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask
    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

_TARGET_TYPE = {
    RowType.SPANS: EvalTargetType.SPAN,
    RowType.VOICE_CALLS: EvalTargetType.SPAN,
    RowType.TRACES: EvalTargetType.TRACE,
    RowType.SESSIONS: EvalTargetType.SESSION,
}

_MATERIALIZE_BATCH = 5_000


def materialize_pending(task: EvalTask) -> int:
    """Create one pending entry per (desired row, eval). Returns rows submitted."""
    evals = list(task.evals.all())
    if not evals:
        return 0
    hashes = {cfg.id: resolved_config_hash(cfg) for cfg in evals}
    target_type = _TARGET_TYPE[task.row_type]
    submitted = 0
    reader = get_reader()
    try:
        for batch in iter_desired_rows(task, batch_size=_MATERIALIZE_BATCH):
            fk_by_id = _resolve_entry_fks(reader, task.row_type, batch)
            rows = []
            for identity in batch:
                fks = fk_by_id.get(identity)
                if fks is None:
                    # e.g. a trace with no root span, or a span gone from CH.
                    continue
                for cfg in evals:
                    rows.append(
                        EvalLogger(
                            target_type=target_type,
                            custom_eval_config=cfg,
                            eval_task_id=str(task.id),
                            status=EvalEntryStatus.PENDING,
                            config_hash=hashes[cfg.id],
                            **fks,
                        )
                    )
            if rows:
                EvalLogger.objects.bulk_create(rows, ignore_conflicts=True)
                submitted += len(rows)
    finally:
        reader.close()
    return submitted


def soft_delete_live(task: EvalTask) -> int:
    """Soft-delete every live entry of the task (Delete & rerun). Returns count."""
    return EvalLogger.objects.filter(eval_task_id=str(task.id)).update(
        deleted=True, deleted_at=timezone.now()
    )


def claim_pending_batch(task: EvalTask, n: int) -> list[EvalLogger]:
    """Atomically claim up to ``n`` pending entries and mark them running.

    ``FOR UPDATE SKIP LOCKED`` lets many workers pull disjoint batches without
    blocking each other. ``updated_at`` is stamped to "now" so the reaper can
    measure how long an entry has been running.
    """
    now = timezone.now()
    with transaction.atomic():
        entries = list(
            EvalLogger.objects.filter(
                eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
            )
            .select_for_update(skip_locked=True)
            .order_by("created_at", "id")[:n]
        )
        if entries:
            EvalLogger.objects.filter(id__in=[e.id for e in entries]).update(
                status=EvalEntryStatus.RUNNING, updated_at=now
            )
    for entry in entries:
        entry.status = EvalEntryStatus.RUNNING
        entry.updated_at = now
    return entries


def mark_terminal(
    entry: EvalLogger,
    status: str,
    *,
    config_hash: str,
    error: bool | None = None,
    error_message: str | None = None,
    skipped_reason: str | None = None,
) -> bool:
    """Record an entry's terminal state (status + the hash that produced it).

    No-op (returns False) if the entry was soft-deleted mid-run — a Delete &
    rerun landing while it ran. error / error_message / skipped_reason are set
    only when passed, so a result already written by the evaluator isn't
    clobbered.
    """
    fields: dict[str, Any] = {
        "status": status,
        "config_hash": config_hash,
        "updated_at": timezone.now(),
    }
    if error is not None:
        fields["error"] = error
    if error_message is not None:
        fields["error_message"] = error_message
    if skipped_reason is not None:
        fields["skipped_reason"] = skipped_reason
    return EvalLogger.objects.filter(id=entry.id).update(**fields) > 0


def _resolve_entry_fks(
    reader: CHSpanReader, row_type: str, identities: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Map each desired row identity to the EvalLogger FK fields for its
    target_type. Rows that can't be shaped (missing span / rootless trace) are
    absent from the result and skipped by the caller."""
    if row_type in (RowType.SPANS, RowType.VOICE_CALLS):
        spans = reader.list_by_ids(list(identities))
        return {
            s.id: {"observation_span_id": s.id, "trace_id": s.trace_id} for s in spans
        }
    if row_type == RowType.TRACES:
        roots = reader.list_root_spans_by_trace_ids(list(identities))
        return {
            trace_id: {"observation_span_id": root.id, "trace_id": trace_id}
            for trace_id, root in roots.items()
        }
    if row_type == RowType.SESSIONS:
        return {sid: {"trace_session_id": sid} for sid in identities}
    raise ValueError(f"Unsupported row_type: {row_type!r}")
