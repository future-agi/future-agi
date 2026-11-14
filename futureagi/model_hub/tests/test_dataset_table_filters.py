import json
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    DatasetRowDataRequestSerializer,
    DatasetTableQuerySerializer,
    DatasetUpdateCellValueRequestSerializer,
)
from model_hub.utils.annotation_queue_helpers import _filter_dataset_cells
from model_hub.views.develop_dataset import GetDatasetTableView


def _filter(column_id, filter_type, filter_op, filter_value=None):
    config = {
        "filter_type": filter_type,
        "filter_op": filter_op,
    }
    if filter_value is not None:
        config["filter_value"] = filter_value
    return {"column_id": str(column_id), "filter_config": config}


@pytest.fixture
def dataset_filter_seed(organization, workspace):
    dataset = Dataset.objects.create(
        name="Filter dataset",
        organization=organization,
        workspace=workspace,
    )
    text_col = Column.objects.create(
        name="status",
        data_type=DataTypeChoices.TEXT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    bool_col = Column.objects.create(
        name="passed",
        data_type=DataTypeChoices.BOOLEAN.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    rows = [
        Row.objects.create(dataset=dataset, order=1),
        Row.objects.create(dataset=dataset, order=2),
        Row.objects.create(dataset=dataset, order=3),
    ]
    Cell.objects.create(dataset=dataset, row=rows[0], column=text_col, value="Alpha")
    Cell.objects.create(dataset=dataset, row=rows[1], column=text_col, value="Beta")
    Cell.objects.create(dataset=dataset, row=rows[2], column=text_col, value="")
    Cell.objects.create(dataset=dataset, row=rows[0], column=bool_col, value="true")
    Cell.objects.create(dataset=dataset, row=rows[1], column=bool_col, value="false")
    Cell.objects.create(dataset=dataset, row=rows[2], column=bool_col, value="")
    return dataset, rows, text_col, bool_col


def _apply(dataset, filters, columns):
    return list(
        GetDatasetTableView()
        ._apply_filters(
            Cell.objects.filter(dataset=dataset),
            Row.objects.filter(dataset=dataset),
            filters,
            [],
            {str(column.id): column for column in columns},
        )
        .order_by("order")
    )


def test_dataset_table_text_in_and_not_in_filters(dataset_filter_seed):
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "in", ["alpha", "beta"])],
            [text_col, bool_col],
        )
        == rows[:2]
    )
    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "not_in", ["alpha"])],
            [text_col, bool_col],
        )
        == rows[1:]
    )


def test_dataset_table_boolean_not_equals_and_null_filters(dataset_filter_seed):
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(bool_col.id, "boolean", "not_equals", "true")],
            [text_col, bool_col],
        )
        == rows[1:]
    )
    assert _apply(
        dataset,
        [_filter(text_col.id, "text", "is_null")],
        [text_col, bool_col],
    ) == [rows[2]]
    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "is_not_null")],
            [text_col, bool_col],
        )
        == rows[:2]
    )


def test_dataset_table_query_serializer_rejects_camel_case_aliases():
    serializer = DatasetTableQuerySerializer(
        data={
            "filters": json.dumps([]),
            "pageSize": "10",
            "currentPageIndex": "0",
            "columnConfigOnly": "false",
        }
    )

    assert not serializer.is_valid()
    assert "pageSize" in serializer.errors
    assert "currentPageIndex" in serializer.errors
    assert "columnConfigOnly" in serializer.errors


def test_dataset_row_data_request_rejects_legacy_filter_shape(dataset_filter_seed):
    _dataset, rows, text_col, _bool_col = dataset_filter_seed
    serializer = DatasetRowDataRequestSerializer(
        data={
            "row_id": str(rows[0].id),
            "filters": [
                {
                    "column_id": str(text_col.id),
                    "filterConfig": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Alpha",
                    },
                }
            ],
        }
    )

    assert not serializer.is_valid()
    assert "filters" in serializer.errors


def test_dataset_update_cell_value_request_rejects_legacy_aliases(dataset_filter_seed):
    _dataset, rows, text_col, _bool_col = dataset_filter_seed
    serializer = DatasetUpdateCellValueRequestSerializer(
        data={
            "rowId": str(rows[0].id),
            "columnId": str(text_col.id),
            "newValue": "Gamma",
        }
    )

    assert not serializer.is_valid()
    assert "rowId" in serializer.errors
    assert "columnId" in serializer.errors
    assert "newValue" in serializer.errors


