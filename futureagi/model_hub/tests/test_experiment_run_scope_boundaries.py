"""Scope readback, snapshot paths, and pre-write refusal boundaries."""

from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Cell, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.dataset_snapshot import create_dataset_snapshot
from model_hub.services.experiment_run_scope import prepare_run_scope
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
    version_world,  # noqa: F401
)
from model_hub.tests.test_experiment_v2_results_tenancy import _member_client
from model_hub.tests.test_experiment_version_params import create_run

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def qualified_scope(settings):
    settings.EXPERIMENT_SCOPE_ENABLED = True


def test_scope_readback_is_denied_to_other_organization(auth_client, scope_world):  # noqa: F811
    record = create_run(
        auth_client,
        request_body(
            scope_world,
            sample={"size": 3, "seed": 7},
            metadata={"private": "own scope"},
        ),
    )
    experiment = record.experiment_dataset.experiment
    foreign = _member_client("Scope reader B", "scope-b")
    try:
        response = foreign["client"].get(f"{CREATE_URL}{experiment.id}/")
        assert response.status_code == 404
        assert b"own scope" not in response.content
        for row_id in experiment.run_scope["selected_row_ids"]:
            assert row_id.encode() not in response.content
    finally:
        foreign["client"].stop_workspace_injection()


@pytest.mark.parametrize("raw_sql", [False, True])
def test_both_snapshot_paths_copy_exact_selected_cells(scope_world, user, raw_sql):  # noqa: F811
    dataset = scope_world[0]
    row_ids = list(
        Row.objects.filter(dataset=dataset, order__in=[2, 5]).values_list(
            "id", flat=True
        )
    )
    scope = prepare_run_scope(
        dataset, {"row_ids": row_ids}, dataset.organization, dataset.workspace
    )
    experiment = ExperimentsTable.objects.create(
        name="snapshot paths", dataset=dataset, user=user, run_scope=scope
    )
    snapshot, _, mapping = create_dataset_snapshot(
        dataset, experiment, use_raw_sql_cells=raw_sql
    )
    assert set(mapping) == set(row_ids)
    assert snapshot_orders(experiment) == [2, 5]
    assert set(
        Cell.objects.filter(dataset=snapshot).values_list("value", flat=True)
    ) == {"row-2", "row-5"}


def test_duplicate_and_deleted_rows_are_refused(auth_client, scope_world):  # noqa: F811
    row = Row.objects.filter(dataset=scope_world[0]).first()
    before = ExperimentsTable.objects.count()
    duplicate = auth_client.post(
        CREATE_URL, request_body(scope_world, row_ids=[str(row.id)] * 2), format="json"
    )
    assert duplicate.status_code == 400
    row.deleted = True
    row.save(update_fields=["deleted"])
    deleted = auth_client.post(
        CREATE_URL, request_body(scope_world, row_ids=[str(row.id)]), format="json"
    )
    assert deleted.status_code == 400
    assert ExperimentsTable.objects.count() == before


def test_snapshot_failure_rolls_back_new_experiment(auth_client, scope_world):  # noqa: F811
    before = (ExperimentsTable.objects.count(), Dataset.objects.count())
    with (
        patch(
            "model_hub.views.experiments.create_dataset_snapshot",
            side_effect=ValueError("selected row changed"),
        ),
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
    ):
        response = auth_client.post(
            CREATE_URL,
            request_body(scope_world, sample={"size": 3, "seed": 17}),
            format="json",
        )
    assert response.status_code == 400
    assert (ExperimentsTable.objects.count(), Dataset.objects.count()) == before
    start.assert_not_called()
