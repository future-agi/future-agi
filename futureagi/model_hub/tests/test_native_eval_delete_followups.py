"""Follow-ups to deleting an evaluation from a native experiment.

(a) Re-running the experiment after one of its evaluations was deleted runs
only the live evaluations; with none left, the rerun says so in plain words.
(b) A soft-deleted eval template or pinned version keeps a native run
readable, shown as retired, the same rule external runs follow. Every scope
check still applies, and writers still need a live definition.
"""

import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentRun
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_run_writes import RunWriteError
from model_hub.services.native_run_binding import validate_run_references
from model_hub.services.native_run_projection import project_run as project_native_run
from model_hub.services.results_catalog import (
    ResultsReadError,
    checked_runs,
    metric_definitions,
    project_run,
)
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_native_run_binding import (
    add_native_eval,
    bind,
    make_native_world,
)

pytestmark = pytest.mark.django_db

START = "tfc.temporal.experiments.start_experiment_v2_workflow"
NONE_LEFT = (
    "Every evaluation on this experiment was deleted. "
    "Add an evaluation, then run the experiment again."
)
RETIRED = "Pinned evaluators retired; historical scores remain readable"
PROVENANCE = "Native run provenance is unavailable"


@pytest.fixture
def world(organization, workspace, user, prompt_version):
    w = make_native_world(organization, workspace, user, prompt_version)
    w["experiment"].refresh_from_db()
    return w


def soft_delete_metric(metric):
    # The metric write the evaluation Delete makes.
    UserEvalMetric._base_manager.filter(pk=metric.pk).update(
        deleted=True, deleted_at=timezone.now()
    )


def retire(definition, metric, pin, **changes):
    target = metric.template if definition == "template" else pin
    type(target)._base_manager.filter(pk=target.pk).update(
        deleted=True, deleted_at=timezone.now(), **changes
    )


def stored_pins(run):
    return ExperimentRun.no_workspace_objects.get(pk=run.pk).evaluator_versions


def pinned_metric_ids(run):
    return {pin["metric_id"] for pin in stored_pins(run)}


def retired_by_metric(projected):
    return {
        entry["metric_id"]: entry["retired"] for entry in projected["eval_versions"]
    }


def rerun_whole_experiment(client, experiment):
    with patch(START, return_value="rerun-workflow") as start:
        response = client.post(
            reverse("experiments-v2-rerun"),
            {"experiment_ids": [str(experiment.id)]},
            format="json",
        )
    return response, start


# (a) Rerun after an evaluation was deleted.


def test_rerun_after_deleting_one_evaluation_runs_only_the_live_one(world):
    w = world
    deleted, _ = add_native_eval(w)
    kept, kept_pin = add_native_eval(w)
    first = bind(w)
    first_pins = stored_pins(first)
    soft_delete_metric(deleted)

    # A whole rerun passes no evaluator selection, as the Re-run button does.
    rerun = bind(w, execution_id=uuid.uuid4())

    stored = ExperimentRun.no_workspace_objects.get(pk=rerun.pk)
    assert pinned_metric_ids(stored) == {str(kept.id)}
    assert stored.evaluator_versions[0]["evaluator_version_id"] == str(kept_pin.id)
    assert stored.metadata["native_identity"]["evaluator_metric_ids"] == [str(kept.id)]
    # The earlier run keeps both pins and shows the deleted one as retired.
    assert stored_pins(first) == first_pins
    [checked] = checked_runs(w["dataset"], [first.id])
    assert checked._results_retired_evaluators == {str(deleted.id)}


def test_rerun_with_every_evaluation_deleted_says_so_in_plain_words(world):
    w = world
    only, _ = add_native_eval(w)
    soft_delete_metric(only)
    before = ExperimentRun.no_workspace_objects.count()

    with pytest.raises(RunWriteError) as error:
        bind(w, execution_id=uuid.uuid4())

    assert str(error.value) == NONE_LEFT
    assert ExperimentRun.no_workspace_objects.count() == before


def test_experiment_that_never_had_an_evaluation_still_runs(world):
    run = bind(world)
    assert stored_pins(run) == []


def test_explicitly_selecting_a_deleted_evaluation_is_still_refused(world):
    w = world
    deleted, _ = add_native_eval(w)
    add_native_eval(w)
    soft_delete_metric(deleted)
    with pytest.raises(RunWriteError):
        bind(w, execution_id=uuid.uuid4(), evaluator_metric_ids=[str(deleted.id)])


