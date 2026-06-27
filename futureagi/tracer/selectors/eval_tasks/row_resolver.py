"""Resolve an eval task's desired (in-scope) row set, deterministically.

The "did the row set change?" axis of the reconciler — the counterpart to the
config hash. Streams the in-scope identity ids (span / trace / session ids, per
the task's row_type) in deterministic order, in batches, so a large historical
task never holds its whole row set in memory. Sampling and filtering run as a
single ClickHouse query (see ``CHSpanReader.iter_sample_row_ids``); the entry
FKs are batch-resolved by the materializer in a later step.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from tracer.models.eval_task import RunType
from tracer.services.clickhouse.v2 import get_reader

if TYPE_CHECKING:
    from tracer.models.eval_task import EvalTask


def iter_desired_rows(
    task: EvalTask, *, batch_size: int = 10_000
) -> Iterator[list[str]]:
    # Row limit applies to historical tasks only; continuous runs forever.
    limit = task.spans_limit if task.run_type == RunType.HISTORICAL else None
    sampling_rate = task.sampling_rate if task.sampling_rate is not None else 100.0

    reader = get_reader()
    try:
        yield from reader.iter_sample_row_ids(
            project_id=str(task.project_id),
            row_type=task.row_type,
            salt=str(task.id),
            sampling_rate=float(sampling_rate),
            filters=task.filters or {},
            limit=limit,
            batch_size=batch_size,
        )
    finally:
        reader.close()
