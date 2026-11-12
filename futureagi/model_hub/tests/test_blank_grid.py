"""Manual creation preserves schemas and never leaves partial datasets."""

import pytest

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.manual_dataset import MANUAL_COLUMN_TYPES

CREATE = "/model-hub/develops/create-dataset-manually/"


@pytest.fixture(autouse=True)
def no_external_billing(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request", None
    )


@pytest.mark.django_db
def test_all_supported_types_and_roles_persist(auth_client, organization, workspace):
    columns = [
        {
            "name": f"column_{kind}",
            "type": kind,
            "role": ("input", "expected", "metadata")[i % 3],
        }
        for i, kind in enumerate(MANUAL_COLUMN_TYPES)
    ]
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": "Typed blank dataset",
            "number_of_rows": 2,
            "columns": columns,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    dataset = Dataset.objects.get(id=response.json()["result"]["dataset_id"])
    assert dataset.organization_id == organization.id
    assert dataset.workspace_id == workspace.id
    saved = {
        str(column.id): column for column in Column.objects.filter(dataset=dataset)
    }
    assert [
        {
            "name": saved[id].name,
            "type": saved[id].data_type,
            "role": dataset.column_config[id]["role"],
        }
        for id in dataset.column_order
    ] == columns
    assert Row.objects.filter(dataset=dataset).count() == 2
    assert Cell.objects.filter(dataset=dataset, value__isnull=True).count() == 2 * len(
        columns
    )


@pytest.mark.django_db
@pytest.mark.parametrize("failure", ["column", "cell"])
def test_database_failure_rolls_back_dataset_schema_and_rows(
    auth_client, monkeypatch, failure
):
    name = f"Rollback blank dataset {failure}"
    if failure == "column":
        original = Column.objects.create
        count = 0

        def create(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("Controlled second column failure")
            return original(**kwargs)

        monkeypatch.setattr(Column.objects, "create", create)
    else:

        def fail(*args, **kwargs):
            raise RuntimeError("Controlled cell persistence failure")

        monkeypatch.setattr(Cell.objects, "bulk_create", fail)
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": name,
            "number_of_rows": 2,
            "columns": [{"name": "first"}, {"name": "second"}],
        },
        format="json",
    )
    assert response.status_code == 500
    assert not Dataset.objects.filter(name=name).exists()
    assert not Column.objects.filter(dataset__name=name).exists()
    assert not Row.objects.filter(dataset__name=name).exists()
    assert not Cell.objects.filter(dataset__name=name).exists()


@pytest.mark.django_db
def test_zero_rows_and_omitted_counts(auth_client):
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": "Empty named grid",
            "number_of_rows": 0,
            "columns": [{"name": "input"}, {"name": "expected"}],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["columns_created"] == 2
    assert result["rows_created"] == 0
    assert not Row.objects.filter(dataset_id=result["dataset_id"]).exists()
    assert not Cell.objects.filter(dataset_id=result["dataset_id"]).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "columns",
    [
        [{"name": "x"}, {"name": " X "}],
        [{"name": ""}],
        [{"name": "x", "role": "split"}],
        [{"name": "x", "type": "imaginary"}],
        [{"name": "x", "unknown": True}],
        [{"name": str(i)} for i in range(101)],
    ],
)
def test_invalid_schema_leaves_no_dataset(auth_client, columns):
    response = auth_client.post(
        CREATE,
        {
            "dataset_name": "Invalid blank grid",
            "columns": columns,
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert not Dataset.objects.filter(name="Invalid blank grid").exists()