@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_rerun_button_after_deleting_one_evaluation_starts_the_run(auth_client, world):
    w = world
    deleted, _ = add_native_eval(w)
    kept, _ = add_native_eval(w)
    # Experiment page: Add evaluations, trash, Delete.
    response = auth_client.delete(
        reverse(
            "delete-evals",
            kwargs={"dataset_id": w["dataset"].id, "eval_id": deleted.id},
        ),
        {"delete_column": True, "experiment_id": str(w["experiment"].id)},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert UserEvalMetric._base_manager.get(pk=deleted.pk).deleted

    response, start = rerun_whole_experiment(auth_client, w["experiment"])

    assert response.status_code == 200, response.content
    start.assert_called_once()
    binding = NativeRunBinding.objects.get(snapshot__experiment=w["experiment"])
    assert pinned_metric_ids(binding.run) == {str(kept.id)}


@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_rerun_button_with_no_evaluation_left_answers_the_plain_line(
    auth_client, world
):
    w = world
    only, _ = add_native_eval(w)
    soft_delete_metric(only)

    response, start = rerun_whole_experiment(auth_client, w["experiment"])

    assert response.status_code == 400, response.content
    body = response.content.decode()
    assert NONE_LEFT in body
    assert "membership" not in body
    start.assert_not_called()
    assert not NativeRunBinding.objects.filter(
        snapshot__experiment=w["experiment"]
    ).exists()


# (b) Reads after an eval template or pinned version was deleted.


@pytest.mark.parametrize("definition", ["template", "version"])
def test_deleted_template_or_version_keeps_the_run_readable_as_retired(
    world, definition
):
    w = world
    metric, pin = add_native_eval(w)
    kept, _ = add_native_eval(w)
    run = bind(w)
    pins_before = stored_pins(run)
    retire(definition, metric, pin)

    [checked] = checked_runs(w["dataset"], [run.id])
    projected = project_run(checked)
    native = project_native_run(run, dataset=w["dataset"])

    assert native["run_id"] == str(run.id)
    assert retired_by_metric(projected) == {str(metric.id): True, str(kept.id): False}
    assert projected["availability"]["evaluators"] == RETIRED
    assert checked._results_retired_evaluators == {str(metric.id)}
    names = {d["key"]: d["name"] for d in metric_definitions([checked])}
    assert names == {str(metric.id): metric.name, str(kept.id): kept.name}
    # The read changes nothing stored on the run.
    assert stored_pins(run) == pins_before


@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
@pytest.mark.parametrize("definition", ["template", "version"])
def test_deleted_template_or_version_keeps_experiment_page_and_runs_tab_loading(
    auth_client, world, definition
):
    w = world
    metric, pin = add_native_eval(w)
    add_native_eval(w)
    run = bind(w)
    retire(definition, metric, pin)

    # The dataset's Experiments tab.
    page = reads.result(reads.read(auth_client, w["dataset"], "dataset-results-runs"))
    rows = {row["run_id"]: row for row in page["results"]}
    assert retired_by_metric(rows[str(run.id)])[str(metric.id)] is True
    assert rows[str(run.id)]["availability"]["evaluators"] == RETIRED
    # The experiment page.
    response = auth_client.get(f"/model-hub/experiments/v2/{w['experiment'].id}/")
    assert response.status_code == 200, response.content


@pytest.mark.parametrize(
    "definition,moved",
    [
        ("template", "organization"),
        ("version", "organization"),
        ("version", "template"),
    ],
)
def test_deleted_definition_outside_this_tenant_still_fails(world, definition, moved):
    w = world
    metric, pin = add_native_eval(w)
    add_native_eval(w)
    run = bind(w)
    if moved == "template":
        other = EvalTemplate.no_workspace_objects.create(
            name=f"unrelated-{uuid.uuid4().hex}",
            owner="user",
            eval_type="llm",
            organization=w["dataset"].organization,
            workspace=w["dataset"].workspace,
        )
        retire(definition, metric, pin, eval_template_id=other.id)
    else:
        other_org = Organization.objects.create(name="Retired definition scope control")
        retire(definition, metric, pin, organization_id=other_org.id)

    with pytest.raises(ResultsReadError) as error:
        checked_runs(w["dataset"], [run.id])
    assert error.value.status == 404
    assert str(error.value.detail) == PROVENANCE


@pytest.mark.parametrize("definition", ["template", "version"])
def test_recording_still_requires_a_live_template_and_version(world, definition):
    w = world
    metric, pin = add_native_eval(w)
    run = bind(w)
    retire(definition, metric, pin)
    binding = NativeRunBinding.objects.select_related("snapshot").get(run_id=run.id)
    with pytest.raises(RunWriteError):
        validate_run_references(
            binding, ExperimentRun.no_workspace_objects.get(pk=run.pk), w["dataset"]
        )
