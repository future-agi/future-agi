"""A new experiment can run on an earlier version of its dataset.

The create copies that version's rows into the experiment instead of the
dataset's current rows, and pins the run to that same version. A version
that can no longer be copied exactly (rows or columns deleted since, or
items that never came from this dataset's table) is refused with a sentence
and nothing is written.
"""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings

from model_hub.models.dataset_splits import DatasetSplit, DatasetSplitMembership
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    create_dataset_version,
)
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
)
from model_hub.tests.test_experiment_version_params import (
    create_run,
    setup_run,
    version_world,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)

START = "tfc.temporal.experiments.start_experiment_v2_workflow"
NOT_FOUND = "Dataset version not found"
ROWS_GONE = (
    "This dataset version can't be run: some of its rows were deleted from "
    "the dataset after it was saved."
)
COLUMNS_GONE = (
    "This dataset version can't be run: some of its columns were deleted from "
    "the dataset after it was saved."
)
NOT_FROM_TABLE = (
    "This dataset version can't be run: its rows weren't saved from this "
    "dataset's table."
)
ALL_ROWS = (
    "An earlier dataset version runs on all of its rows. Leave out row_ids, "
    "split_ids and sample, or leave out dataset_version_id to run on the "
    "current rows."
)


def copied_values(experiment):
    """The experiment's copy of the input column, in row order."""
    experiment.refresh_from_db()
    source_column = experiment.dataset.column_order[0]
    return list(
        Cell.no_workspace_objects.filter(
            dataset_id=experiment.snapshot_dataset_id,
            column__source_id=str(source_column),
            deleted=False,
        )
        .order_by("row__order")
        .values_list("value", flat=True)
    )


def version_of(experiment):
    return NativeExperimentSnapshot.objects.get(experiment=experiment).dataset_version


def counts():
    return (
        ExperimentsTable.no_workspace_objects.count(),
        Dataset.no_workspace_objects.count(),
        NativeExperimentSnapshot.objects.count(),
    )


def edit_every_cell(dataset):
    Cell.no_workspace_objects.filter(dataset=dataset).update(value="edited")


def post(auth_client, body):
    with patch(START) as start:
        response = auth_client.post(CREATE_URL, body, format="json")
    return response, start


def first_experiment(auth_client, world):
    return create_run(auth_client, request_body(world)).experiment_dataset.experiment


def test_create_on_an_earlier_version_runs_that_versions_rows(auth_client, scope_world):
    dataset = scope_world[0]
    first = first_experiment(auth_client, scope_world)
    saved = version_of(first)
    before = copied_values(first)
    assert len(before) == 8
    edit_every_cell(dataset)
    # A row added after the version is not part of it.
    Row.no_workspace_objects.create(dataset=dataset, order=8)

    record = create_run(
        auth_client, request_body(scope_world, dataset_version_id=str(saved.id))
    )
    experiment = record.experiment_dataset.experiment
    assert copied_values(experiment) == before
    assert "edited" not in copied_values(experiment)
    assert snapshot_orders(experiment) == list(range(8))
    # The run is pinned to the version it copied, not to a new one.
    assert version_of(experiment).id == saved.id
    setup = setup_run(record)
    assert len(setup["row_ids"]) == 8
    # Both experiments on the one version still read back.
    for shared in (first, experiment):
        detail = auth_client.get(f"{CREATE_URL}{shared.id}/")
        assert detail.status_code == 200, detail.content


def test_create_without_a_version_still_copies_the_current_rows(
    auth_client, scope_world
):
    first = first_experiment(auth_client, scope_world)
    edit_every_cell(scope_world[0])
    second = first_experiment(auth_client, scope_world)
    assert set(copied_values(second)) == {"edited"}
    assert version_of(second).id != version_of(first).id


