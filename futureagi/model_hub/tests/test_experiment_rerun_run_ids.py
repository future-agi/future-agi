"""Rerun all rows / Rerun failed rows of a native run, as the page sends them.

The Experiments tab (leaderboard row menu and selection bar), the run panel
and the run grid post each run's Results catalog ``run_id`` as rerun-cells
``source_ids``, without an Idempotency-Key. The server reruns that run's
variant; an id that names nothing in the experiment gets a plain refusal.
"""

import uuid

import pytest

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell
from model_hub.models.native_experiment_runs import NativeRunBinding
from model_hub.services.experiment_submit import state_of
from model_hub.tests.test_native_prompt_create_bridge import (
    native_fixture_evaluator,  # noqa: F401 -- imported autouse fixture
    seed_native_admission,
    subset_post,
    subset_world,
    tenants,  # noqa: F401 -- imported fixture
)

# Worker helpers close old connections and need committed rows (as the bridge).
pytestmark = pytest.mark.django_db(transaction=True)

PLAIN_REFUSAL = (
    "Nothing was rerun. One selected run is no longer part of this experiment. "
    "Refresh the page and try again."
)


def run_id_of(experiment, config):
    """The catalog run id the leaderboard shows for one config's variant."""
    return str(
        NativeRunBinding.objects.get(
            snapshot__experiment=experiment, variant=config.experiment_dataset
        ).run_id
    )


def assert_new_subset_admission(state, before):
    """The rerun committed its own plan; not the plan subset_world left behind."""
    assert state["admission_id"] != before["admission_id"]
    assert state["execution_plan"]["operation"] == "subset"


def test_rerun_all_rows_by_run_ids_starts_every_named_run(tenants):  # noqa: F811
    actor = tenants["a"]
    experiment, configs, _, rows = subset_world(actor)
    run_ids = [run_id_of(experiment, config) for config in configs]
    assert not set(run_ids) & {str(c.experiment_dataset_id) for c in configs}
    before = state_of(experiment)

    response, start = subset_post(actor, experiment, {"source_ids": run_ids}, key=None)

    assert response.status_code == 200, response.content
    start.assert_called_once()
    state = state_of(experiment)
    assert_new_subset_admission(state, before)
    assert set(state["workflow_args"]["native_runs"]) == {str(c.id) for c in configs}
    targets = state["execution_plan"]["targets"]
    for config in configs:
        assert targets[str(config.id)]["snapshot_row_ids"] == sorted(rows)


def test_rerun_all_rows_of_one_run_leaves_its_sibling_alone(tenants):  # noqa: F811
    actor = tenants["a"]
    experiment, configs, _, rows = subset_world(actor)
    before = state_of(experiment)

    response, start = subset_post(
        actor, experiment, {"source_ids": [run_id_of(experiment, configs[1])]}, key=None
    )

    assert response.status_code == 200, response.content
    start.assert_called_once()
    state = state_of(experiment)
    assert_new_subset_admission(state, before)
    assert set(state["workflow_args"]["native_runs"]) == {str(configs[1].id)}
    assert state["execution_plan"]["targets"][str(configs[1].id)][
        "snapshot_row_ids"
    ] == sorted(rows)


def test_rerun_failed_rows_by_run_id_reruns_only_the_failed_rows(tenants):  # noqa: F811
    actor = tenants["a"]
    experiment, configs, columns, rows = subset_world(actor)
    # subset_world completes the admission, not necessarily every provider row
    # (test_results_rerun_identity.py pins its statuses the same way). Pin the
    # second run's output cells: only rows[0] failed.
    output_cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset,
        column=columns[1],
        row_id__in=rows,
        deleted=False,
    )
    output_cells.update(status=CellStatus.PASS.value)
    output_cells.filter(row_id=rows[0]).update(status=CellStatus.ERROR.value)
    assert {
        str(row_id): status
        for row_id, status in output_cells.values_list("row_id", "status")
    } == {
        row_id: CellStatus.ERROR.value if row_id == rows[0] else CellStatus.PASS.value
        for row_id in rows
    }
    before = state_of(experiment)

    response, start = subset_post(
        actor,
        experiment,
        {"source_ids": [run_id_of(experiment, configs[1])], "failed_only": True},
        key=None,
    )

    assert response.status_code == 200, response.content
    start.assert_called_once()
    state = state_of(experiment)
    assert_new_subset_admission(state, before)
    assert set(state["workflow_args"]["native_runs"]) == {str(configs[1].id)}
    assert state["execution_plan"]["targets"][str(configs[1].id)][
        "snapshot_row_ids"
    ] == [rows[0]]


def test_a_run_that_is_not_in_the_experiment_is_refused_in_plain_words(tenants):  # noqa: F811
    actor = tenants["a"]
    experiment, _, _, _ = subset_world(actor)
    before = state_of(experiment)
    unknown = str(uuid.uuid4())

    response, start = subset_post(
        actor, experiment, {"source_ids": [unknown]}, key=None
    )

    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert response.json()["message"] == PLAIN_REFUSAL
    text = response.content.decode()
    assert "Source IDs" not in text and unknown not in text and "{'" not in text
    assert state_of(experiment) == before


@pytest.mark.parametrize("owner", ["same_tenant", "other_tenant"])
def test_a_run_of_another_experiment_is_refused_and_starts_nothing(tenants, owner):  # noqa: F811
    actor = tenants["a"]
    experiment, _, _, _ = subset_world(actor)
    other, _ = seed_native_admission(tenants["b"] if owner == "other_tenant" else actor)
    foreign = str(NativeRunBinding.objects.get(snapshot__experiment=other).run_id)
    # The run-id mapping itself leaves a foreign run as it is.
    from model_hub.views.experiments import _run_ids_as_variant_ids

    assert _run_ids_as_variant_ids(experiment, [foreign]) == [foreign]
    before = state_of(experiment)

    response, start = subset_post(
        actor, experiment, {"source_ids": [foreign]}, key=None
    )

    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert response.json()["message"] == PLAIN_REFUSAL
    assert foreign not in response.content.decode()
    assert state_of(experiment) == before