def test_dataset_table_api_rejects_legacy_query_aliases(
    auth_client, dataset_filter_seed
):
    dataset, _rows, _text_col, _bool_col = dataset_filter_seed

    response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {
            "pageSize": "10",
            "currentPageIndex": "0",
            "columnConfigOnly": "false",
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db(transaction=True)
def test_dataset_table_exact_continuation_keeps_same_timestamp_columns_ordered(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, bool_col = dataset_filter_seed
    # Equal timestamps used to leave the inherited ``-created_at`` ordering
    # nondeterministic, so page two could report a different column inventory.
    Column.all_objects.filter(id__in=[text_col.id, bool_col.id]).update(
        created_at=text_col.created_at
    )
    expected_column_ids = sorted([str(text_col.id), str(bool_col.id)])

    first = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {
            "page_size": 2,
            "current_page_index": 0,
            "exact_snapshot": True,
        },
    )
    assert first.status_code == status.HTTP_200_OK
    first_result = first.json()["result"]
    assert [row["row_id"] for row in first_result["table"]] == [
        str(row.id) for row in rows[:2]
    ]
    assert first_result["metadata"]["total_rows"] == 3
    assert first_result["metadata"]["total_pages"] == 2
    assert first_result["metadata"]["page_size"] == 2
    assert first_result["metadata"]["current_page_index"] == 0
    assert first_result["metadata"]["has_more"] is True
    assert first_result["metadata"]["next_page_index"] == 1
    assert first_result["metadata"]["next_cursor"]
    assert first_result["metadata"]["is_exact"] is True
    assert first_result["metadata"]["snapshot_bound"] is True
    assert [column["id"] for column in first_result["column_config"]] == (
        expected_column_ids
    )

    second = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {
            "page_size": 2,
            "current_page_index": 1,
            "exact_snapshot": True,
            "cursor": first_result["metadata"]["next_cursor"],
        },
    )
    assert second.status_code == status.HTTP_200_OK
    second_result = second.json()["result"]
    assert [row["row_id"] for row in second_result["table"]] == [str(rows[2].id)]
    assert second_result["metadata"]["has_more"] is False
    assert second_result["metadata"]["next_page_index"] is None
    assert second_result["metadata"]["next_cursor"] is None
    assert second_result["metadata"]["is_exact"] is True
    assert [column["id"] for column in second_result["column_config"]] == (
        expected_column_ids
    )


