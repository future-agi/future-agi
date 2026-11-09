"""Experiment scope is enforced by the persisted snapshot, not the browser."""

import uuid
from unittest.mock import patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Cell, Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.tests.test_experiment_version_params import (
    body,
    create_run,
    setup_run,
    version_world,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)
CREATE_URL = "/model-hub/experiments/v2/"


@pytest.fixture(autouse=True)
def qualified_scope(settings):
    settings.EXPERIMENT_SCOPE_ENABLED = True


@pytest.fixture
def scope_world(version_world):  # noqa: F811
    dataset = version_world[0]
    column_id = dataset.column_order[0]
    for order in range(1, 8):
        row = Row.no_workspace_objects.create(dataset=dataset, order=order)
        Cell.no_workspace_objects.create(
            dataset=dataset, row=row, column_id=column_id, value=f"row-{order}"
        )
    return version_world


def request_body(world, **settings):
    return {**body(world, {}), **settings}


def snapshot_orders(experiment):
    return list(
        Row.no_workspace_objects.filter(
            dataset=experiment.snapshot_dataset, deleted=False
        )
        .order_by("order")
        .values_list("order", flat=True)
    )


def test_picked_rows_are_the_only_snapshot_and_runner_rows(auth_client, scope_world):
    dataset = scope_world[0]
    picked = list(
        Row.objects.filter(dataset=dataset, order__in=[1, 3, 6]).values_list(
            "id", flat=True
        )
    )
    record = create_run(
        auth_client,
        request_body(scope_world, row_ids=[str(row_id) for row_id in picked]),
    )
    experiment = record.experiment_dataset.experiment
    assert snapshot_orders(experiment) == [1, 3, 6]
    assert Cell.objects.filter(dataset=experiment.snapshot_dataset).count() == 3
    setup = setup_run(record)
    assert set(setup["row_ids"]) == {
        str(row_id)
        for row_id in Row.objects.filter(
            dataset=experiment.snapshot_dataset
        ).values_list("id", flat=True)
    }
    detail = auth_client.get(f"{CREATE_URL}{experiment.id}/")
    assert detail.status_code == 200
    assert detail.json()["result"]["run_scope"]["selected_count"] == 3


def test_seeded_sample_is_repeatable_within_picked_rows(auth_client, scope_world):
    rows = list(
        Row.objects.filter(dataset=scope_world[0], order__gte=2).values_list(
            "id", flat=True
        )
    )
    settings = {
        "row_ids": [str(row_id) for row_id in rows],
        "sample": {"size": 3, "seed": 17},
        "metadata": {"purpose": "scope regression", "batch": "17"},
    }
    first = create_run(auth_client, request_body(scope_world, **settings))
    second = create_run(
        auth_client,
        request_body(
            scope_world, **{**settings, "row_ids": list(reversed(settings["row_ids"]))}
        ),
    )
    exp = first.experiment_dataset.experiment
    assert snapshot_orders(exp) == snapshot_orders(second.experiment_dataset.experiment)
    assert len(snapshot_orders(exp)) == 3
    assert set(snapshot_orders(exp)) <= set(range(2, 8))
    detail = auth_client.get(f"{CREATE_URL}{exp.id}/").json()["result"]
    assert detail["sample"] == settings["sample"]
    assert detail["metadata"] == settings["metadata"]
    assert set(detail["row_ids"]) == set(settings["row_ids"])
    assert detail["run_scope"]["source_count"] == 8
    assert detail["run_scope"]["eligible_count"] == 6
    assert detail["run_scope"]["selected_count"] == 3


def test_no_scope_keeps_existing_full_snapshot(auth_client, scope_world):
    record = create_run(auth_client, request_body(scope_world))
    experiment = record.experiment_dataset.experiment
    assert snapshot_orders(experiment) == list(range(8))
    detail = auth_client.get(f"{CREATE_URL}{experiment.id}/").json()["result"]
    assert "sample" not in detail
    assert "row_ids" not in detail
    assert detail["metadata"] == {}


@pytest.mark.parametrize("foreign_workspace", [False, True])
def test_foreign_row_rejected_before_any_write(
    auth_client, scope_world, foreign_workspace, user
):
    own = scope_world[0]
    org = (
        own.organization
        if foreign_workspace
        else Organization.objects.create(name="Other scope org")
    )
    workspace = Workspace.objects.create(
        name="Other scope workspace", organization=org, created_by=user
    )
    dataset = Dataset.no_workspace_objects.create(
        name="Foreign scope", organization=org, workspace=workspace
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    before = (
        ExperimentsTable.no_workspace_objects.count(),
        Dataset.no_workspace_objects.count(),
    )
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, row_ids=[str(row.id)]), format="json"
        )
    assert response.status_code == 400
    assert (
        ExperimentsTable.no_workspace_objects.count(),
        Dataset.no_workspace_objects.count(),
    ) == before
    start.assert_not_called()


@pytest.mark.parametrize(
    "settings",
    [
        {"row_ids": []},
        {"row_ids": [str(uuid.uuid4())]},
        {"sample": {"size": 0, "seed": 1}},
        {"sample": {"size": 9, "seed": 1}},
        {"sample": {"size": 2, "seed": True}},
        {"sample": {"size": 2, "seed": 1, "algorithm": "ignored"}},
        {"metadata": ["not an object"]},
        {"metadata": {"nested": {"not": "flat"}}},
    ],
)
def test_invalid_scope_is_rejected_before_writes(auth_client, scope_world, settings):
    before = (ExperimentsTable.objects.count(), Dataset.objects.count())
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, **settings), format="json"
        )
    assert response.status_code == 400, response.content
    assert (ExperimentsTable.objects.count(), Dataset.objects.count()) == before
    start.assert_not_called()


def test_snapshot_revalidates_scope_before_creating_dataset(scope_world, user):
    from model_hub.services.dataset_snapshot import create_dataset_snapshot

    dataset = scope_world[0]
    exp = ExperimentsTable.objects.create(
        name="revalidate scope", dataset=dataset, user=user
    )
    exp.run_scope = {"selected_row_ids": [str(uuid.uuid4())]}
    before = Dataset.objects.count()
    with pytest.raises(ValueError, match="row"):
        create_dataset_snapshot(dataset, exp)
    assert Dataset.objects.count() == before
