"""
Helpers for transitioning eval cells to terminal states when a run stops or
fails. Kept here (not in model_hub.tasks.user_evaluation) so view and task
layers can both import it without pulling in the heavy runner imports
`user_evaluation.py` carries.
"""

from __future__ import annotations

import json

import structlog
from django.db.models import Q

from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.experiments import ExperimentsTable

logger = structlog.get_logger(__name__)


def mark_eval_cells_stopped(user_eval_metric, *, reason: str) -> int:
    """Flip RUNNING cells of an eval (and its paired EVALUATION_REASON cells)
    to ERROR with a human-readable `reason`.

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
        if not user_eval_metric.dataset_id:
            return 0
        eval_id = str(user_eval_metric.id)
        value_infos = json.dumps({"reason": reason})
        display = reason[:500]

        dataset_ids = {user_eval_metric.dataset_id}
        if user_eval_metric.source_id:
            # Group-applied experiment metrics retain the source dataset, while
            # their result columns belong to the experiment's snapshot. Only
            # include a snapshot when this metric is linked to that experiment.
            experiments = ExperimentsTable.objects.filter(
                user_eval_template_ids=user_eval_metric,
                dataset_id=user_eval_metric.dataset_id,
                dataset__organization_id=user_eval_metric.organization_id,
                snapshot_dataset__organization_id=user_eval_metric.organization_id,
                snapshot_dataset__deleted=False,
                deleted=False,
            ).values_list("id", "snapshot_dataset_id")
            for experiment_id, snapshot_id in experiments:
                if str(experiment_id) == str(user_eval_metric.source_id):
                    dataset_ids.add(snapshot_id)

        updated = 0
        for dataset_id in dataset_ids:
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
                    dataset_id=dataset_id,
                    dataset__organization_id=user_eval_metric.organization_id,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            reason_column_ids = list(
                Column.objects.filter(
                    source=SourceChoices.EVALUATION_REASON.value,
                    source_id__endswith=f"-sourceid-{eval_id}",
                    dataset_id=dataset_id,
                    dataset__organization_id=user_eval_metric.organization_id,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            column_ids = eval_column_ids + reason_column_ids
            if not column_ids:
                continue

            updated += Cell.objects.filter(
                column_id__in=column_ids,
                dataset_id=dataset_id,
                row__dataset_id=dataset_id,
                deleted=False,
                status=CellStatus.RUNNING.value,
            ).update(
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