@pytest.mark.django_db(transaction=True)
def test_dataset_table_exact_continuation_rejects_mutation_between_pages(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed
    url = f"/model-hub/develops/{dataset.id}/get-dataset-table/"

    first = auth_client.get(
        url,
        {
            "page_size": 2,
            "current_page_index": 0,
            "exact_snapshot": True,
        },
    )
    assert first.status_code == status.HTTP_200_OK
    cursor = first.json()["result"]["metadata"]["next_cursor"]
    assert cursor

    # QuerySet.update deliberately bypasses auto_now. The MVCC binding must
    # still detect the changed tuple version; updated_at-based fingerprints do
    # not cover this real write path.
    Cell.objects.filter(row=rows[0], column=text_col).update(value="changed")

    changed = auth_client.get(
        url,
        {
            "page_size": 2,
            "current_page_index": 1,
            "exact_snapshot": True,
            "cursor": cursor,
        },
    )
    assert changed.status_code == status.HTTP_409_CONFLICT
    assert changed.json()["code"] == "dataset_snapshot_changed"


def test_update_cell_value_api_rejects_legacy_payload_aliases(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed
    cell = Cell.objects.get(dataset=dataset, row=rows[0], column=text_col)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "rowId": str(rows[0].id),
            "columnId": str(text_col.id),
            "newValue": "Gamma",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    cell.refresh_from_db()
    assert cell.value == "Alpha"


def test_update_cell_value_api_accepts_canonical_payload(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed
    cell = Cell.objects.get(dataset=dataset, row=rows[0], column=text_col)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(rows[0].id),
            "column_id": str(text_col.id),
            "new_value": "Gamma",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.value == "Gamma"


def test_dataset_row_data_api_rejects_legacy_filter_shape(
    auth_client, dataset_filter_seed
):
    dataset, rows, text_col, _bool_col = dataset_filter_seed

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/get-row-data/",
        {
            "row_id": str(rows[0].id),
            "filters": [
                {
                    "column_id": str(text_col.id),
                    "filterConfig": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "Alpha",
                    },
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.fixture
def array_col_seed(organization, workspace):
    """A dataset with an array-typed column (e.g. PDF-to-text / extracted
    entities) whose cells are stored as ``json.dumps([...])``."""
    dataset = Dataset.objects.create(
        name="Array filter dataset",
        organization=organization,
        workspace=workspace,
    )
    arr_col = Column.objects.create(
        name="pdf_to_text",
        data_type=DataTypeChoices.ARRAY.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    rows = [
        Row.objects.create(dataset=dataset, order=1),
        Row.objects.create(dataset=dataset, order=2),
        Row.objects.create(dataset=dataset, order=3),
    ]
    Cell.objects.create(
        dataset=dataset,
        row=rows[0],
        column=arr_col,
        value=json.dumps(["CIRCULAR NO. 123 dated 2024"]),
    )
    Cell.objects.create(
        dataset=dataset,
        row=rows[1],
        column=arr_col,
        value=json.dumps(["internal memo, no reference"]),
    )
    Cell.objects.create(
        dataset=dataset,
        row=rows[2],
        column=arr_col,
        value=json.dumps(["see CIRCULAR appendix"]),
    )
    return dataset, rows, arr_col


@pytest.mark.django_db
def test_dataset_table_array_contains_list_value(array_col_seed):
    """The UI sends ``filter_value`` as a list (``["CIRCULAR"]``) for array
    columns. ``contains`` must match the same rows a scalar text search does —
    it must not stringify the list into a Python repr that can never match.
    """
    dataset, rows, arr_col = array_col_seed

    # Sanity: the data and search term are fine — a scalar text search matches.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "text", "contains", "CIRCULAR")],
        [arr_col],
    ) == [rows[0], rows[2]]

    # The array-typed list payload the UI actually sends must match the same rows.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "contains", ["CIRCULAR"])],
        [arr_col],
    ) == [rows[0], rows[2]]


@pytest.mark.django_db
def test_dataset_table_array_contains_not_contains_and_multi_term(array_col_seed):
    """not_contains is the exact complement of contains, and a multi-element
    list matches on any element (OR)."""
    dataset, rows, arr_col = array_col_seed

    # not_contains ["CIRCULAR"] → the rows contains did NOT return.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "not_contains", ["CIRCULAR"])],
        [arr_col],
    ) == [rows[1]]

    # multi-element list → any element matches (OR): "memo" hits row 1 only,
    # "CIRCULAR" hits rows 0 and 2 → union of all three rows.
    assert _apply(
        dataset,
        [_filter(arr_col.id, "array", "contains", ["memo", "CIRCULAR"])],
        [arr_col],
    ) == [rows[0], rows[1], rows[2]]


@pytest.mark.django_db
def test_dataset_table_none_value_contains_is_noop(dataset_filter_seed):
    """A text ``contains`` sent without a filter_value degrades to a no-op
    (matches every row) instead of matching the literal repr of ``None``. Pins
    the unified None handling that ``or_text_filter_q`` shares across both sites.
    """
    dataset, rows, text_col, bool_col = dataset_filter_seed

    assert (
        _apply(
            dataset,
            [_filter(text_col.id, "text", "contains")],
            [text_col, bool_col],
        )
        == rows
    )


@pytest.mark.django_db
def test_filter_dataset_cells_array_contains_list_value(array_col_seed):
    """The annotation-queue cell filter (``_filter_dataset_cells``) has the same
    list-payload path and must match per-element, not the list's repr."""
    dataset, rows, arr_col = array_col_seed
    cells = Cell.objects.filter(column=arr_col)

    matched = _filter_dataset_cells(cells, "array", "contains", ["CIRCULAR"], "array")
    assert set(matched.values_list("row_id", flat=True)) == {rows[0].id, rows[2].id}

    # in/not_in keep exact-value membership (a separate branch, unchanged by the
    # fix) — it matches the whole stored cell value, not a substring.
    exact = _filter_dataset_cells(
        cells, "array", "in", [json.dumps(["see CIRCULAR appendix"])], "array"
    )
    assert set(exact.values_list("row_id", flat=True)) == {rows[2].id}


@pytest.fixture
def audio_col_seed(organization, workspace):
    """A dataset with an audio-typed column and a row for duration-filter tests."""
    dataset = Dataset.objects.create(
        name="Audio filter dataset",
        organization=organization,
        workspace=workspace,
    )
    audio_col = Column.objects.create(
        name="recording",
        data_type=DataTypeChoices.AUDIO.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    return dataset, row, audio_col


def test_audio_duration_filter_with_metadata(audio_col_seed):
    """Duration filter reads from column_metadata__audio_duration_seconds."""
    dataset, row, audio_col = audio_col_seed

    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/test.mp3",
        column_metadata={"audio_duration_seconds": 12.04},
    )

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "greater_than", 1)],
        [audio_col],
    )
    assert matched == [row]

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "greater_than", 100)],
        [audio_col],
    )
    assert matched == []

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "less_than", 5)],
        [audio_col],
    )
    assert matched == []

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "between", [10, 15])],
        [audio_col],
    )
    assert matched == [row]

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "between", [0, 5])],
        [audio_col],
    )
    assert matched == []


