import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pdfplumber
import pytest

from model_hub.models.choices import CellStatus, DataTypeChoices

MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n"
    b"<< /Type /Catalog /Pages 2 0 R >>\n"
    b"endobj\n"
    b"2 0 obj\n"
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
    b"endobj\n"
    b"3 0 obj\n"
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
    b"/Resources << /Font << /F1 5 0 R >> >> >>\n"
    b"endobj\n"
    b"4 0 obj\n"
    b"<< /Length 44 >>\n"
    b"stream\n"
    b"BT /F1 12 Tf 72 120 Td (Hello PDF) Tj ET\n"
    b"endstream\n"
    b"endobj\n"
    b"5 0 obj\n"
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
    b"endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000241 00000 n \n"
    b"0000000335 00000 n \n"
    b"trailer\n"
    b"<< /Root 1 0 R /Size 6 >>\n"
    b"startxref\n"
    b"405\n"
    b"%%EOF\n"
)


def test_pdf_feature_maps_to_document_data_type():
    from model_hub.utils.utils import get_data_type_huggingface

    assert (
        get_data_type_huggingface({"name": "paper", "type": {"_type": "Pdf"}})
        == DataTypeChoices.DOCUMENT.value
    )
    assert (
        get_data_type_huggingface({"name": "paper", "type": {"dtype": "pdf"}})
        == DataTypeChoices.DOCUMENT.value
    )


def test_document_path_dict_rejects_local_file_without_bytes(tmp_path):
    from model_hub.views.utils.hugginface import (
        _coerce_huggingface_document_for_upload,
    )

    pdf_path = tmp_path / "local-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nfrom disk")

    with pytest.raises(ValueError, match="did not include document bytes"):
        _coerce_huggingface_document_for_upload(
            {"path": str(pdf_path)}, fallback_name="paper.pdf"
        )


def test_document_path_dict_allows_remote_urls():
    from model_hub.views.utils.hugginface import (
        _coerce_huggingface_document_for_upload,
    )

    upload_input, document_name = _coerce_huggingface_document_for_upload(
        {"path": "https://example.com/local-paper.pdf"}, fallback_name="paper.pdf"
    )

    assert upload_input == "https://example.com/local-paper.pdf"
    assert document_name == "local-paper.pdf"


@patch("model_hub.views.utils.hugginface.upload_document_to_s3")
@patch("model_hub.views.utils.hugginface.Cell.objects.create")
@patch("model_hub.views.utils.hugginface.Dataset.objects.get")
@patch("model_hub.views.utils.hugginface.Column.objects.get")
@patch("model_hub.views.utils.hugginface.close_old_connections")
def test_process_columns_uploads_decoded_pdf_object(
    mock_close_connections,
    mock_column_get,
    mock_dataset_get,
    mock_cell_create,
    mock_upload_document,
):
    from model_hub.views.utils.hugginface import process_huggingface_columns

    decoded_pdf = pdfplumber.open(BytesIO(MINIMAL_PDF_BYTES))
    column = SimpleNamespace(id="column-id", name="paper", data_type="document")
    dataset = SimpleNamespace(id="dataset-id", organization_id="org-id")
    mock_column_get.return_value = column
    mock_dataset_get.return_value = dataset
    mock_upload_document.return_value = "https://cdn.example.com/decoded.pdf"

    try:
        process_huggingface_columns(
            data_dict={"paper": [decoded_pdf]},
            dataset_id="dataset-id",
            column_id="column-id",
            rows={"0": "row-id"},
            index=0,
        )
    finally:
        decoded_pdf.close()

    mock_upload_document.assert_called_once()
    upload_input = mock_upload_document.call_args.args[0]
    assert upload_input.startswith("data:application/pdf;base64,")
    assert base64.b64decode(upload_input.split(",", 1)[1]) == MINIMAL_PDF_BYTES

    mock_cell_create.assert_called_once()
    cell_kwargs = mock_cell_create.call_args.kwargs
    assert cell_kwargs["value"] == "https://cdn.example.com/decoded.pdf"
    assert cell_kwargs["status"] == CellStatus.PASS.value
    assert json.loads(cell_kwargs["value_infos"]) == {
        "document_url": "https://cdn.example.com/decoded.pdf",
        "document_name": "paper.pdf",
    }


