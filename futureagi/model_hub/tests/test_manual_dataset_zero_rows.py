"""Zero-row manual creation keeps the count-only contract and typed blank grids.

A count-only request (no ``columns`` or an empty list) for zero rows stays a
400 before any usage is charged or anything is saved, as it was before blank grids. The
refusal keeps the legacy field error and says how to create an empty dataset:
send its columns. A named, typed grid with zero rows is the blank-grid
feature: it is created and charged for the dataset only, never for rows.
"""

import pytest
from rest_framework import status

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import ManualDatasetCreateRequestSerializer
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401
from tfc.constants.api_calls import APICallTypeChoices

pytestmark = pytest.mark.usefixtures("no_external_lookups")

CREATE = "/model-hub/develops/create-dataset-manually/"
COUNT_ONLY_ZERO_ROWS = [
    {"number_of_rows": 0, "number_of_columns": 1},
    {"number_of_rows": 0},
    {"number_of_rows": 0, "number_of_columns": 3},
    {"number_of_rows": 0, "number_of_columns": 1, "columns": []},
    {"number_of_rows": 0, "columns": []},
    {"number_of_rows": 0, "number_of_columns": 3, "columns": []},
]
COUNT_ONLY_POSITIVE_ROWS = [
    {},
    {"number_of_rows": 1, "number_of_columns": 1},
    {"number_of_rows": 3},
    {"number_of_rows": 2, "number_of_columns": 3},
]
ZERO_ROWS_REFUSAL = (
    "Ensure this value is greater than or equal to 1. "
    'To create an empty dataset with no rows, send "number_of_rows": 0 with a '
    '"columns" list that names each column, for example '
    '[{"name": "input", "type": "text"}].'
)


class _CreatedUsageEntry:
    status = "created"

    def save(self):
        return None


@pytest.fixture
def usage_calls(monkeypatch):
    calls = []

    def record(*args, **kwargs):
        calls.append(kwargs.get("api_call_type"))
        return _CreatedUsageEntry()

    monkeypatch.setattr(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        record,
    )
    return calls


@pytest.fixture
def frontend_blank_grid_payload():
    # Captured from ManuallyCreateDataset.jsx's submit() with DEFAULT_NEW_COLUMNS
    # in datasetName.js: the UI adds type="text"; it does not send data_type.
    return {
        "dataset_name": "Frontend blank grid",
        "model_type": "GenerativeLLM",
        "number_of_rows": 0,
        "number_of_columns": 2,
        "columns": [
            {"name": "input", "type": "text", "role": "input"},
            {"name": "expected_output", "type": "text", "role": "expected"},
        ],
    }


@pytest.mark.parametrize("counts", COUNT_ONLY_ZERO_ROWS)
def test_count_only_zero_rows_keeps_the_legacy_error_and_explains_columns(counts):
    serializer = ManualDatasetCreateRequestSerializer(
        data={"dataset_name": "Legacy count-only", **counts}
    )

    assert not serializer.is_valid()
    assert serializer.errors == {"number_of_rows": [ZERO_ROWS_REFUSAL]}
    assert serializer.errors["number_of_rows"][0].code == "min_value"


@pytest.mark.django_db
@pytest.mark.parametrize("counts", COUNT_ONLY_ZERO_ROWS)
def test_count_only_zero_rows_is_refused_before_charge_or_save(
    auth_client, usage_calls, counts
):
    name = "Count-only zero rows"

    response = auth_client.post(CREATE, {"dataset_name": name, **counts}, format="json")

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    assert response.json()["details"] == {"number_of_rows": [ZERO_ROWS_REFUSAL]}
    assert usage_calls == []
    assert not Dataset.no_workspace_objects.filter(name=name).exists()
    assert not Column.no_workspace_objects.filter(dataset__name=name).exists()


