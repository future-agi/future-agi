"""Experiment deletion must not publish a partially retired selection."""

from unittest.mock import patch

import pytest

from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentRun,
)
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.tests import test_native_run_binding as native

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

DELETE_URL = "/model-hub/experiments/v2/delete/"
CANCEL = "tfc.temporal.experiments.cancel_experiment_workflow"
RETIRE = "model_hub.views.experiments._soft_delete_edt_and_columns"


def _bind(world):
    world["run"] = native.bind(world)
    ExperimentRun.no_workspace_objects.filter(pk=world["run"].pk).update(
        status="cancelled"
    )
    return world


def _world(organization, workspace, user, prompt_version):
    world = native.make_native_world(organization, workspace, user, prompt_version)
    metric, _ = native.add_native_eval(world)
    score = Column.objects.create(
        dataset=world["snapshot"],
        name="Base score",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    reason = Column.objects.create(
        dataset=world["snapshot"],
        name="Base reason",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{score.id}-sourceid-{metric.id}",
    )
    for column, value in (
        (score, "1"),
        (reason, "Recorded evaluation"),
        (world["output_column"], "Recorded output"),
    ):
        Cell.objects.create(
            dataset=world["snapshot"],
            column=column,
            row=next(iter(world["row_map"].values())),
            value=value,
        )
    return _bind(world)


def _sibling(world):
    variant = ExperimentDatasetTable.objects.create(
        experiment=world["experiment"], name="Sibling"
    )
    config = ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        prompt_version=world["config"].prompt_version,
        prompt_template=world["config"].prompt_template,
        name="Sibling",
        model="model-b",
    )
    output = Column.objects.create(
        dataset=world["snapshot"],
        name="Sibling output",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    return _bind(
        {
            **world,
            "variant": variant,
            "config": config,
            "output_column": output,
        }
    )


def _records(worlds):
    records = []
    for world in worlds:
        records.extend(
            [
                world["experiment"],
                world["variant"],
                world["run"],
                NativeRunBinding.objects.get(run=world["run"]),
            ]
        )
    snapshots = {world["snapshot"].pk for world in worlds}
    records.extend(Column.all_objects.filter(dataset_id__in=snapshots))
    records.extend(Cell.all_objects.filter(dataset_id__in=snapshots))
    return records


def _stored(records):
    # Include soft-deleted records and compare every persisted field.
    return [
        type(record)._base_manager.filter(pk=record.pk).values().get()
        for record in records
    ]


@pytest.mark.parametrize("selection", ["single", "variants", "experiments"])
@pytest.mark.parametrize("cancel_fails", [False, True])
def test_rejected_delete_preserves_entire_selection(
    auth_client,
    organization,
    workspace,
    user,
    prompt_version,
    selection,
    cancel_fails,
):
    from model_hub.views.experiments import _soft_delete_edt_and_columns

    worlds = [_world(organization, workspace, user, prompt_version)]
    if selection == "variants":
        worlds.append(_sibling(worlds[0]))
    elif selection == "experiments":
        worlds.append(_world(organization, workspace, user, prompt_version))
    worlds.sort(key=lambda world: world["variant"].pk)
    # Earlier variants can retire; the last bound execution is still running.
    ExperimentRun.no_workspace_objects.filter(pk=worlds[-1]["run"].pk).update(
        status="running"
    )
    for index, world in enumerate(worlds):
        expected = "running" if index == len(worlds) - 1 else "cancelled"
        assert (
            native.project_run(world["run"], dataset=world["dataset"])["status"]
            == expected
        )
    experiment_ids = sorted({str(world["experiment"].pk) for world in worlds})
    records = _records(worlds)
    before = _stored(records)
    failure = RuntimeError("Cancellation unavailable") if cancel_fails else None

    with (
        patch(CANCEL, return_value=None, side_effect=failure) as cancel,
        patch(RETIRE, wraps=_soft_delete_edt_and_columns) as retire,
    ):
        response = auth_client.delete(
            DELETE_URL, {"experiment_ids": experiment_ids}, format="json"
        )

    assert response.status_code == 400
    assert _stored(records) == before
    assert cancel.call_count == len(experiment_ids)
    assert [call.args[0].pk for call in retire.call_args_list] == [
        world["variant"].pk for world in worlds
    ]


def test_terminal_selection_deletes_together_and_keeps_recorded_cells(
    auth_client,
    organization,
    workspace,
    user,
    prompt_version,
):
    worlds = [_world(organization, workspace, user, prompt_version) for _ in range(2)]
    snapshots = {world["snapshot"].pk for world in worlds}
    columns = list(
        Column.all_objects.filter(
            dataset_id__in=snapshots,
            source__in=[
                SourceChoices.EXPERIMENT.value,
                SourceChoices.EVALUATION.value,
                SourceChoices.EVALUATION_REASON.value,
            ],
        )
    )
    inputs = list(
        Column.all_objects.filter(
            dataset_id__in=snapshots,
            source=SourceChoices.OTHERS.value,
        )
    )
    cells = list(Cell.all_objects.filter(dataset_id__in=snapshots))
    before_inputs = _stored(inputs + cells)

    with patch(CANCEL, return_value=None) as cancel:
        response = auth_client.delete(
            DELETE_URL,
            {"experiment_ids": [str(world["experiment"].pk) for world in worlds]},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["result"] == {
        "message": "2 experiments deleted successfully",
        "deleted_count": 2,
    }
    assert cancel.call_count == 2
    retired = columns + [
        world[key] for world in worlds for key in ("experiment", "variant", "run")
    ]
    for record in retired:
        assert type(record)._base_manager.get(pk=record.pk).deleted is True
    assert _stored(inputs + cells) == before_inputs
    assert (
        NativeRunBinding.objects.filter(
            run_id__in=[world["run"].pk for world in worlds]
        ).count()
        == 2
    )
