"""Recover dataset evals whose run was abandoned with cells left ``running``.

A dataset eval run flips its cells to ``running`` and writes each result as the
row finishes. A run that dies first (a deploy restart, an OOM, the activity's
time limit) leaves the remaining cells ``running``, and nothing selects them
again: the grid shows them loading forever. Recovery closes them the way the
runner closes a run that crashed (``EvaluationRunner.run_prompt``): the eval
becomes ``Error`` and its running cells ``error`` with a reason.

A running cell that still holds a value is left as it is and reported: a rerun
flips cells to ``running`` and often keeps their value, so the cell still shows
an earlier result. An eval whose running cells all hold one is not touched.

A column is abandoned when none of its cells has been written for longer than
any run can last. The last write comes from the ClickHouse mirror
(``model_hub.selectors.stale_eval_cells``), the only place a bulk flip to
``running`` leaves a timestamp.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from django.db.models import Count
from django.utils import timezone

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.selectors.stale_eval_cells import (
    mirror_is_available,
    read_mirror_last_write,
    read_running_columns,
)
from model_hub.utils.eval_cell_status import (
    CELL_WITHOUT_VALUE,
    mark_eval_cells_stopped,
)
from tfc.utils.error_codes import get_error_message

logger = structlog.get_logger(__name__)

# The drop-in runner closes every execution of the dataset eval activities
# (process_evaluation_single_task, run_evaluation_task) within its 24 h
# execution timeout (tfc/temporal/drop_in/runner.py), queue wait and the three
# 1 h attempts included. A column no one has written for longer has no run left.
DATASET_EVAL_STALE_AFTER = timedelta(hours=24)

# A mirror this far behind is paused, broken or replaying a backlog, and the
# last writes it reports can predate a rerun it has not received.
MIRROR_MAX_IDLE = timedelta(minutes=30)

# A run is about to start: execute_evaluation picks these up every 10 s.
_QUEUED_STATUSES = frozenset(
    {
        StatusType.NOT_STARTED.value,
        StatusType.EXPERIMENT_EVALUATION.value,
        StatusType.OPTIMIZATION_EVALUATION.value,
    }
)

_REASON_SOURCE_ID_SEPARATOR = "-sourceid-"

EXCLUDED_HOLDS_RESULT = "holds_result"


@dataclass(frozen=True)
class RecoveredEval:
    user_eval_metric_id: str
    organization_id: str
    dataset_id: str
    metric_status: str
    running_cells: int
    last_write: datetime


@dataclass(frozen=True)
class ExcludedEval:
    """An abandoned eval's running cells that still hold a result."""

    user_eval_metric_id: str
    organization_id: str
    dataset_id: str
    reason: str
    cells: int


@dataclass(frozen=True)
class DatasetEvalRecovery:
    recovered: list[RecoveredEval]
    excluded: list[ExcludedEval] = field(default_factory=list)
    # Why nothing was read: "mirror_unavailable" or "mirror_idle".
    skipped: str | None = None