@patch("model_hub.views.utils.hugginface.upload_document_to_s3")
@patch("model_hub.views.utils.hugginface.Cell.objects.create")
@patch("model_hub.views.utils.hugginface.Dataset.objects.get")
@patch("model_hub.views.utils.hugginface.Column.objects.get")
@patch("model_hub.views.utils.hugginface.close_old_connections")
def test_process_columns_uploads_document_bytes(
    mock_close_connections,
    mock_column_get,
    mock_dataset_get,
    mock_cell_create,
    mock_upload_document,
):
    from model_hub.views.utils.hugginface import process_huggingface_columns

    column = SimpleNamespace(id="column-id", name="paper", data_type="document")
    dataset = SimpleNamespace(id="dataset-id", organization_id="org-id")
    mock_column_get.return_value = column
    mock_dataset_get.return_value = dataset
    mock_upload_document.return_value = "https://cdn.example.com/paper.pdf"

    pdf_bytes = b"%PDF-1.4\nexample"

    process_huggingface_columns(
        data_dict={"paper": [{"bytes": pdf_bytes, "path": "paper.pdf"}]},
        dataset_id="dataset-id",
        column_id="column-id",
        rows={"0": "row-id"},
        index=0,
    )

    mock_upload_document.assert_called_once()
    upload_input = mock_upload_document.call_args.args[0]
    assert upload_input.startswith("data:application/pdf;base64,")
    assert base64.b64decode(upload_input.split(",", 1)[1]) == pdf_bytes
    assert mock_upload_document.call_args.kwargs["object_key"].startswith(
        "documents/dataset-id/"
    )
    assert mock_upload_document.call_args.kwargs["org_id"] == "org-id"

    mock_cell_create.assert_called_once()
    cell_kwargs = mock_cell_create.call_args.kwargs
    assert cell_kwargs["dataset"] is dataset
    assert cell_kwargs["column"] is column
    assert cell_kwargs["row_id"] == "row-id"
    assert cell_kwargs["value"] == "https://cdn.example.com/paper.pdf"
    assert cell_kwargs["status"] == CellStatus.PASS.value
    assert json.loads(cell_kwargs["value_infos"]) == {
        "document_url": "https://cdn.example.com/paper.pdf",
        "document_name": "paper.pdf",
    }


@patch("model_hub.views.utils.hugginface.upload_document_to_s3")
@patch("model_hub.views.utils.hugginface.Cell.objects.create")
@patch("model_hub.views.utils.hugginface.Dataset.objects.get")
@patch("model_hub.views.utils.hugginface.Column.objects.get")
@patch("model_hub.views.utils.hugginface.close_old_connections")
def test_process_columns_marks_document_upload_errors(
    mock_close_connections,
    mock_column_get,
    mock_dataset_get,
    mock_cell_create,
    mock_upload_document,
):
    from model_hub.views.utils.hugginface import process_huggingface_columns

    column = SimpleNamespace(id="column-id", name="paper", data_type="document")
    dataset = SimpleNamespace(id="dataset-id", organization_id="org-id")
    mock_column_get.return_value = column
    mock_dataset_get.return_value = dataset
    mock_upload_document.side_effect = ValueError("invalid document")

    process_huggingface_columns(
        data_dict={"paper": [{"bytes": b"not a pdf", "path": "paper.pdf"}]},
        dataset_id="dataset-id",
        column_id="column-id",
        rows={"0": "row-id"},
        index=0,
    )

    mock_cell_create.assert_called_once()
    cell_kwargs = mock_cell_create.call_args.kwargs
    assert cell_kwargs["value"] == ""
    assert cell_kwargs["status"] == CellStatus.ERROR.value
    assert json.loads(cell_kwargs["value_infos"]) == {"reason": "invalid document"}
