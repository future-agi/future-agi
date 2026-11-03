"""Failed waits clear only their eval placeholders, not generating outputs."""

import json

import pytest

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.tests.test_experiment_eval_rerun_dispatch import (
    eval_template as eval_template,
)
from model_hub.tests.test_experiment_eval_rerun_dispatch import experiment as experiment

pytestmark = pytest.mark.django_db


def test_failed_eval_queue_clears_only_its_loading_cells(
    experiment, eval_template, organization, workspace, user
):
    from model_hub.services import experiment_utils

    fail = getattr(experiment_utils, "fail_eval_only_rerun", None)
    assert fail is not None, "A failed wait must clear scoped eval placeholders"
    snapshot = experiment.snapshot_dataset
    metric = UserEvalMetric.objects.create(
        name="Failed wait eval",
        dataset=snapshot,
        template=eval_template,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    result = Column.objects.create(
        dataset=snapshot,
        name="Queued result",
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"edt-sourceid-{metric.id}",
        status=StatusType.RUNNING.value,
    )
    reason = Column.objects.create(
        dataset=snapshot,
        name="Queued reason",
        data_type="text",
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{result.id}-sourceid-{metric.id}",
        status=StatusType.RUNNING.value,
    )
    output = Column.objects.get(dataset=snapshot, source=SourceChoices.EXPERIMENT.value)
    row = Row.objects.filter(dataset=snapshot, deleted=False).first()
    for col in [result, reason, output]:
        Cell.objects.create(
            dataset=snapshot,
            row=row,
            column=col,
            status=CellStatus.RUNNING.value,
            value="",
        )
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    fail(
        experiment,
        [str(metric.id)],
        "Could not wait for final outputs. Retry evaluation.",
    )
    for col in [result, reason]:
        cell = Cell.objects.get(column=col, row=row)
        assert cell.status == CellStatus.ERROR.value
        infos = (
            json.loads(cell.value_infos)
            if isinstance(cell.value_infos, str)
            else cell.value_infos
        )
        assert "Retry evaluation" in infos["reason"]
        col.refresh_from_db()
        assert col.status != StatusType.RUNNING.value
    assert Cell.objects.get(column=output, row=row).status == CellStatus.RUNNING.value
    experiment.refresh_from_db()
    assert experiment.status != StatusType.FAILED.value


def test_failed_dispatch_does_not_leave_loading_placeholders(
    experiment, eval_template, organization, workspace, user
):
    from unittest.mock import patch

    from model_hub.services.experiment_utils import queue_eval_only_rerun

    metric = UserEvalMetric.objects.create(
        name="Dispatch failure",
        dataset=experiment.snapshot_dataset,
        template=eval_template,
        organization=organization,
        workspace=workspace,
        user=user,
    )
    column = Column.objects.create(
        dataset=experiment.snapshot_dataset,
        name="Queued",
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"edt-sourceid-{metric.id}",
    )
    with patch(
        "tfc.temporal.experiments.start_experiment_eval_rerun_workflow",
        side_effect=RuntimeError("Temporal unavailable"),
    ):
        with pytest.raises(RuntimeError, match="Temporal unavailable"):
            queue_eval_only_rerun(experiment, [str(metric.id)])
    cells = list(Cell.objects.filter(column=column))
    assert cells
    assert all(cell.status == CellStatus.ERROR.value for cell in cells)
    experiment.refresh_from_db()
    assert experiment.status != StatusType.RUNNING.value
