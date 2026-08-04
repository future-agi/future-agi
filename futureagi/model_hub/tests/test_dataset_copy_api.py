import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row


class _SuccessfulResourceCallLog:
    status = "created"

    def save(self):
        return None


@pytest.fixture
def dataset_factory(organization, workspace):
    def create_dataset(name, *, target_workspace=workspace):
        dataset = Dataset.objects.create(
            name=name,
            organization=organization,
            workspace=target_workspace,
        )
        input_column = Column.objects.create(
            name="input",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        output_column = Column.objects.create(
            name="output",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        dataset.column_order = [str(input_column.id), str(output_column.id)]
        dataset.column_config = {
            str(input_column.id): {"is_visible": True},
            str(output_column.id): {"is_visible": True},
        }
        dataset.save()
        return dataset, input_column, output_column

    return create_dataset


def add_row(dataset, values, order=0):
    row = Row.objects.create(dataset=dataset, order=order)
    for column, value in values.items():
        Cell.objects.create(
            dataset=dataset,
            column=column,
            row=row,
            value=value,
        )
    return row


@pytest.mark.django_db
def test_duplicate_rows_rejects_other_workspace_dataset(
    auth_client, organization, user, dataset_factory
):
    other_workspace = Workspace.objects.create(
        name="Other Dataset Copy Workspace",
        organization=organization,
        created_by=user,
    )
    dataset, input_column, _output_column = dataset_factory(
        "Other workspace duplicate source",
        target_workspace=other_workspace,
    )
    row = add_row(dataset, {input_column: "outside"}, order=0)

    response = auth_client.post(
        f"/model-hub/datasets/{dataset.id}/duplicate-rows/",
        {"row_ids": [str(row.id)], "num_copies": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == 1


@pytest.mark.django_db
def test_duplicate_rows_rejects_rows_outside_target_dataset(
    auth_client, dataset_factory
):
    dataset, input_column, _output_column = dataset_factory("Duplicate target")
    other_dataset, other_input_column, _other_output = dataset_factory(
        "Duplicate outside row"
    )
    add_row(dataset, {input_column: "inside"}, order=0)
    other_row = add_row(other_dataset, {other_input_column: "outside"}, order=0)

    response = auth_client.post(
        f"/model-hub/datasets/{dataset.id}/duplicate-rows/",
        {"row_ids": [str(other_row.id)], "num_copies": 1},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Row.objects.filter(dataset=dataset, deleted=False).count() == 1


@pytest.mark.django_db
def test_merge_appends_rows_after_target_dataset_order(auth_client, dataset_factory):
    source_dataset, source_input, source_output = dataset_factory("Merge source")
    target_dataset, target_input, target_output = dataset_factory("Merge target")
    source_row = add_row(
        source_dataset,
        {source_input: "source input", source_output: "source output"},
        order=0,
    )
    add_row(target_dataset, {target_input: "target 10", target_output: "target"}, 10)
    add_row(target_dataset, {target_input: "target 0", target_output: "target"}, 0)

    response = auth_client.post(
        f"/model-hub/datasets/{source_dataset.id}/merge/",
        {
            "target_dataset_id": str(target_dataset.id),
            "row_ids": [str(source_row.id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"]["rows_added"] == 1
    copied_row = (
        Row.objects.filter(dataset=target_dataset, deleted=False)
        .order_by("-order")
        .first()
    )
    assert copied_row.order == 11
    copied_input = Cell.objects.get(row=copied_row, column=target_input)
    assert copied_input.value == "source input"


@pytest.mark.django_db
def test_add_rows_from_existing_rejects_columns_outside_source_or_target(
    auth_client, dataset_factory
):
    source_dataset, source_input, _source_output = dataset_factory(
        "Existing rows source"
    )
    target_dataset, target_input, _target_output = dataset_factory(
        "Existing rows target"
    )
    outside_dataset, outside_input, _outside_output = dataset_factory(
        "Existing rows outside"
    )
    add_row(source_dataset, {source_input: "source"}, order=0)

    bad_source_response = auth_client.post(
        f"/model-hub/develops/{target_dataset.id}/add_rows_from_existing_dataset/",
        {
            "source_dataset_id": str(source_dataset.id),
            "column_mapping": {
                str(outside_input.id): str(target_input.id),
            },
        },
        format="json",
    )
    bad_target_response = auth_client.post(
        f"/model-hub/develops/{target_dataset.id}/add_rows_from_existing_dataset/",
        {
            "source_dataset_id": str(source_dataset.id),
            "column_mapping": {
                str(source_input.id): str(outside_input.id),
            },
        },
        format="json",
    )

    assert bad_source_response.status_code == status.HTTP_400_BAD_REQUEST
    assert bad_target_response.status_code == status.HTTP_400_BAD_REQUEST
    assert Row.objects.filter(dataset=target_dataset, deleted=False).count() == 0


@pytest.mark.django_db
def test_add_rows_from_existing_rejects_other_workspace_source(
    auth_client, organization, user, dataset_factory
):
    other_workspace = Workspace.objects.create(
        name="Other Existing Rows Workspace",
        organization=organization,
        created_by=user,
    )
    source_dataset, source_input, _source_output = dataset_factory(
        "Other workspace existing source",
        target_workspace=other_workspace,
    )
    target_dataset, target_input, _target_output = dataset_factory(
        "Existing rows target"
    )
    add_row(source_dataset, {source_input: "outside"}, order=0)

    response = auth_client.post(
        f"/model-hub/develops/{target_dataset.id}/add_rows_from_existing_dataset/",
        {
            "source_dataset_id": str(source_dataset.id),
            "column_mapping": {
                str(source_input.id): str(target_input.id),
            },
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Row.objects.filter(dataset=target_dataset, deleted=False).count() == 0


@pytest.mark.django_db
def test_duplicate_dataset_rejects_other_workspace_before_usage_charge(
    auth_client, organization, user, dataset_factory, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other Duplicate Dataset Workspace",
        organization=organization,
        created_by=user,
    )
    dataset, input_column, _output_column = dataset_factory(
        "Other workspace duplicate dataset",
        target_workspace=other_workspace,
    )
    add_row(dataset, {input_column: "outside"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        f"/model-hub/datasets/{dataset.id}/duplicate/",
        {"name": "Should not be created", "row_ids": []},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []


@pytest.mark.django_db
def test_duplicate_dataset_rejects_rows_outside_source_before_usage_or_creation(
    auth_client, organization, dataset_factory, monkeypatch
):
    source_dataset, input_column, _output_column = dataset_factory(
        "Duplicate dataset source"
    )
    outside_dataset, outside_input, _outside_output = dataset_factory(
        "Duplicate dataset outside"
    )
    add_row(source_dataset, {input_column: "inside"}, order=0)
    outside_row = add_row(outside_dataset, {outside_input: "outside"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        f"/model-hub/datasets/{source_dataset.id}/duplicate/",
        {
            "name": "Should not duplicate outside rows",
            "row_ids": [str(outside_row.id)],
            "selected_all_rows": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert not Dataset.objects.filter(
        name="Should not duplicate outside rows",
        organization=organization,
        deleted=False,
    ).exists()


@pytest.mark.django_db
def test_clone_dataset_rejects_other_workspace_before_usage_charge(
    auth_client, organization, user, dataset_factory, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other Clone Dataset Workspace",
        organization=organization,
        created_by=user,
    )
    dataset, input_column, _output_column = dataset_factory(
        "Other workspace clone dataset",
        target_workspace=other_workspace,
    )
    add_row(dataset, {input_column: "outside"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        f"/model-hub/develops/clone-dataset/{dataset.id}/",
        {"new_dataset_name": "Should not be cloned"},
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []


@pytest.mark.django_db
def test_add_as_new_rejects_other_workspace_before_usage_charge(
    auth_client, organization, user, dataset_factory, monkeypatch
):
    other_workspace = Workspace.objects.create(
        name="Other Add As New Workspace",
        organization=organization,
        created_by=user,
    )
    dataset, input_column, _output_column = dataset_factory(
        "Other workspace add as new",
        target_workspace=other_workspace,
    )
    add_row(dataset, {input_column: "outside"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        "/model-hub/develops/add-as-new/",
        {
            "dataset_id": str(dataset.id),
            "name": "Should not be created",
            "columns": {str(input_column.id): "input copy"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert usage_calls == []


@pytest.mark.django_db
def test_add_as_new_rejects_duplicate_name_before_usage_charge(
    auth_client, organization, dataset_factory, monkeypatch
):
    dataset, input_column, _output_column = dataset_factory("Add as new duplicate name")
    add_row(dataset, {input_column: "source"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        "/model-hub/develops/add-as-new/",
        {
            "dataset_id": str(dataset.id),
            "name": dataset.name,
            "columns": {str(input_column.id): "input copy"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert (
        Dataset.objects.filter(
            name=dataset.name,
            organization=organization,
            deleted=False,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_add_as_new_rejects_columns_outside_source_before_usage_or_creation(
    auth_client, organization, dataset_factory, monkeypatch
):
    source_dataset, input_column, _output_column = dataset_factory("Add as new source")
    outside_dataset, outside_input, _outside_output = dataset_factory(
        "Add as new outside"
    )
    add_row(source_dataset, {input_column: "inside"}, order=0)
    add_row(outside_dataset, {outside_input: "outside"}, order=0)
    usage_calls = []

    def record_usage(*args, **kwargs):
        usage_calls.append((args, kwargs))
        return _SuccessfulResourceCallLog()

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct_resource",
        record_usage,
    )

    response = auth_client.post(
        "/model-hub/develops/add-as-new/",
        {
            "dataset_id": str(source_dataset.id),
            "name": "Should not add as new",
            "columns": {str(outside_input.id): "outside copy"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert usage_calls == []
    assert not Dataset.objects.filter(
        name="Should not add as new",
        organization=organization,
        deleted=False,
    ).exists()
