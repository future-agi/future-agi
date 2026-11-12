"""P0 11-11: deleting one evaluation from an experiment must not make it unreadable.

Native experiment runs pin their evaluators. After delete_user_eval soft-deletes
one of them, the experiment detail, its results catalog and the dataset runs
list must still load and show the deleted evaluation as retired, the same rule
external runs already follow. Authored on 587fca65; the cloud runner executes it.
"""

import uuid

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import UserEvalMetric
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
    validate_evaluators,
)
from model_hub.tests import test_results_launch_reads as reads
from model_hub.tests.test_native_run_binding import (
    add_native_eval,
    bind,
    make_native_world,
)

pytestmark = pytest.mark.django_db

RETIRED = "Pinned evaluators retired; historical scores remain readable"


@pytest.fixture
def world(organization, workspace, user, prompt_version):
    return make_native_world(organization, workspace, user, prompt_version)


def soft_delete(metric):
    # The metric write delete_user_eval makes (develop_dataset.py:9047-9049).
    UserEvalMetric._base_manager.filter(pk=metric.pk).update(
        deleted=True, deleted_at=timezone.now()
    )


def read_all(w, run):
    """Each service read that answered 404 on cut 1 after the delete."""
    [checked] = checked_runs(w["dataset"], [run.id])
    projected = project_run(checked)
    native = project_native_run(run, dataset=w["dataset"])
    assert native["run_id"] == str(run.id)
    return checked, projected


def retired_by_metric(projected):
    return {
        entry["metric_id"]: entry["retired"] for entry in projected["eval_versions"]
    }


def test_delete_one_of_two_keeps_every_read_and_marks_it_retired(world):
    w = world
    toxicity, _ = add_native_eval(w)
    kept, _ = add_native_eval(w)
    run = bind(w)
    pins_before = ExperimentRun.no_workspace_objects.get(pk=run.pk).evaluator_versions
    _, before = read_all(w, run)
    assert retired_by_metric(before) == {str(toxicity.id): False, str(kept.id): False}

    soft_delete(toxicity)

    # RED on 587fca65: ResultsReadError("Native run provenance is unavailable", 404).
    checked, after = read_all(w, run)
    assert retired_by_metric(after) == {str(toxicity.id): True, str(kept.id): False}
    assert after["availability"]["evaluators"] == RETIRED
    assert checked._results_retired_evaluators == {str(toxicity.id)}
    names = {d["key"]: d["name"] for d in metric_definitions([checked])}
    assert names == {str(toxicity.id): toxicity.name, str(kept.id): kept.name}
    assert not UserEvalMetric._base_manager.get(pk=kept.pk).deleted
    # The read changes nothing stored on the run.
    assert (
        ExperimentRun.no_workspace_objects.get(pk=run.pk).evaluator_versions
        == pins_before
    )


@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_delete_keeps_experiment_page_and_dataset_runs_tab_loading(auth_client, world):
    w = world
    toxicity, _ = add_native_eval(w)
    add_native_eval(w)
    run = bind(w)
    soft_delete(toxicity)

    # The dataset's Experiments tab ("Couldn't load runs" on cut 1).
    page = reads.result(reads.read(auth_client, w["dataset"], "dataset-results-runs"))
    rows = {row["run_id"]: row for row in page["results"]}
    assert retired_by_metric(rows[str(run.id)])[str(toxicity.id)] is True
    # The experiment page ("Experiment not found" on cut 1).
    response = auth_client.get(f"/model-hub/experiments/v2/{w['experiment'].id}/")
    assert response.status_code == 200, response.content


def test_delete_the_only_evaluation_keeps_reads(world):
    w = world
    only, _ = add_native_eval(w)
    run = bind(w)
    soft_delete(only)

    checked, projected = read_all(w, run)
    assert retired_by_metric(projected) == {str(only.id): True}
    assert projected["availability"]["evaluators"] == RETIRED
    assert validate_evaluators(w["dataset"], [checked])[str(only.id)].name == only.name


def test_delete_then_add_new_evaluation_old_run_shows_retired(world):
    w = world
    old, _ = add_native_eval(w)
    kept, _ = add_native_eval(w)
    first = bind(w)
    soft_delete(old)
    new, _ = add_native_eval(w)
    second = bind(
        w, execution_id=uuid.uuid4(), evaluator_metric_ids=[str(kept.id), str(new.id)]
    )

    _, old_row = read_all(w, first)
    _, new_row = read_all(w, second)
    assert retired_by_metric(old_row) == {str(old.id): True, str(kept.id): False}
    assert retired_by_metric(new_row) == {str(kept.id): False, str(new.id): False}
    assert new_row["availability"]["evaluators"] is None
    checked = checked_runs(w["dataset"], [first.id, second.id])
    assert {d["key"] for d in metric_definitions(checked)} == {
        str(old.id),
        str(kept.id),
        str(new.id),
    }


@pytest.mark.parametrize("deleted", [True, False])
def test_pin_outside_this_experiment_scope_still_fails(world, user, deleted):
    w = world
    moved, _ = add_native_eval(w)
    add_native_eval(w)
    run = bind(w)
    other_org = Organization.objects.create(name="Eval delete scope control")
    other_ws = Workspace.objects.create(
        name="Other",
        organization=other_org,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_ds = Dataset.no_workspace_objects.create(
        name="Other",
        organization=other_org,
        workspace=other_ws,
        user=user,
        column_order=[],
        column_config={},
    )
    UserEvalMetric._base_manager.filter(pk=moved.pk).update(
        dataset_id=other_ds.id, deleted=deleted
    )

    with pytest.raises(ResultsReadError) as error:
        checked_runs(w["dataset"], [run.id])
    assert error.value.status == 404
    assert str(error.value.detail) == "Native run provenance is unavailable"


def test_recording_into_a_run_still_requires_a_live_evaluator(world):
    w = world
    toxicity, _ = add_native_eval(w)
    run = bind(w)
    soft_delete(toxicity)
    binding = NativeRunBinding.objects.select_related("snapshot").get(run_id=run.id)
    with pytest.raises(RunWriteError):
        validate_run_references(
            binding, ExperimentRun.no_workspace_objects.get(pk=run.pk), w["dataset"]
        )