@pytest.mark.django_db
def test_following_the_refusal_guidance_creates_an_empty_dataset(
    auth_client, usage_calls
):
    name = "Guided empty dataset"
    refused = auth_client.post(
        CREATE, {"dataset_name": name, "number_of_rows": 0}, format="json"
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.content

    response = auth_client.post(
        CREATE,
        {
            "dataset_name": name,
            "number_of_rows": 0,
            "columns": [{"name": "input", "type": "text"}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    assert result["rows_created"] == 0
    assert result["columns_created"] == 1
    assert not Row.no_workspace_objects.filter(dataset_id=result["dataset_id"]).exists()
    assert usage_calls == [APICallTypeChoices.DATASET_ADD.value]


@pytest.mark.django_db
@pytest.mark.parametrize("send_count", [False, True])
def test_typed_zero_row_grid_is_created_without_a_row_charge(
    auth_client, workspace, usage_calls, send_count
):
    columns = [
        {"name": "question", "type": "text", "role": "input"},
        {"name": "score", "type": "float", "role": "expected"},
        {"name": "tags", "type": "array", "role": "metadata"},
    ]
    body = {
        "dataset_name": "Typed zero-row grid",
        "number_of_rows": 0,
        "columns": columns,
    }
    if send_count:
        body["number_of_columns"] = len(columns)

    response = auth_client.post(CREATE, body, format="json")

    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    assert result["rows_created"] == 0
    assert result["columns_created"] == len(columns)
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.workspace_id == workspace.id
    saved = {
        str(column.id): column
        for column in Column.no_workspace_objects.filter(dataset=dataset)
    }
    assert [
        {
            "name": saved[column_id].name,
            "type": saved[column_id].data_type,
            "role": dataset.column_config[column_id]["role"],
        }
        for column_id in dataset.column_order
    ] == columns
    assert not Row.no_workspace_objects.filter(dataset=dataset).exists()
    assert not Cell.no_workspace_objects.filter(dataset=dataset).exists()
    assert usage_calls == [APICallTypeChoices.DATASET_ADD.value]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "omit_type", [False, True], ids=["frontend-payload", "name-and-role-only"]
)
def test_frontend_blank_grid_payload_creates_an_empty_dataset(
    auth_client, workspace, usage_calls, frontend_blank_grid_payload, omit_type
):
    body = frontend_blank_grid_payload
    if omit_type:
        for column in body["columns"]:
            column.pop("type")

    response = auth_client.post(CREATE, body, format="json")

    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    assert result["rows_created"] == 0
    assert result["columns_created"] == 2
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    assert dataset.name == body["dataset_name"]
    assert dataset.workspace_id == workspace.id
    saved = {
        str(column.id): column
        for column in Column.no_workspace_objects.filter(dataset=dataset)
    }
    assert len(saved) == 2
    assert [
        {
            "name": saved[column_id].name,
            "type": saved[column_id].data_type,
            "role": dataset.column_config[column_id]["role"],
        }
        for column_id in dataset.column_order
    ] == [
        {"name": "input", "type": "text", "role": "input"},
        {"name": "expected_output", "type": "text", "role": "expected"},
    ]
    assert not Row.no_workspace_objects.filter(dataset=dataset).exists()
    assert not Cell.no_workspace_objects.filter(dataset=dataset).exists()
    assert usage_calls == [APICallTypeChoices.DATASET_ADD.value]


@pytest.mark.parametrize("counts", COUNT_ONLY_POSITIVE_ROWS)
def test_empty_columns_use_the_same_validated_counts_as_omitted_columns(counts):
    body = {"dataset_name": "Count-only positive rows", **counts}
    omitted = ManualDatasetCreateRequestSerializer(data=body)
    empty = ManualDatasetCreateRequestSerializer(data={**body, "columns": []})

    assert omitted.is_valid(), omitted.errors
    assert empty.is_valid(), empty.errors
    assert empty.validated_data == omitted.validated_data


@pytest.mark.django_db
@pytest.mark.parametrize("counts", COUNT_ONLY_POSITIVE_ROWS)
def test_empty_columns_positive_rows_use_count_only_defaults(
    auth_client, usage_calls, counts
):
    response = auth_client.post(
        CREATE,
        {"dataset_name": "Empty columns count-only", "columns": [], **counts},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    rows = counts.get("number_of_rows", 1)
    columns = counts.get("number_of_columns", 1)
    assert result["rows_created"] == rows
    assert result["columns_created"] == columns
    dataset = Dataset.no_workspace_objects.get(id=result["dataset_id"])
    saved = {
        str(column.id): column
        for column in Column.no_workspace_objects.filter(dataset=dataset)
    }
    assert len(saved) == columns
    assert [
        (saved[column_id].name, saved[column_id].data_type)
        for column_id in dataset.column_order
    ] == [(f"Column {index + 1}", "text") for index in range(columns)]
    assert all("role" not in config for config in dataset.column_config.values())
    assert Row.no_workspace_objects.filter(dataset=dataset).count() == rows
    assert Cell.no_workspace_objects.filter(dataset=dataset).count() == rows * columns
    assert usage_calls == [
        APICallTypeChoices.DATASET_ADD.value,
        APICallTypeChoices.ROW_ADD.value,
    ]


@pytest.mark.django_db
def test_count_only_defaults_still_create_one_charged_row(auth_client, usage_calls):
    response = auth_client.post(
        CREATE, {"dataset_name": "Count-only defaults"}, format="json"
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    dataset_id = response.json()["result"]["dataset_id"]
    assert Row.no_workspace_objects.filter(dataset_id=dataset_id).count() == 1
    assert Column.no_workspace_objects.filter(dataset_id=dataset_id).count() == 1
    assert usage_calls == [
        APICallTypeChoices.DATASET_ADD.value,
        APICallTypeChoices.ROW_ADD.value,
    ]
