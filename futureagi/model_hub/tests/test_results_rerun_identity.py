"""W10-EXPERIMENTS-DATASETS-L02: Results IDs are not snapshot Row IDs."""
from uuid import uuid4

import pytest

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell
from model_hub.models.native_experiment_runs import NativeExampleBinding, NativeRunBinding
from model_hub.services.experiment_submit import state_of
from model_hub.tests import test_native_prompt_create_bridge as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
tenants = fixtures.tenants
native_fixture_evaluator = fixtures.native_fixture_evaluator


def result_cells(experiment, column, rows):
    binding = NativeRunBinding.objects.get(snapshot__experiment=experiment, output_column=column)
    links = {
        str(link.snapshot_row_id): link
        for link in NativeExampleBinding.objects.select_related("version_item").filter(
            snapshot=binding.snapshot
        )
    }
    cells = []
    for row_id in rows:
        link = links[row_id]
        assert link.version_item.example_id != link.snapshot_row_id
        assert link.source_row_id != link.snapshot_row_id
        cells.append({
            "column_id": str(column.id),
            "run_id": str(binding.run_id),
            "example_id": str(link.version_item.example_id),
        })
    return binding, cells


@pytest.mark.parametrize("identity", ["canonical", "classic"])
def test_results_rerun_resolves_snapshot_rows_before_failed_only_and_replay(tenants, identity):
    actor = tenants["a"]
    experiment, configs, columns, rows = fixtures.subset_world(actor)
    binding, cells = result_cells(experiment, columns[0], rows)
    receipts = list(binding.run.items.order_by("id").values())
    # subset_world completes the admission, not necessarily every provider row.
    # Pin the mutable cell statuses this selection test needs explicitly; leave
    # the original canonical receipts unchanged.
    output_cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset,
        column=columns[0],
        row_id__in=rows,
        deleted=False,
    )
    assert output_cells.update(status=CellStatus.PASS.value) == len(rows)
    assert output_cells.filter(row_id=rows[0]).update(
        status=CellStatus.ERROR.value
    ) == 1
    assert {
        str(row_id): status
        for row_id, status in output_cells.values_list("row_id", "status")
    } == {
        row_id: CellStatus.ERROR.value if row_id == rows[0] else CellStatus.PASS.value
        for row_id in rows
    }
    if identity == "classic":
        cells = [{"column_id": str(columns[0].id), "row_id": row_id} for row_id in rows]
    body = {"cells": cells, "failed_only": True}

    response, start = fixtures.subset_post(actor, experiment, body)

    assert response.status_code == 200, response.content
    start.assert_called_once()
    state = state_of(experiment)
    targets = state["execution_plan"]["targets"]
    assert set(targets) == {str(configs[0].id)}
    target = targets[str(configs[0].id)]
    assert target["snapshot_row_ids"] == [rows[0]]
    assert target["run_id"] != str(binding.run_id)
    assert list(binding.run.items.order_by("id").values()) == receipts
    replay, replay_start = fixtures.subset_post(actor, experiment, body)
    assert replay.status_code == 200, replay.content
    replay_start.assert_not_called()
    assert state_of(experiment)["admission_id"] == state["admission_id"]


@pytest.mark.parametrize("identity", ["canonical", "classic"])
@pytest.mark.parametrize("failed_only", [False, True])
def test_results_rerun_excludes_unselected_failed_rows_and_replays_across_identities(
    tenants, identity, failed_only
):
    actor = tenants["a"]
    experiment, configs, columns, rows = fixtures.subset_world(actor)
    binding, canonical_cells = result_cells(experiment, columns[0], rows[:1])
    classic_cells = [{"column_id": str(columns[0].id), "row_id": rows[0]}]
    receipts = list(binding.run.items.order_by("id").values())
    output_cells = Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset,
        column=columns[0],
        row_id__in=rows,
        deleted=False,
    )
    # A failed-only filter must intersect the requested rows, not pick every
    # failed cell in the output column. Also cover the unfiltered selector.
    assert len(rows) > 1
    assert output_cells.update(status=CellStatus.ERROR.value) == len(rows)
    unselected = output_cells.exclude(row_id=rows[0]).order_by("id")
    before_unselected = list(unselected.values())
    cells = canonical_cells if identity == "canonical" else classic_cells
    body = {"cells": cells, "failed_only": failed_only}

    response, start = fixtures.subset_post(actor, experiment, body)

    assert response.status_code == 200, response.content
    start.assert_called_once()
    state = state_of(experiment)
    targets = state["execution_plan"]["targets"]
    assert set(targets) == {str(configs[0].id)}
    assert targets[str(configs[0].id)]["snapshot_row_ids"] == rows[:1]
    assert targets[str(configs[0].id)]["run_id"] != str(binding.run_id)
    assert list(unselected.values()) == before_unselected
    assert list(binding.run.items.order_by("id").values()) == receipts

    # Both selector namespaces normalize to the same immutable snapshot cells.
    # Replaying that intent must not refilter mutable statuses or dispatch again.
    replay_cells = classic_cells if identity == "canonical" else canonical_cells
    assert output_cells.update(status=CellStatus.PASS.value) == len(rows)
    before_replay = list(output_cells.order_by("id").values())
    replay, replay_start = fixtures.subset_post(
        actor, experiment, {"cells": replay_cells, "failed_only": failed_only}
    )
    assert replay.status_code == 200, replay.content
    assert replay.json() == response.json()
    replay_start.assert_not_called()
    assert state_of(experiment) == state
    assert list(output_cells.order_by("id").values()) == before_replay
    assert list(binding.run.items.order_by("id").values()) == receipts


@pytest.mark.parametrize("invalid", [
    "other_column", "other_experiment", "other_tenant", "unknown_example",
    "deleted_run", "missing_run", "both_identities", "example_as_row",
])
def test_results_rerun_rejects_unbound_identity_without_dispatch_or_writes(tenants, invalid):
    actor = tenants["a"]
    experiment, _, columns, rows = fixtures.subset_world(actor)
    binding, cells = result_cells(experiment, columns[0], rows[:1])
    cell = cells[0]
    if invalid == "other_column":
        cell["column_id"] = str(columns[1].id)
    elif invalid in {"other_experiment", "other_tenant"}:
        other_actor = tenants["b"] if invalid == "other_tenant" else actor
        other, _ = fixtures.seed_native_admission(other_actor)
        cell["run_id"] = str(NativeRunBinding.objects.get(snapshot__experiment=other).run_id)
    elif invalid == "unknown_example":
        cell["example_id"] = str(uuid4())
    elif invalid == "deleted_run":
        binding.run.deleted = True
        binding.run.save(update_fields=["deleted"])
    elif invalid == "missing_run":
        cell.pop("run_id")
    elif invalid == "both_identities":
        cell["row_id"] = rows[0]
    elif invalid == "example_as_row":
        cell["row_id"] = cell.pop("example_id")
        cell.pop("run_id")
    before = state_of(experiment)
    before_cells = list(Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset
    ).order_by("id").values())
    before_runs = NativeRunBinding.objects.filter(snapshot__experiment=experiment).count()

    response, start = fixtures.subset_post(actor, experiment, {"cells": cells, "failed_only": True})

    assert response.status_code == 400, response.content
    start.assert_not_called()
    assert state_of(experiment) == before
    assert NativeRunBinding.objects.filter(snapshot__experiment=experiment).count() == before_runs
    assert list(Cell.no_workspace_objects.filter(
        dataset=experiment.snapshot_dataset
    ).order_by("id").values()) == before_cells
