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
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from tracer.models.eval_task import RowType
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.selectors.eval_tasks.row_resolver import (
    EvalTaskSelectionRejected,
    TraceFilterWitness,
    iter_desired_rows,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.eval_tasks.config_hash import resolved_config_hash

if TYPE_CHECKING:
    from datetime import datetime

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
_SAFE_AMBIGUOUS_SPAN_MESSAGE = (
    "Evaluation task row selection could not safely distinguish one or more "
    "spans. Narrow the filters and retry."
)

# Set to the materialized entry's id while the engine runs one entry; the eval
# core's result write then lands on that entry instead of creating a new row.
_engine_entry_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eval_engine_entry_id", default=None
)
_engine_task_selection: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("eval_engine_task_selection", default=None)
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
def writing_onto_entry(
    entry_id: str,
    *,
    output_metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Within this block, eval result writes update the materialized entry in
    place instead of creating a new (colliding) EvalLogger row."""
    token = _engine_entry_id.set(str(entry_id))
    selection = (
        deepcopy(output_metadata.get("_task_selection"))
        if isinstance(output_metadata, dict)
        and isinstance(output_metadata.get("_task_selection"), dict)
        else None
    )
    selection_token = _engine_task_selection.set(selection)
    try:
        yield
    finally:
        _engine_task_selection.reset(selection_token)
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
    selection = _engine_task_selection.get()
    if selection is not None and "output_metadata" in fields:
        result_metadata = fields.get("output_metadata")
        if not isinstance(result_metadata, dict):
            result_metadata = {}
        fields["output_metadata"] = {
            **result_metadata,
            "_task_selection": deepcopy(selection),
        }
    # Fence on RUNNING so a stale worker's late result write no-ops after a
    # reaper requeue + re-claim (see mark_terminal).
    EvalLogger.objects.filter(id=entry_id, status=EvalEntryStatus.RUNNING).update(
        **fields
    )
    return EvalLogger.objects.filter(id=entry_id).first()


def materialize_pending(
    task: EvalTask,
    resolved_row_ids: Iterable[str] | None = None,
    *,
    ceiling: datetime | None = None,
    trace_filter_witnesses: Iterable[TraceFilterWitness] = (),
) -> int:
    """Create one pending entry per (desired row, eval). Returns rows submitted.

    ``ceiling`` (the reconcile pass's frozen now) upper-bounds the continuous
    arrival window so a slow pass can't leave rows above the next cursor.
    """
    evals = list(task.evals.all())
    if not evals:
        return 0
    hashes = {cfg.id: resolved_config_hash(cfg) for cfg in evals}
    target_type = _TARGET_TYPE[task.row_type]
    witnesses_by_trace: dict[str, list[dict[str, Any]]] = {}
    for witness in trace_filter_witnesses:
        witnesses_by_trace.setdefault(str(witness.trace_id), []).append(
            {
                "filter_ordinal": int(witness.filter_ordinal),
                "column_id": str(witness.column_id),
                "col_type": str(witness.col_type),
                "project_id": str(witness.project_id),
                "trace_id": str(witness.trace_id),
                "span_id": str(witness.span_id),
                "start_time": witness.start_time.isoformat(),
            }
        )
    submitted = 0
    reader = get_reader()
    try:
        batches = (
            _iter_materialize_batches(resolved_row_ids)
            if resolved_row_ids is not None
            else iter_desired_rows(task, batch_size=_MATERIALIZE_BATCH, ceiling=ceiling)
        )
        for batch in batches:
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
                    entry_fields = dict(fks)
                    trace_witnesses = witnesses_by_trace.get(str(identity), [])
                    if target_type == EvalTargetType.TRACE and trace_witnesses:
                        entry_fields["output_metadata"] = {
                            "_task_selection": {
                                "filter_witnesses": sorted(
                                    trace_witnesses,
                                    key=lambda item: item["filter_ordinal"],
                                )
                            }
                        }
                    rows.append(
                        EvalLogger(
                            target_type=target_type,
                            custom_eval_config=cfg,
                            eval_task_id=str(task.id),
                            status=EvalEntryStatus.PENDING,
                            config_hash=hashes[cfg.id],
                            **entry_fields,
                        )
                    )
            if rows:
                EvalLogger.objects.bulk_create(rows, ignore_conflicts=True)
                submitted += len(rows)
            if target_type == EvalTargetType.TRACE and witnesses_by_trace:
                _refresh_waiting_trace_witnesses(
                    task,
                    batch,
                    witnesses_by_trace=witnesses_by_trace,
                    config_hashes=hashes,
                )
    finally:
        reader.close()
    return submitted


def _refresh_waiting_trace_witnesses(
    task: EvalTask,
    trace_ids: Iterable[str],
    *,
    witnesses_by_trace: dict[str, list[dict[str, Any]]],
    config_hashes: dict[Any, str],
) -> None:
    """Backfill witness metadata on idempotent/requeued trace entries.

    ``bulk_create(ignore_conflicts=True)`` cannot update an entry that an
    earlier reconcile already materialized. Refresh only entries that are
    waiting/retryable (plus completed entries whose config is about to be
    requeued); never rewrite a completed paid result or an in-flight worker.
    """

    updates: list[EvalLogger] = []
    entries = EvalLogger.objects.filter(
        eval_task_id=str(task.id),
        target_type=EvalTargetType.TRACE,
        trace_id__in=list(trace_ids),
    ).only(
        "id",
        "trace_id",
        "status",
        "config_hash",
        "custom_eval_config_id",
        "output_metadata",
    )
    for entry in entries:
        expected_hash = config_hashes.get(entry.custom_eval_config_id)
        retryable = entry.status in {
            EvalEntryStatus.PENDING,
            EvalEntryStatus.ERRORED,
            EvalEntryStatus.SKIPPED,
        }
        stale_completed = (
            entry.status == EvalEntryStatus.COMPLETED
            and bool(entry.config_hash)
            and expected_hash is not None
            and entry.config_hash != expected_hash
        )
        trace_witnesses = witnesses_by_trace.get(str(entry.trace_id), [])
        if not trace_witnesses or not (retryable or stale_completed):
            continue
        metadata = (
            deepcopy(entry.output_metadata)
            if isinstance(entry.output_metadata, dict)
            else {}
        )
        selection = metadata.get("_task_selection")
        selection = deepcopy(selection) if isinstance(selection, dict) else {}
        selection["filter_witnesses"] = sorted(
            trace_witnesses,
            key=lambda item: item["filter_ordinal"],
        )
        metadata["_task_selection"] = selection
        if metadata != entry.output_metadata:
            entry.output_metadata = metadata
            updates.append(entry)
    if updates:
        EvalLogger.objects.bulk_update(updates, ["output_metadata"])


def _iter_materialize_batches(row_ids: Iterable[str]) -> Iterator[list[str]]:
    """Batch a caller-provided, already-buffered desired row set."""

    batch: list[str] = []
    for row_id in row_ids:
        batch.append(str(row_id))
        if len(batch) >= _MATERIALIZE_BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


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
            roots = reader.list_root_spans_by_trace_ids(
                chunk, project_id=project_id, columns=["id", "trace_id"]
            )
            fks.update(
                {
                    trace_id: {"observation_span_id": root["id"], "trace_id": trace_id}
                    for trace_id, root in roots.items()
                }
            )
        else:
            spans = reader.list_by_ids(
                chunk, project_id=project_id, columns=["id", "trace_id"]
            )
            trace_by_span_id: dict[str, str] = {}
            for span in spans:
                span_id = str(span["id"])
                trace_id = str(span.get("trace_id") or "")
                previous_trace = trace_by_span_id.setdefault(span_id, trace_id)
                if previous_trace != trace_id:
                    # EvalLogger stores only observation_span_id. If another
                    # trace in this project reuses the OTel ID, materializing
                    # either row would make the eventual target arbitrary.
                    raise EvalTaskSelectionRejected(_SAFE_AMBIGUOUS_SPAN_MESSAGE)
            fks.update(
                {
                    str(span["id"]): {
                        "observation_span_id": str(span["id"]),
                        "trace_id": span["trace_id"],
                    }
                    for span in spans
                }
            )
    return fks
