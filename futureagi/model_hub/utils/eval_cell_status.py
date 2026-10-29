"""
Helpers for transitioning eval cells to terminal states when a run stops or
fails. Kept here (not in model_hub.tasks.user_evaluation) so view and task
layers can both import it without pulling in the heavy runner imports
`user_evaluation.py` carries.
"""

from __future__ import annotations

import json
from collections.abc import Collection

import structlog
from django.db.models import Q

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column

logger = structlog.get_logger(__name__)

# A cell with no value to show. A rerun flips cells to ``running`` and often
# keeps their value, so a running cell outside this still shows a result.
CELL_WITHOUT_VALUE = Q(value__isnull=True) | Q(value="")


def mark_eval_cells_stopped(
    user_eval_metric,
    *,
    reason: str,
    column_ids: Collection[str] | None = None,
    keep_values: bool = False,
) -> int:
    """Flip RUNNING cells of an eval (and its paired EVALUATION_REASON cells)
    to ERROR with a human-readable `reason`.

    ``column_ids`` narrows the flip to those of the eval's columns, and
    ``keep_values`` to running cells without a value; stale-work recovery
    passes the columns it proved abandoned and keeps a result a rerun left in
    place.

    Covers:
    - Dataset evals: Column.source = EVALUATION, source_id = eval_id
    - Experiment evals: source = EXPERIMENT_EVALUATION, source_id ends with
      "-sourceid-{eval_id}"
    - Optimisation evals: source = OPTIMISATION_EVALUATION, same suffix
    - Paired reason columns for any of the above (source = EVALUATION_REASON,
      source_id ends with "-sourceid-{eval_id}")

    Safe to call multiple times and from any layer (views, tasks). Never
    raises — failure to update cells must not mask the original error path
    that triggered the stop.

    Returns the number of cells updated.
    """
    try:
        eval_id = str(user_eval_metric.id)
        value_infos = json.dumps({"reason": reason})
        display = reason[:500]

        eval_column_ids = list(
            Column.objects.filter(
                Q(
                    source=SourceChoices.EVALUATION.value,
                    source_id=eval_id,
                )
                | Q(
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.OPTIMISATION_EVALUATION.value,
                    ],
                    source_id__endswith=f"-sourceid-{eval_id}",
                ),
                deleted=False,
            ).values_list("id", flat=True)
        )

        reason_column_ids = list(
            Column.objects.filter(
                source=SourceChoices.EVALUATION_REASON.value,
                source_id__endswith=f"-sourceid-{eval_id}",
                deleted=False,
            ).values_list("id", flat=True)
        )

        target_column_ids = eval_column_ids + reason_column_ids
        if column_ids is not None:
            allowed = {str(column_id) for column_id in column_ids}
            target_column_ids = [
                column_id
                for column_id in target_column_ids
                if str(column_id) in allowed
            ]
        if not target_column_ids:
            return 0

        running = Cell.objects.filter(
            column_id__in=target_column_ids,
            deleted=False,
            status=CellStatus.RUNNING.value,
        )
        if keep_values:
            running = running.filter(CELL_WITHOUT_VALUE)
        updated = running.update(
            status=CellStatus.ERROR.value,
            value=display,
            value_infos=value_infos,
        )
        logger.info(
            "marked_eval_cells_stopped",
            eval_id=eval_id,
            cells_updated=updated,
            reason=reason,
        )
        return updated
    except Exception as exc:
        logger.warning(
            "failed_to_mark_eval_cells_stopped",
            eval_id=str(getattr(user_eval_metric, "id", None)),
            error=str(exc),
        )
        return 0
