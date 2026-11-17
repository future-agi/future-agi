"""Split membership bounds the experiment snapshot and dispatched row set."""

# Imported pytest fixtures are injected by name.
# ruff: noqa: F811

import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.dataset_splits import DatasetSplit, DatasetSplitMembership
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import ExperimentsTable
from model_hub.services.experiment_run_scope import prepare_run_scope
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
)
from model_hub.tests.test_experiment_version_params import (
    create_run,
    setup_run,
    version_world,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)


def make_split(dataset, orders, name="Smoke"):
    split = DatasetSplit.objects.create(dataset=dataset, name=name)
    DatasetSplitMembership.objects.bulk_create(
        [
            DatasetSplitMembership(split=split, row=row)
            for row in Row.no_workspace_objects.filter(
                dataset=dataset, order__in=orders, deleted=False
            )
        ]
    )
    return split


def test_split_union_is_the_snapshot_and_dispatch_scope(auth_client, scope_world):
    dataset = scope_world[0]
    first = make_split(dataset, [1, 3, 6])
    second = make_split(dataset, [3, 5], "Validation")
    ids = [str(first.id), str(second.id)]
    record = create_run(auth_client, request_body(scope_world, split_ids=ids))
    experiment = record.experiment_dataset.experiment
    assert snapshot_orders(experiment) == [1, 3, 5, 6]
    setup = setup_run(record)
    assert set(setup["row_ids"]) == {
        str(pk)
        for pk in Row.objects.filter(dataset=experiment.snapshot_dataset).values_list(
            "id", flat=True
        )
    }
    detail = auth_client.get(f"{CREATE_URL}{experiment.id}/").json()["result"]
    assert detail["run_scope"]["split_ids"] == ids
    assert detail["run_scope"]["eligible_count"] == 4
    assert detail["run_scope"]["selected_count"] == 4


@override_settings(EXPERIMENT_SCOPE_ENABLED=True)
def test_split_intersects_picked_rows_then_samples(auth_client, scope_world):
    dataset = scope_world[0]
    split = make_split(dataset, [1, 3, 6])
    picked = [
        str(pk)
        for pk in Row.objects.filter(
            dataset=dataset, order__in=[0, 1, 3, 7]
        ).values_list("id", flat=True)
    ]
    settings = {
        "split_ids": [str(split.id)],
        "row_ids": picked,
        "sample": {"size": 1, "seed": 23},
    }
    first = create_run(auth_client, request_body(scope_world, **settings))
    second = create_run(auth_client, request_body(scope_world, **settings))
    a, b = first.experiment_dataset.experiment, second.experiment_dataset.experiment
    assert snapshot_orders(a) == snapshot_orders(b)
    assert len(snapshot_orders(a)) == 1
    assert set(snapshot_orders(a)) <= {1, 3}
    assert a.run_scope["eligible_count"] == 2


@pytest.mark.parametrize("kind", ["organization", "workspace", "dataset", "deleted"])
def test_unavailable_split_refused_before_any_write(
    auth_client, scope_world, user, kind
):
    own = scope_world[0]
    if kind == "deleted":
        split = make_split(own, [1])
        split_id = str(split.id)
        split.delete()
    else:
        org = (
            Organization.objects.create(name="Foreign split organization")
            if kind == "organization"
            else own.organization
        )
        workspace = (
            own.workspace
            if kind == "dataset"
            else Workspace.objects.create(
                name="Other split workspace", organization=org, created_by=user
            )
        )
        other = Dataset.no_workspace_objects.create(
            name="Foreign split dataset", organization=org, workspace=workspace
        )
        split_id = str(make_split(other, []).id)
    before = (
        ExperimentsTable.no_workspace_objects.count(),
        Dataset.no_workspace_objects.count(),
    )
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, split_ids=[split_id]), format="json"
        )
    assert response.status_code == 400, response.content
    assert (
        ExperimentsTable.no_workspace_objects.count(),
        Dataset.no_workspace_objects.count(),
    ) == before
    start.assert_not_called()


@pytest.mark.parametrize("case", ["empty", "duplicate", "missing", "oversample"])
@override_settings(EXPERIMENT_SCOPE_ENABLED=True)
def test_invalid_split_selection_refused(auth_client, scope_world, case):
    split = make_split(scope_world[0], [1, 3])
    ids = {
        "empty": [],
        "duplicate": [str(split.id)] * 2,
        "missing": [str(uuid.uuid4())],
    }
    settings = {"split_ids": ids.get(case, [str(split.id)])}
    if case == "oversample":
        settings["sample"] = {"size": 3, "seed": 0}
    before = ExperimentsTable.objects.count()
    response = auth_client.post(
        CREATE_URL, request_body(scope_world, **settings), format="json"
    )
    assert response.status_code == 400, response.content
    assert ExperimentsTable.objects.count() == before


def test_split_membership_never_imports_deleted_or_foreign_rows(
    auth_client, scope_world
):
    dataset = scope_world[0]
    split = make_split(dataset, [1, 3, 6])
    Row.no_workspace_objects.filter(dataset=dataset, order=6).update(deleted=True)
    other = Dataset.no_workspace_objects.create(
        name="Other rows",
        organization=dataset.organization,
        workspace=dataset.workspace,
    )
    foreign_row = Row.no_workspace_objects.create(dataset=other, order=9)
    DatasetSplitMembership.objects.create(split=split, row=foreign_row)
    record = create_run(
        auth_client, request_body(scope_world, split_ids=[str(split.id)])
    )
    assert snapshot_orders(record.experiment_dataset.experiment) == [1, 3]


def test_split_is_revalidated_before_snapshot_write(scope_world, user):
    from model_hub.services.dataset_snapshot import create_dataset_snapshot

    dataset = scope_world[0]
    split = make_split(dataset, [1, 3])
    scope = prepare_run_scope(
        dataset, {"split_ids": [str(split.id)]}, dataset.organization, dataset.workspace
    )
    experiment = ExperimentsTable.objects.create(
        name="Revalidate split", dataset=dataset, user=user, run_scope=scope
    )
    split.delete()
    before = Dataset.objects.count()
    with pytest.raises(ValueError, match="split"):
        create_dataset_snapshot(dataset, experiment)
    assert Dataset.objects.count() == before