@pytest.mark.parametrize("kind", ["another dataset", "deleted", "unknown"])
def test_unavailable_version_is_refused_before_any_write(
    auth_client, scope_world, user, kind
):
    dataset = scope_world[0]
    if kind == "unknown":
        version_id = str(uuid.uuid4())
    else:
        owner = (
            dataset
            if kind == "deleted"
            else Dataset.no_workspace_objects.create(
                name=f"other-{uuid.uuid4().hex}",
                organization=dataset.organization,
                workspace=dataset.workspace,
            )
        )
        version = create_dataset_version(
            dataset=owner,
            items=[DatasetVersionItemInput(input={"values": {}})],
            created_by=user,
        )
        if kind == "deleted":
            DatasetVersion.no_workspace_objects.filter(pk=version.pk).update(
                deleted=True
            )
        version_id = str(version.id)
    before = counts()
    response, start = post(
        auth_client, request_body(scope_world, dataset_version_id=version_id)
    )
    assert response.status_code == 404, response.content
    assert NOT_FOUND in response.content.decode()
    assert counts() == before
    start.assert_not_called()


def test_version_with_a_row_deleted_since_is_refused(auth_client, scope_world):
    dataset = scope_world[0]
    saved = version_of(first_experiment(auth_client, scope_world))
    Row.no_workspace_objects.filter(dataset=dataset, order=3).update(deleted=True)
    before = counts()
    response, start = post(
        auth_client, request_body(scope_world, dataset_version_id=str(saved.id))
    )
    assert response.status_code == 400, response.content
    assert ROWS_GONE in response.content.decode()
    assert counts() == before
    start.assert_not_called()


@pytest.mark.parametrize("extra", ["split_ids", "row_ids", "sample"])
@override_settings(EXPERIMENT_SCOPE_ENABLED=True)
def test_an_earlier_version_runs_all_its_rows(auth_client, scope_world, extra):
    dataset = scope_world[0]
    saved = version_of(first_experiment(auth_client, scope_world))
    picked = Row.no_workspace_objects.filter(dataset=dataset, order__in=[1, 2])
    split = DatasetSplit.objects.create(dataset=dataset, name="Smoke")
    DatasetSplitMembership.objects.bulk_create(
        [DatasetSplitMembership(split=split, row=row) for row in picked]
    )
    value = {
        "split_ids": [str(split.id)],
        "row_ids": [str(row.id) for row in picked],
        "sample": {"size": 1, "seed": 3},
    }[extra]
    before = counts()
    response, start = post(
        auth_client,
        request_body(scope_world, dataset_version_id=str(saved.id), **{extra: value}),
    )
    assert response.status_code == 400, response.content
    assert ALL_ROWS in response.content.decode()
    assert counts() == before
    start.assert_not_called()


def test_snapshot_copies_the_version_rows_not_the_live_ones(scope_world, user):
    dataset = scope_world[0]
    first = ExperimentsTable.objects.create(dataset=dataset, name="first", user=user)
    create_dataset_snapshot(dataset, first)
    saved = version_of(first)
    edit_every_cell(dataset)
    second = ExperimentsTable.objects.create(dataset=dataset, name="second", user=user)
    create_dataset_snapshot(dataset, second, rows_from_version=saved)
    assert copied_values(second) == copied_values(first)
    assert "edited" not in copied_values(second)
    assert version_of(second).id == saved.id


def test_version_with_a_column_deleted_since_is_refused(scope_world, user):
    dataset = scope_world[0]
    first = ExperimentsTable.objects.create(dataset=dataset, name="first", user=user)
    create_dataset_snapshot(dataset, first)
    saved = version_of(first)
    Column.no_workspace_objects.filter(id=dataset.column_order[0]).update(deleted=True)
    later = ExperimentsTable.objects.create(dataset=dataset, name="later", user=user)
    before = counts()
    with pytest.raises(RunWriteError) as refusal:
        create_dataset_snapshot(dataset, later, rows_from_version=saved)
    assert str(refusal.value) == COLUMNS_GONE
    assert counts() == before


def test_version_not_saved_from_the_table_is_refused(scope_world, user):
    dataset = scope_world[0]
    version = create_dataset_version(
        dataset=dataset,
        items=[DatasetVersionItemInput(input={"question": "typed by hand"})],
        created_by=user,
    )
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name="hand-made", user=user
    )
    before = counts()
    with pytest.raises(RunWriteError) as refusal:
        create_dataset_snapshot(dataset, experiment, rows_from_version=version)
    assert str(refusal.value) == NOT_FROM_TABLE
    assert counts() == before
