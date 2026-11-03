import json
import uuid

import structlog
from django.db.models import Q

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentsTable

logger = structlog.get_logger(__name__)


def queue_eval_only_rerun(experiment, eval_template_ids) -> str | None:
    """Show eval cells as loading and queue a rerun after output generation."""
    from model_hub.views.eval_runner import bulk_update_or_create_cells
    from tfc.temporal.experiments import start_experiment_eval_rerun_workflow

    snapshot = experiment.snapshot_dataset
    if snapshot is None:
        logger.warning(
            "Cannot queue eval-only rerun without experiment snapshot",
            experiment_id=str(experiment.id),
        )
        return None

    ids = list(eval_template_ids)
    row_ids = list(
        Row.objects.filter(dataset=snapshot, deleted=False).values_list("id", flat=True)
    )
    empty_values = {
        "value": "",
        "value_infos": json.dumps({}),
        "status": CellStatus.RUNNING.value,
    }
    for eval_id in ids:
        columns = Column.objects.filter(dataset=snapshot, deleted=False).filter(
            Q(
                source=SourceChoices.EXPERIMENT_EVALUATION.value,
                source_id__endswith=f"-sourceid-{eval_id}",
            )
            | Q(source=SourceChoices.EVALUATION.value, source_id=str(eval_id))
        )
        for column_id in columns.values_list("id", flat=True):
            bulk_update_or_create_cells(row_ids, column_id, snapshot.id, empty_values)

    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    try:
        return start_experiment_eval_rerun_workflow(
            experiment_id=str(experiment.id),
            dataset_id=str(snapshot.id),
            eval_template_ids=[str(eval_id) for eval_id in ids],
        )
    except Exception:
        fail_eval_only_rerun(
            experiment, ids, "Could not queue evaluation. Retry evaluation."
        )
        raise


def fail_eval_only_rerun(experiment, eval_template_ids, reason):
    """Close scoped queued evaluations without cancelling output generation."""
    from model_hub.models.develop_dataset import Cell
    from model_hub.views.experiment_runner import check_and_update_experiment_status

    snapshot = experiment.snapshot_dataset
    if snapshot is None:
        return
    targets = Q(pk__in=[])
    eval_sources = [
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.EVALUATION.value,
        SourceChoices.EVALUATION_REASON.value,
        SourceChoices.EVALUATION_TAGS.value,
        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
    ]
    for eval_id in eval_template_ids:
        targets |= Q(source_id__endswith=f"-sourceid-{eval_id}") | Q(
            source_id=str(eval_id)
        )
    columns = Column.objects.filter(
        targets, dataset=snapshot, deleted=False, source__in=eval_sources
    )
    Cell.objects.filter(
        dataset=snapshot,
        column__in=columns,
        deleted=False,
        status=CellStatus.RUNNING.value,
    ).update(
        status=CellStatus.ERROR.value,
        value="error",
        value_infos=json.dumps({"reason": reason}),
    )
    columns.filter(status=StatusType.RUNNING.value).update(
        status=StatusType.COMPLETED.value
    )
    if not is_experiment_cancelled(experiment.id):
        check_and_update_experiment_status(experiment.id)


def is_experiment_cancelled(experiment_id: uuid.UUID) -> bool:
    """Check if an experiment has been cancelled.

    Used as a guard before writing cell results, to prevent in-flight
    activities from overwriting the cleanup done by stop_experiment_cleanup_activity.
    """
    try:
        status = (
            ExperimentsTable.objects.filter(id=experiment_id, deleted=False)
            .values_list("status", flat=True)
            .first()
        )
        return status == StatusType.CANCELLED.value
    except Exception:
        return False


def maybe_complete_experiment_after_eval_stop(experiment_id) -> bool:
    """If the stopped eval was the last running one, flip experiment to COMPLETED.

    StopUserEvalView flips the individual UserEvalMetric to ERROR on a
    per-eval stop. The experiment itself stays RUNNING until either all
    cells finish or the user presses the whole-experiment Stop. When a
    single eval is the only thing keeping the experiment in RUNNING,
    flipping it stopped should move the experiment to COMPLETED so the
    UI reflects reality.

    Returns True if the experiment status was updated.
    """
    if not experiment_id:
        return False
    try:
        exp_uuid = (
            experiment_id
            if isinstance(experiment_id, uuid.UUID)
            else uuid.UUID(str(experiment_id))
        )
        experiment = ExperimentsTable.objects.filter(
            id=exp_uuid, deleted=False
        ).first()
        if not experiment or experiment.status != StatusType.RUNNING.value:
            return False

        still_running = UserEvalMetric.objects.filter(
            source_id=str(experiment.id),
            deleted=False,
            status__in=[
                StatusType.RUNNING.value,
                StatusType.NOT_STARTED.value,
                StatusType.EXPERIMENT_EVALUATION.value,
            ],
        ).exists()
        if still_running:
            return False

        experiment.status = StatusType.COMPLETED.value
        experiment.save(update_fields=["status", "updated_at"])
        return True
    except Exception:
        return False


def is_user_eval_stopped(user_eval_metric_id) -> bool:
    """Check if a UserEvalMetric has been stopped or deleted.

    StopUserEvalView sets status=ERROR and marks the current running
    cells with a "User stopped evaluation" reason. DeleteEvalsView sets
    deleted=True. This guard lets the Temporal activities and sync
    runners skip cell writes that would otherwise clobber the stop/delete
    marker once the in-flight worker finishes.
    """
    if not user_eval_metric_id:
        return False
    try:
        result = (
            UserEvalMetric.all_objects.filter(id=user_eval_metric_id)
            .values_list("status", "deleted")
            .first()
        )
        if result is None:
            return False
        status, deleted = result
        return deleted or status in (
            StatusType.CANCELLED.value,
            StatusType.ERROR.value,
        )
    except Exception:
        return False
