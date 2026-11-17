"""Results show Expected for an experiment that runs on an earlier version.

An experiment created on an earlier dataset version shares that version with
the experiment that saved it. The version names the saving experiment's
snapshot columns, while the new experiment's Expected answers column is its
own snapshot copy. The read still shows that column as Expected, for the new
experiment alone and when both experiments' runs are read side by side.
"""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import json
import uuid
from unittest.mock import patch

import pytest

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import NativeExperimentSnapshot
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.native_run_binding import bind_native_run
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_first_experiment_expected_column import (
    ROWS,
    create,
    wizard_body,
    world,  # noqa: F401
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture(autouse=True)
def _no_workflow_start():
    """The create view starts a workflow on commit; no worker runs here."""
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="wf-earlier-version",
    ):
        yield


def saved_version(experiment):
    return NativeExperimentSnapshot.objects.get(experiment=experiment).dataset_version


def edit_live_cells(dataset):
    """Later edits to the dataset, which the saved version does not hold."""
    Cell.no_workspace_objects.filter(dataset=dataset).update(value="edited")


def result_rows(client, dataset, runs, **params):
    return reads.result(reads.read(client, dataset, runs=runs, **params))["rows"]


def assert_expected_column(rows, hidden_ids):
    """Input shows only the input column; Expected is the expected column,
    named, with the saved version's values; no column id reaches the page."""
    assert len(rows) == len(ROWS)
    for row in rows:
        assert list(row["inputs"]) == ["input"], row["inputs"]
        assert row["expected"]["primary"] == "expected_output", row["expected"]
        text = json.dumps(row["inputs"]) + json.dumps(row["expected"])
        for hidden in hidden_ids:
            assert str(hidden) not in text
    pairs = sorted(
        (row["inputs"]["input"], row["expected"]["value"]["expected_output"])
        for row in rows
    )
    assert pairs == sorted(ROWS)


def test_an_experiment_on_an_earlier_version_shows_its_expected_column(
    auth_client, world
):
    first, _ = create(auth_client, wizard_body(world, expected_column=True))
    version = saved_version(first)
    edit_live_cells(world["dataset"])

    second, run_id = create(
        auth_client,
        {
            **wizard_body(world, expected_column=True),
            "dataset_version_id": str(version.id),
        },
    )

    assert saved_version(second).id == version.id
    # Its Expected answers column is its own copy, which the version's
    # columns do not name.
    assert str(second.column_id) not in {
        str(value) for value in version.metadata["columns"].values()
    }
    rows = result_rows(auth_client, world["dataset"], run_id)
    assert_expected_column(rows, [world["answer"].id, second.column_id])
    # The full row (the row drawer) reads the same Expected.
    full = result_rows(
        auth_client,
        world["dataset"],
        run_id,
        full=True,
        example_id=rows[0]["example_id"],
    )[0]
    assert full["inputs"] == rows[0]["inputs"]
    assert full["expected"] == rows[0]["expected"]


def two_column_dataset(organization, workspace, user):
    dataset = Dataset.objects.create(
        name=f"earlier-version-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    question = Column.objects.create(
        dataset=dataset,
        name="input",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    answer = Column.objects.create(
        dataset=dataset,
        name="expected_output",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(
        column_order=[str(question.id), str(answer.id)]
    )
    for order, (country, capital) in enumerate(ROWS):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, column=question, row=row, value=country)
        Cell.objects.create(dataset=dataset, column=answer, row=row, value=capital)
    dataset.refresh_from_db()
    return dataset, question, answer


def experiment_on(dataset, answer, user, name, **snapshot_options):
    """An experiment whose Expected answers column is its snapshot copy of
    the expected column, as the create view leaves it."""
    experiment = ExperimentsTable.objects.create(
        dataset=dataset,
        name=name,
        user=user,
        experiment_type="llm",
        column=answer,
    )
    _, column_mapping, _ = create_dataset_snapshot(
        dataset, experiment, **snapshot_options
    )
    experiment.column = column_mapping[answer.id]
    experiment.save(update_fields=["column"])
    return experiment


def bound_run(dataset, experiment, prompt_version, user):
    """A draft run of one prompt, with no evaluators, on the experiment's copy."""
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        prompt_template=prompt_version.original_template,
        prompt_version=prompt_version,
        name="A",
        model="fixture-model",
        model_params={"temperature": 0.2},
    )
    output = Column.objects.create(
        dataset=experiment.snapshot_dataset,
        name="output",
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    return bind_native_run(
        dataset=dataset,
        experiment=experiment,
        variant=variant,
        execution_id=uuid.uuid4(),
        output_column=output,
        created_by=user,
        effective_settings={"trials": 1, "concurrency": 10},
    )


def test_runs_of_two_experiments_on_one_version_keep_expected(
    auth_client, organization, workspace, user, prompt_version
):
    dataset, question, answer = two_column_dataset(organization, workspace, user)
    first = experiment_on(dataset, answer, user, "saved the version")
    version = saved_version(first)
    edit_live_cells(dataset)
    second = experiment_on(
        dataset, answer, user, "runs on the version", rows_from_version=version
    )
    assert saved_version(second).id == version.id
    assert first.column_id != second.column_id
    runs = [bound_run(dataset, e, prompt_version, user) for e in (first, second)]
    run_ids = ",".join(str(run.id) for run in runs)

    rows = result_rows(auth_client, dataset, run_ids)

    assert_expected_column(
        rows, [question.id, answer.id, first.column_id, second.column_id]
    )
    for row in rows:
        assert set(row["results"]) == {str(run.id) for run in runs}
    # The Matrix reads the same named inputs.
    matrix = reads.result(
        reads.read(auth_client, dataset, runs=run_ids, shape="matrix")
    )
    assert len(matrix["rows"]) == len(ROWS)
    assert all(list(r["values"]) == ["input"] for r in matrix["rows"])
    full = result_rows(
        auth_client, dataset, run_ids, full=True, example_id=rows[0]["example_id"]
    )[0]
    assert full["expected"] == rows[0]["expected"]
