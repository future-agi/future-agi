"""W4 (walk 1111, row 9): an experiment's Data page on first open.

A native experiment stores each example as {"values": {<column id>: value}}
and no expected value. The results read sent that envelope as the row's
Input (the page showed raw JSON keyed by column ids) and None as Expected
("No expected value"), even when the wizard picked an Expected answers column.
A failed evaluator left no trace on the grid cell, so the page guessed
"no expected value" for it.

Real route, ORM and auth scope; nothing is mocked.
"""
import json
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

ROWS = [("France", "Paris"), ("Japan", "Tokyo"), ("Italy", "Rome")]


def native_world(organization, workspace, user, prompt_version, *, pick_expected):
    dataset = Dataset.objects.create(
        name="walk-model-flows-1111", organization=organization, workspace=workspace,
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
        Cell.objects.create(dataset=dataset, column=answer, row=row, value=capital)
        Cell.objects.create(dataset=dataset, column=question, row=row, value=country)
    experiment = ExperimentsTable.objects.create(
        dataset=dataset, name="walk-exp-model-flows-1111", user=user,
        experiment_type="llm", column=answer if pick_expected else None,
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


def test_input_reads_by_column_and_expected_is_the_picked_column(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version, pick_expected=True)
    rows = grid_rows(auth_client, world)
    assert len(rows) == 3
    for row in rows:
        # What the Input cell shows: the input column by name, never ids.
        assert list(row["inputs"]) == ["input"], row["inputs"]
        text = json.dumps(row["inputs"]) + json.dumps(row["expected"])
        assert str(world.question.id) not in text and str(world.answer.id) not in text
        assert row["values"] == row["inputs"]
    pairs = sorted((r["inputs"]["input"], r["expected"]["value"]["expected_output"])
                   for r in rows)
    assert pairs == sorted(ROWS)
    # The row drawer's Compare tab reads the expected column by "primary".
    assert all(r["expected"]["primary"] == "expected_output" for r in rows)
    # The Matrix and the result drawer read the same named values.
    matrix = reads.result(reads.read(
        auth_client, world.dataset, runs=str(world.run.id), shape="matrix"))
    assert sorted(r["values"]["input"] for r in matrix["rows"]) == sorted(c for c, _ in ROWS)
    example_id = rows[0]["example_id"]
    full = grid_rows(auth_client, world, full=True, example_id=example_id)[0]
    assert full["inputs"] == rows[0]["inputs"]
    assert full["expected"] == rows[0]["expected"]


def test_without_an_expected_column_the_read_says_so(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version, pick_expected=False)
    rows = grid_rows(auth_client, world)
    assert len(rows) == 3
    for row in rows:
        # Every captured value stays visible, named, in the dataset's order.
        assert list(row["inputs"]) == ["input", "expected_output"], row["inputs"]
        assert row["expected"] == {"value": None, "reason": "no_reference_column"}


def test_the_stored_example_the_runner_reads_is_unchanged(
        auth_client, organization, workspace, user, prompt_version):
    world = native_world(organization, workspace, user, prompt_version, pick_expected=True)
    grid_rows(auth_client, world)
    items = DatasetVersionItem.no_workspace_objects.filter(
        dataset_version_id=world.run.dataset_version_id, deleted=False)
    assert items.count() == 3
    for item in items:
        assert set(item.input) == {"values"}
        assert set(item.input["values"]) == {str(world.question.id), str(world.answer.id)}
        assert item.expected is None


def test_a_failed_evaluator_is_counted_on_the_grid_cell(
        auth_client, organization, workspace, user):
    dataset, version, examples = reads.seed(organization, workspace, user, 2)
    run, binding = reads.pinned_run(version, examples, user)
    pair = {"metric_id": str(binding.metric.id),
            "evaluator_version_id": str(binding.version.id)}
    reads.record(run, examples[0], score_refs=[
        {**pair, "status": "error", "reason": "evaluator_execution_failed"}])
    reads.score_record(run, examples[1], binding, 1)
    data = reads.result(reads.read(auth_client, dataset, runs=str(run.id), baseline="none"))
    by_id = {r["example_id"]: r["results"][str(run.id)]["scores"][pair["metric_id"]]
             for r in data["rows"]}
    failed = by_id[str(examples[0].example_id)]
    assert failed["value"] is None and failed["status"] == "not_assessed"
    assert failed["error_trials"] == 1
    scored = by_id[str(examples[1].example_id)]
    assert scored["value"] == 1 and scored["error_trials"] == 0