def _uuid_or_none(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _dataset_eval_columns_by_metric(column_ids: set[str]) -> dict[str, set[str]]:
    """The dataset eval columns among ``column_ids``, keyed by their eval.

    An eval's result column is ``source=evaluation, source_id=<eval id>``; its
    reason column is ``evaluation_reason`` keyed ``<result column>-sourceid-
    <eval id>``. Experiment and optimisation columns are left out: their runs
    are owned by other workflows.
    """
    columns = list(
        Column.objects.filter(
            id__in=column_ids,
            deleted=False,
            source__in=[
                SourceChoices.EVALUATION.value,
                SourceChoices.EVALUATION_REASON.value,
            ],
        ).values_list("id", "source", "source_id")
    )
    reason_parents = {}
    for column_id, source, source_id in columns:
        if source == SourceChoices.EVALUATION_REASON.value:
            parent, _, metric_id = (source_id or "").partition(
                _REASON_SOURCE_ID_SEPARATOR
            )
            if _uuid_or_none(parent) and _uuid_or_none(metric_id):
                reason_parents[str(column_id)] = (parent, metric_id)
    result_parents = {
        str(column_id)
        for column_id in Column.objects.filter(
            id__in={parent for parent, _ in reason_parents.values()},
            source=SourceChoices.EVALUATION.value,
        ).values_list("id", flat=True)
    }

    by_metric: dict[str, set[str]] = {}
    for column_id, source, source_id in columns:
        if source == SourceChoices.EVALUATION.value:
            metric_id = _uuid_or_none(source_id or "")
        elif str(column_id) in reason_parents:
            parent, metric_id = reason_parents[str(column_id)]
            metric_id = metric_id if parent in result_parents else None
        else:
            metric_id = None
        if metric_id:
            by_metric.setdefault(metric_id, set()).add(str(column_id))
    return by_metric


def recover_stale_dataset_evals(
    *, apply: bool, older_than: timedelta | None = None, limit: int
) -> DatasetEvalRecovery:
    """Close (or, without ``apply``, list) up to ``limit`` abandoned evals.

    An eval is skipped while any of its columns was written within the age: a
    run is still filling it. A queued eval is skipped, and so is a ``Running``
    one that went ``Running`` within the age (``updated_at``): its run may
    still be waiting for a worker. An abandoned ``Running`` eval becomes
    ``Error``; a finished one keeps its status and only its leftover running
    cells are closed. Running cells that hold a value are never closed; they
    are reported in ``excluded``, and an eval with no other running cell is
    left as it is.
    """
    if not mirror_is_available():
        return DatasetEvalRecovery(recovered=[], skipped="mirror_unavailable")
    now = timezone.now()
    mirror_last_write = read_mirror_last_write()
    if mirror_last_write is None or now - mirror_last_write > MIRROR_MAX_IDLE:
        logger.warning(
            "stale_eval_recovery_mirror_idle", mirror_last_write=mirror_last_write
        )
        return DatasetEvalRecovery(recovered=[], skipped="mirror_idle")

    cutoff = now - max(DATASET_EVAL_STALE_AFTER, older_than or timedelta())
    running_columns = {column.column_id: column for column in read_running_columns()}
    columns_by_metric = _dataset_eval_columns_by_metric(set(running_columns))
    stale_by_metric = {
        metric_id: column_ids
        for metric_id, column_ids in columns_by_metric.items()
        if all(running_columns[c].last_write < cutoff for c in column_ids)
    }
    # A deleted dataset shows no grid, so its cells are left as they are.
    metrics = UserEvalMetric.no_workspace_objects.filter(
        id__in=stale_by_metric, dataset__deleted=False
    ).only("id", "status", "organization_id", "dataset_id", "updated_at")

    reason = get_error_message("RUN_INTERRUPTED")
    recovered = []
    excluded = []
    for metric in metrics:
        if len(recovered) >= limit:
            break
        if metric.status in _QUEUED_STATUSES:
            continue
        if metric.status == StatusType.RUNNING.value and metric.updated_at >= cutoff:
            continue
        column_ids = stale_by_metric[str(metric.id)]
        running = Cell.objects.filter(
            column_id__in=column_ids,
            deleted=False,
            status=CellStatus.RUNNING.value,
        ).aggregate(
            total=Count("id"), without_value=Count("id", filter=CELL_WITHOUT_VALUE)
        )
        running_cells = running["without_value"]
        holding_results = running["total"] - running_cells
        if holding_results:
            excluded.append(
                ExcludedEval(
                    user_eval_metric_id=str(metric.id),
                    organization_id=str(metric.organization_id),
                    dataset_id=str(metric.dataset_id),
                    reason=EXCLUDED_HOLDS_RESULT,
                    cells=holding_results,
                )
            )
        if not running_cells:
            continue
        if apply:
            # The runner's own crash path, in its order: the eval, then its
            # cells. Both are skipped if a run started since the read.
            current = UserEvalMetric.no_workspace_objects.filter(
                id=metric.id, status=metric.status
            )
            if metric.status == StatusType.RUNNING.value:
                claimed = bool(
                    current.filter(updated_at__lt=cutoff).update(
                        status=StatusType.ERROR.value, updated_at=now
                    )
                )
            else:
                claimed = current.exists()
            if not claimed:
                continue
            running_cells = mark_eval_cells_stopped(
                metric, reason=reason, column_ids=column_ids, keep_values=True
            )
            if not running_cells:
                # Closed meanwhile, or the write failed (logged by the helper).
                continue
        recovered.append(
            RecoveredEval(
                user_eval_metric_id=str(metric.id),
                organization_id=str(metric.organization_id),
                dataset_id=str(metric.dataset_id),
                metric_status=metric.status,
                running_cells=running_cells,
                last_write=max(running_columns[c].last_write for c in column_ids),
            )
        )
    if apply:
        logger.info("stale_dataset_evals_closed", evals=len(recovered))
    return DatasetEvalRecovery(recovered=recovered, excluded=excluded)
