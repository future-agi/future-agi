"""Feedback identities and saved corrections on the persisted results read."""

# ruff: noqa: F811

import pytest

from accounts.models.organization import Organization
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Column
from model_hub.models.evals_metric import Feedback, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentPromptConfig
from model_hub.models.native_experiment_runs import NativeExampleBinding
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_native_run_binding import (
    add_native_eval,  # noqa: F401
    bind,
    make_native_world,
    native_world,  # noqa: F401
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


def evaluation_column(world, metric):
    return Column.objects.create(
        dataset=world["snapshot"],
        name="Evaluation",
        data_type="text",
        source=SourceChoices.EXPERIMENT_EVALUATION.value,
        source_id=f"{world['variant'].id}-{world['output_column'].id}-sourceid-{metric.id}",
    )


def second_variant(world):
    variant = ExperimentDatasetTable.objects.create(
        name="B", experiment=world["experiment"]
    )
    ExperimentPromptConfig.objects.create(
        experiment_dataset=variant,
        prompt_version=world["config"].prompt_version,
        prompt_template=world["config"].prompt_template,
        name="B",
        model="model-b",
    )
    output = Column.objects.create(
        dataset=world["snapshot"],
        name="output-B",
        data_type="text",
        source=SourceChoices.EXPERIMENT.value,
        source_id=str(variant.id),
    )
    variant.columns.add(output)
    return {**world, "variant": variant, "output_column": output}


def test_metric_definitions_map_each_run_to_its_experiment_metric(
    auth_client, native_world, organization, workspace, user, prompt_version
):
    world = native_world
    metric, _ = add_native_eval(world)
    first = bind(world)
    first_column = evaluation_column(world, metric)
    other_variant = second_variant(world)
    second = bind(other_variant)
    second_column = evaluation_column(other_variant, metric)
    other = make_native_world(organization, workspace, user, prompt_version)
    other_metric, _ = add_native_eval(other)
    evaluation_column(other, other_metric)
    foreign_metric, _ = add_native_eval(other)
    foreign_org = Organization.objects.create(name="Separate organization")
    UserEvalMetric.no_workspace_objects.filter(pk=foreign_metric.pk).update(
        organization=foreign_org
    )
    world["experiment"].user_eval_template_ids.add(foreign_metric)

    data = reads.result(
        reads.read(
            auth_client,
            world["dataset"],
            runs=f"{first.id},{second.id}",
            baseline="none",
        )
    )
    definitions = data["summary"]["metrics"]
    assert [entry["key"] for entry in definitions] == [str(metric.id)]
    assert definitions[0]["uem_ids"] == {
        str(first.id): str(metric.id),
        str(second.id): str(metric.id),
    }
    assert definitions[0]["feedback_column_ids"] == {
        str(first.id): str(first_column.id),
        str(second.id): str(second_column.id),
    }
    catalog = reads.result(
        reads.read(
            auth_client,
            world["dataset"],
            "dataset-results-runs",
            experiment_id=str(world["experiment"].id),
            baseline="none",
        )
    )
    assert [entry["key"] for entry in catalog["metric_defs"]] == [str(metric.id)]
    assert catalog["metric_defs"][0]["uem_ids"] == definitions[0]["uem_ids"]
    assert (
        catalog["metric_defs"][0]["feedback_column_ids"]
        == definitions[0]["feedback_column_ids"]
    )


def test_metric_no_longer_in_experiment_is_not_a_feedback_target(
    auth_client, native_world
):
    world = native_world
    metric, _ = add_native_eval(world)
    run = bind(world)
    evaluation_column(world, metric)
    world["experiment"].user_eval_template_ids.remove(metric)
    data = reads.result(reads.read(auth_client, world["dataset"], runs=str(run.id)))
    assert data["summary"]["metrics"][0]["uem_ids"] == {}
    assert data["summary"]["metrics"][0]["feedback_column_ids"] == {}


def test_saved_feedback_round_trips_with_snapshot_row_and_evaluation_column(
    auth_client, native_world, user
):
    world = native_world
    metric, pin = add_native_eval(world)
    run = bind(world)
    column = evaluation_column(world, metric)
    link = (
        NativeExampleBinding.objects.select_related("version_item")
        .filter(snapshot__experiment=world["experiment"])
        .first()
    )
    item = reads.record(
        run,
        link.version_item,
        score_refs=[
            {
                "metric_id": str(metric.id),
                "evaluator_version_id": str(pin.id),
                "status": "assessed",
                "value": 0.2,
            }
        ],
    )
    params = {
        "runs": str(run.id),
        "full": True,
        "example_id": str(link.version_item.example_id),
    }
    before = reads.result(reads.read(auth_client, world["dataset"], **params))
    cell = before["rows"][0]["results"][str(run.id)]
    definition = before["summary"]["metrics"][0]
    assert cell["result_id"] == str(item.id)
    assert cell["feedback_row_id"] == str(link.snapshot_row_id)
    assert cell["feedback_row_id"] != str(item.id)
    response = auth_client.post(
        f"/model-hub/experiments/v2/{world['experiment'].id}/feedback/",
        {
            "source": "experiment",
            "source_id": definition["feedback_column_ids"][str(run.id)],
            "row_id": cell["feedback_row_id"],
            "user_eval_metric": definition["uem_ids"][str(run.id)],
            "value": "0.8",
            "explanation": "The answer matches the reference.",
        },
        format="json",
    )
    saved = reads.result(response)
    feedback = Feedback.objects.get(pk=saved["id"])
    assert feedback.row_id == str(link.snapshot_row_id)
    assert feedback.source_id == str(column.id)
    assert feedback.user_eval_metric_id == metric.id
    after = reads.result(reads.read(auth_client, world["dataset"], **params))
    score = after["rows"][0]["results"][str(run.id)]["scores"][str(metric.id)]
    assert score["value"] == 0.2
    assert score["override"] == {
        "value": "0.8",
        "explanation": "The answer matches the reference.",
        "at": feedback.created_at.isoformat(),
        "user_name": user.name,
    }
    item.refresh_from_db()
    assert item.score_refs[0]["value"] == 0.2

    foreign_org = Organization.objects.create(name="Unrelated organization")
    Feedback.no_workspace_objects.create(
        organization=foreign_org,
        workspace=world["dataset"].workspace,
        user=user,
        source="experiment",
        source_id=str(column.id),
        row_id=str(link.snapshot_row_id),
        user_eval_metric=metric,
        value="0.1",
        explanation="Unrelated feedback",
    )
    again = reads.result(reads.read(auth_client, world["dataset"], **params))
    assert again["rows"][0]["results"][str(run.id)]["scores"][str(metric.id)] == score
    Feedback.objects.create(
        organization=world["dataset"].organization,
        workspace=world["dataset"].workspace,
        user=user,
        source="experiment",
        source_id=str(column.id),
        row_id=str(link.snapshot_row_id),
        user_eval_metric=metric,
        value="0.9",
        explanation="Updated after checking the reference again.",
    )
    latest = reads.result(reads.read(auth_client, world["dataset"], **params))
    latest_score = latest["rows"][0]["results"][str(run.id)]["scores"][str(metric.id)]
    assert latest_score["value"] == 0.2
    assert latest_score["override"]["value"] == "0.9"
    assert (
        latest_score["override"]["explanation"]
        == "Updated after checking the reference again."
    )


def test_feedback_on_a_cell_without_a_recorded_score_adds_no_score(
    auth_client, native_world, user
):
    # The run's only try is still queued, so its cell has no score for the
    # metric. Saved feedback for that row does not invent one, and both the
    # grid and the Matrix read still answer.
    world = native_world
    metric, _ = add_native_eval(world)
    run = bind(world)
    column = evaluation_column(world, metric)
    link = (
        NativeExampleBinding.objects.select_related("version_item")
        .filter(snapshot__experiment=world["experiment"])
        .first()
    )
    Feedback.objects.create(
        organization=world["dataset"].organization,
        workspace=world["dataset"].workspace,
        user=user,
        source="experiment",
        source_id=str(column.id),
        row_id=str(link.snapshot_row_id),
        user_eval_metric=metric,
        value="0.8",
        explanation="Checked by hand.",
    )
    grid = reads.result(
        reads.read(
            auth_client,
            world["dataset"],
            runs=str(run.id),
            full=True,
            example_id=str(link.version_item.example_id),
        )
    )
    cell = grid["rows"][0]["results"][str(run.id)]
    assert cell["feedback_row_id"] == str(link.snapshot_row_id)
    assert str(metric.id) not in cell["scores"]
    matrix = reads.result(
        reads.read(auth_client, world["dataset"], runs=str(run.id), shape="matrix")
    )
    matrix_cell = matrix["rows"][0]["cells"][str(run.id)]
    assert str(metric.id) not in matrix_cell["scores"]


def test_feedback_is_separate_for_each_variant(auth_client, native_world, user):
    world = native_world
    metric, _ = add_native_eval(world)
    first = bind(world)
    first_column = evaluation_column(world, metric)
    other = second_variant(world)
    second = bind(other)
    evaluation_column(other, metric)
    link = (
        NativeExampleBinding.objects.select_related("version_item")
        .filter(snapshot__experiment=world["experiment"])
        .first()
    )
    for run in (first, second):
        reads.record(run, link.version_item)
    Feedback.objects.create(
        organization=world["dataset"].organization,
        workspace=world["dataset"].workspace,
        user=user,
        source="experiment",
        source_id=str(first_column.id),
        row_id=str(link.snapshot_row_id),
        user_eval_metric=metric,
        value="Passed",
        explanation="Reviewed against the reference",
    )
    data = reads.result(
        reads.read(
            auth_client,
            world["dataset"],
            runs=f"{first.id},{second.id}",
            full=True,
            example_id=str(link.version_item.example_id),
        )
    )
    cells = data["rows"][0]["results"]
    assert (
        cells[str(first.id)]["scores"][str(metric.id)]["override"]["value"] == "Passed"
    )
    assert "override" not in cells[str(second.id)]["scores"][str(metric.id)]


def test_external_results_do_not_invent_classic_feedback_identity(
    auth_client, organization, workspace, user
):
    dataset, version, examples = reads.seed(organization, workspace, user, 1)
    run, _ = reads.pinned_run(version, examples, user)
    reads.record(run, examples[0])
    data = reads.result(reads.read(auth_client, dataset, runs=str(run.id)))
    assert data["summary"]["metrics"][0]["uem_ids"] == {}
    assert data["rows"][0]["results"][str(run.id)]["feedback_row_id"] is None
