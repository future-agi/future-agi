"""API tests for document-cell Link writes (#2433).

A bad link must be refused with a reason, must not be reported as success,
and must never delete the document already stored in the cell.
"""

import json
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.utils.document_link import (
    DOCUMENT_ADDRESS_NOT_A_DOCUMENT,
    DOCUMENT_ADDRESS_UNREACHABLE,
    DOCUMENT_NOT_A_WEB_ADDRESS,
)


EXISTING_URL = "https://storage.example.com/kept.pdf"
EXISTING_INFOS = json.dumps(
    {"document_url": EXISTING_URL, "document_name": "kept.pdf"}
)


@pytest.fixture
def document_cell(organization, workspace):
    dataset = Dataset.objects.create(
        name="Document link dataset",
        organization=organization,
        workspace=workspace,
    )
    column = Column.objects.create(
        name="doc",
        data_type=DataTypeChoices.DOCUMENT.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(
        dataset=dataset,
        row=row,
        column=column,
        value=EXISTING_URL,
        value_infos=EXISTING_INFOS,
        status=CellStatus.PASS.value,
    )
    return dataset, row, column, cell


def _post_link(auth_client, dataset, row, column, new_value):
    return auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(column.id),
            "new_value": new_value,
        },
        format="json",
    )


def _value_infos(cell):
    raw = cell.value_infos
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _assert_cell_unchanged(cell):
    cell.refresh_from_db()
    assert cell.value == EXISTING_URL
    assert _value_infos(cell).get("document_url") == EXISTING_URL
    assert cell.status == CellStatus.PASS.value


def test_invalid_non_url_is_refused_and_keeps_existing_document(
    auth_client, document_cell
):
    dataset, row, column, cell = document_cell

    response = _post_link(auth_client, dataset, row, column, "sssss")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert DOCUMENT_NOT_A_WEB_ADDRESS in (response.data.get("message") or "")
    _assert_cell_unchanged(cell)


def test_unreachable_url_is_refused_and_keeps_existing_document(
    auth_client, document_cell
):
    dataset, row, column, cell = document_cell
    unreachable = "https://example.com/missing.pdf"

    with patch(
        "model_hub.views.develop_dataset.upload_document_to_s3",
        side_effect=ValueError("ERROR_DOWNLOADING_DOCUMENT: Max retries exceeded"),
    ):
        response = _post_link(auth_client, dataset, row, column, unreachable)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert DOCUMENT_ADDRESS_UNREACHABLE in (response.data.get("message") or "")
    _assert_cell_unchanged(cell)


def test_non_document_url_is_refused_and_keeps_existing_document(
    auth_client, document_cell
):
    dataset, row, column, cell = document_cell
    html_url = "https://example.com/index.html"

    with patch(
        "model_hub.views.develop_dataset.upload_document_to_s3",
        side_effect=ValueError("Invalid document data (Content-Type: text/html)"),
    ):
        response = _post_link(auth_client, dataset, row, column, html_url)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert DOCUMENT_ADDRESS_NOT_A_DOCUMENT in (response.data.get("message") or "")
    _assert_cell_unchanged(cell)


def test_working_document_url_is_stored(auth_client, document_cell):
    dataset, row, column, cell = document_cell
    new_url = "https://cdn.example.com/fresh.pdf"
    stored_url = "https://storage.example.com/fresh.pdf"

    with patch(
        "model_hub.views.develop_dataset.upload_document_to_s3",
        return_value=stored_url,
    ) as mock_upload:
        response = _post_link(auth_client, dataset, row, column, new_url)

    assert response.status_code == status.HTTP_200_OK
    mock_upload.assert_called_once()
    cell.refresh_from_db()
    assert cell.value == stored_url
    assert cell.status == CellStatus.PASS.value
    infos = cell.value_infos
    if isinstance(infos, str):
        infos = json.loads(infos)
    assert infos.get("document_url") == stored_url
