"""Entry-store write primitives for eval tasks.

``materialize_pending`` turns a task's desired row set (streamed by the
resolver) into pending ``EvalLogger`` entries — one per ``(row, eval)`` —
resolving the per-target_type FK shape and stamping the config hash. Idempotent
via the per-target_type unique indexes (``bulk_create(ignore_conflicts=True)``).
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
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
_FK_CHUNK = 1000

# Set to the materialized entry's id while the engine runs one entry; the eval
# core's result write then lands on that entry instead of creating a new row.
_engine_entry_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eval_engine_entry_id", default=None
)

# Identity / FK / lifecycle columns the materialized entry already owns — the
# result write must not touch them (status + hash are stamped by mark_terminal;
# the FKs are db_constraint=False and may point at CH-only rows).
_RESULT_SKIP = {
    "id",
    "trace",
    "trace_id",
    "observation_span",
    "observation_span_id",
    "trace_session",
    "trace_session_id",
    "custom_eval_config",
    "eval_task_id",
    "target_type",
    "value",
    "log_id",
    "feedback_id",
    "deleted",
    "deleted_at",
    "created_at",
    "updated_at",
    "status",
    "config_hash",
    "attempts",
}


@contextmanager
def writing_onto_entry(entry_id: str) -> Iterator[None]:
    """Within this block, eval result writes update the materialized entry in
    place instead of creating a new (colliding) EvalLogger row."""
    token = _engine_entry_id.set(str(entry_id))
    try:
        yield
    finally:
        _engine_entry_id.reset(token)


def in_engine_write_mode() -> bool:
    """True while the eval-task engine is running one entry — result writes
    should update that entry rather than create a new EvalLogger row."""
    return _engine_entry_id.get() is not None


def persist_eval_result(logger_kwargs: dict[str, Any]) -> EvalLogger | None:
    """Persist an eval result. In engine mode (inside ``writing_onto_entry``)
    update the materialized entry — a queryset update that skips the live-unique
    create conflict and ``full_clean`` (so a CH-only FK is fine). Otherwise
    create a new EvalLogger row (legacy cron behavior)."""
    entry_id = _engine_entry_id.get()
    if entry_id is None:
        return EvalLogger.objects.create(**logger_kwargs)
    valid = {f.name for f in EvalLogger._meta.concrete_fields}
    fields = {
        k: v for k, v in logger_kwargs.items() if k in valid and k not in _RESULT_SKIP
    }
    # Fence on RUNNING so a stale worker's late result write no-ops after a
    # reaper requeue + re-claim (see mark_terminal).
    EvalLogger.objects.filter(id=entry_id, status=EvalEntryStatus.RUNNING).update(
        **fields
    )
    return EvalLogger.objects.filter(id=entry_id).first()


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
            fk_by_id = _resolve_entry_fks(
                reader, task.row_type, batch, project_id=str(task.project_id)
            )
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
    # Fence on RUNNING so a stale worker's late write no-ops after the reaper
    # requeued the entry (and another worker re-claimed it).
    return (
        EvalLogger.objects.filter(id=entry.id, status=EvalEntryStatus.RUNNING).update(
            **fields
        )
        > 0
    )


def _resolve_entry_fks(
    reader: CHSpanReader,
    row_type: str,
    identities: Iterable[str],
    *,
    project_id: str,
) -> dict[str, dict[str, Any]]:
    """Map each desired row identity to the EvalLogger FK fields for its
    target_type. Rows that can't be shaped (missing span / rootless trace) are
    absent from the result and skipped by the caller. CH reads are
    project-scoped and chunked into ``_FK_CHUNK``-sized IN-lists."""
    ids = list(identities)
    if row_type == RowType.SESSIONS:
        return {sid: {"trace_session_id": sid} for sid in ids}
    if row_type not in (RowType.SPANS, RowType.VOICE_CALLS, RowType.TRACES):
        raise ValueError(f"Unsupported row_type: {row_type!r}")
    fks: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), _FK_CHUNK):
        chunk = ids[start : start + _FK_CHUNK]
        if row_type == RowType.TRACES:
            # Only root.id is used below, so skip the fat JSON columns.
            roots = reader.list_root_spans_by_trace_ids(
                chunk, include_heavy=False, project_id=project_id
            )
            fks.update(
                {
                    trace_id: {"observation_span_id": root.id, "trace_id": trace_id}
                    for trace_id, root in roots.items()
                }
            )
        else:
            # SPANS / VOICE_CALLS: only id + trace_id are used.
            spans = reader.list_by_ids(
                chunk, include_heavy=False, project_id=project_id
            )
            fks.update(
                {
                    s.id: {"observation_span_id": s.id, "trace_id": s.trace_id}
                    for s in spans
                }
            )
    return fks
