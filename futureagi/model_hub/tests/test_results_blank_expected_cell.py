"""A native experiment's row whose Expected cell is empty.

The experiment has an Expected answers column, but on some rows that cell
is blank, whitespace only, null, or was never written. The results read
gives those rows no expected value and still names the expected column as
"primary". The row drawer then says "No expected value" for that row, not
"This dataset has no reference column".

Real route, ORM and auth scope; nothing is mocked.
"""
import uuid
from types import SimpleNamespace

import pytest

from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import (
    ExperimentDatasetTable, ExperimentPromptConfig, ExperimentsTable,
)
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.native_run_binding import bind_native_run
from model_hub.tests import test_results_launch_reads as reads

pytestmark = [pytest.mark.django_db,
              pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")]

NO_CELL = object()
# (input, Expected cell): one filled cell, then the empty kinds.
ROWS = [("France", "Paris"), ("Japan", ""), ("Italy", "   "),
        ("Spain", None), ("Peru", NO_CELL)]
EMPTY = ("Japan", "Italy", "Spain", "Peru")
NO_VALUE = {"value": None, "primary": "expected_output"}


def native_world(organization, workspace, user, prompt_version):
    dataset = Dataset.objects.create(
        name="blank-expected", organization=organization, workspace=workspace,
        user=user, column_order=[], column_config={},
    )
    question = Column.objects.create(
        dataset=dataset, name="input", data_type="text", source=SourceChoices.OTHERS.value,
    )
    answer = Column.objects.create(
        dataset=dataset, name="expected_output", data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(
        column_order=[str(question.id), str(answer.id)])
    for order, (country, capital) in enumerate(ROWS):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(dataset=dataset, column=question, row=row, value=country)
        if capital is not NO_CELL:
            Cell.objects.create(dataset=dataset, column=answer, row=row, value=capital)
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name="blank-expected-exp", user=user,
        experiment_type="llm", column=answer,
    )
    snapshot, _, _ = create_dataset_snapshot(dataset, experiment)
    variant = ExperimentDatasetTable.objects.create(name="A", experiment=experiment)
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant, prompt_template=prompt_version.original_template,
        prompt_version=prompt_version, name="A", model="fixture-model",
        model_params={"temperature": 0.2},
    )
    output = Column.objects.create(
        dataset=snapshot, name="output", data_type="text",
        source=SourceChoices.EXPERIMENT.value, source_id=str(variant.id),
    )
    variant.columns.add(output)
    run = bind_native_run(
        dataset=dataset, experiment=experiment, variant=variant,
        execution_id=uuid.uuid4(), output_column=output, created_by=user,
        effective_settings={"trials": 1, "concurrency": 10},
    )
    return SimpleNamespace(dataset=dataset, run=run, question=question, answer=answer)


def grid_rows(client, world, **params):
    data = reads.result(reads.read(client, world.dataset, runs=str(world.run.id), **params))
    return data["rows"]


def by_input(rows):
    return {row["inputs"]["input"]: row for row in rows}


def test_an_empty_expected_cell_still_names_the_expected_column(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version)
    rows = by_input(grid_rows(auth_client, world))
    assert sorted(rows) == sorted(country for country, _ in ROWS)
    assert rows["France"]["expected"] == {
        "value": {"expected_output": "Paris"}, "primary": "expected_output"}
    for country in EMPTY:
        row = rows[country]
        # No expected value on this row, and the experiment's expected column
        # is still named, so the drawer reads "No expected value".
        assert row["expected"] == NO_VALUE, (country, row["expected"])
        # The expected column never shows up as an input.
        assert list(row["inputs"]) == ["input"], (country, row["inputs"])


def test_the_drawer_read_of_an_empty_row_matches_the_grid(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version)
    rows = by_input(grid_rows(auth_client, world))
    for country in ("Japan", "Peru"):
        full = grid_rows(auth_client, world, full=True,
                         example_id=rows[country]["example_id"])
        assert len(full) == 1
        assert full[0]["expected"] == NO_VALUE, (country, full[0]["expected"])
        assert full[0]["inputs"] == {"input": country}


def test_the_stored_examples_are_unchanged(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version)
    grid_rows(auth_client, world)
    items = DatasetVersionItem.no_workspace_objects.filter(
        dataset_version_id=world.run.dataset_version_id, deleted=False)
    assert items.count() == len(ROWS)
    answer_id = str(world.answer.id)
    stored = {item.input["values"][str(world.question.id)]: item for item in items}
    for country, capital in ROWS:
        item = stored[country]
        assert set(item.input) == {"values"}
        assert item.expected is None
        if capital is NO_CELL:
            assert answer_id not in item.input["values"]
        else:
            assert item.input["values"][answer_id] == capital