def test_audio_duration_filter_without_metadata_returns_empty(audio_col_seed):
    """A cell with no audio_duration_seconds → NULL numeric_value → no matches."""
    dataset, row, audio_col = audio_col_seed

    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/test.mp3",
        column_metadata={},
    )

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "greater_than", 1)],
        [audio_col],
    )
    assert matched == []

    matched = _apply(
        dataset,
        [_filter(audio_col.id, "number", "less_than", 1000)],
        [audio_col],
    )
    assert matched == []


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_stores_audio_duration(
    mock_upload, auth_client, audio_col_seed
):
    """update_cell_value/ persists audio_duration_seconds in column_metadata."""
    dataset, row, audio_col = audio_col_seed

    mock_upload.return_value = (
        "https://bucket/audio/new_upload.mp3",
        12.5,
    )

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="",
        column_metadata={},
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/new_upload.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.column_metadata == {"audio_duration_seconds": 12.5}


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_clears_audio_duration_on_empty(
    mock_upload, auth_client, audio_col_seed
):
    """Clearing an audio cell removes audio_duration_seconds from column_metadata."""
    dataset, row, audio_col = audio_col_seed

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/old.mp3",
        column_metadata={"audio_duration_seconds": 12.04},
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.value is None
    assert "audio_duration_seconds" not in (cell.column_metadata or {})
    mock_upload.assert_not_called()


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_preserves_existing_metadata_keys(
    mock_upload, auth_client, audio_col_seed
):
    """Non-audio keys in column_metadata survive an audio cell update."""
    dataset, row, audio_col = audio_col_seed

    mock_upload.return_value = (
        "https://bucket/audio/new_upload.mp3",
        8.0,
    )

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="",
        column_metadata={"embedding": True, "custom_key": "value"},
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/new_upload.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.column_metadata["audio_duration_seconds"] == 8.0
    assert cell.column_metadata["embedding"] is True
    assert cell.column_metadata["custom_key"] == "value"


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_handles_none_column_metadata(
    mock_upload, auth_client, audio_col_seed
):
    """A cell with column_metadata=None does not raise on update."""
    dataset, row, audio_col = audio_col_seed

    mock_upload.return_value = (
        "https://bucket/audio/new_upload.mp3",
        3.3,
    )

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="",
        column_metadata=None,
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/new_upload.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    cell.refresh_from_db()
    assert cell.column_metadata == {"audio_duration_seconds": 3.3}


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_reuses_cached_duration_for_same_url(
    mock_upload, auth_client, audio_col_seed
):
    """Re-saving the same URL passes cached duration so the file is not re-downloaded."""
    dataset, row, audio_col = audio_col_seed

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/same.mp3",
        column_metadata={"audio_duration_seconds": 9.99},
    )

    mock_upload.return_value = (
        "https://bucket/audio/same.mp3",
        9.99,
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/same.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK

    call_kwargs = mock_upload.call_args.kwargs
    assert call_kwargs["duration_seconds"] == 9.99

    cell.refresh_from_db()
    assert cell.column_metadata["audio_duration_seconds"] == 9.99


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_recomputes_duration_for_different_url(
    mock_upload, auth_client, audio_col_seed
):
    """Replacing the audio recomputes the duration instead of reusing the cached one."""
    dataset, row, audio_col = audio_col_seed

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/old.mp3",
        column_metadata={"audio_duration_seconds": 12.5},
    )

    mock_upload.return_value = (
        "https://bucket/audio/new.mp3",
        4.0,
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/new.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK

    call_kwargs = mock_upload.call_args.kwargs
    assert call_kwargs["duration_seconds"] is None

    cell.refresh_from_db()
    assert cell.value == "https://bucket/audio/new.mp3"
    assert cell.column_metadata["audio_duration_seconds"] == 4.0


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_skips_metadata_write_for_falsy_duration(
    mock_upload, auth_client, audio_col_seed
):
    """A 0 duration leaves no audio_duration_seconds key, matching the file-upload path."""
    dataset, row, audio_col = audio_col_seed

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="",
        column_metadata={"audio_duration_seconds": 12.5},
    )

    mock_upload.return_value = (
        "https://bucket/audio/zero.mp3",
        0,
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": "https://bucket/audio/zero.mp3",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK

    cell.refresh_from_db()
    assert "audio_duration_seconds" not in (cell.column_metadata or {})


@patch("model_hub.views.develop_dataset.upload_audio_to_s3_duration")
def test_update_cell_value_clears_cell_for_non_string_value(
    mock_upload, auth_client, audio_col_seed
):
    """A non-string new_value with no file upload degrades to an empty cell, not an error."""
    dataset, row, audio_col = audio_col_seed

    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=audio_col,
        value="https://bucket/audio/old.mp3",
        column_metadata={"audio_duration_seconds": 12.5},
    )

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(audio_col.id),
            "new_value": 12345,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK

    mock_upload.assert_not_called()

    cell.refresh_from_db()
    assert cell.value is None
    assert cell.status == CellStatus.PASS.value
    assert "audio_duration_seconds" not in (cell.column_metadata or {})
