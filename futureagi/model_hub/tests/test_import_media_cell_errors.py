"""Row imports keep blanks empty and expose only stable media failure reasons."""

import base64
import csv
import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset
from model_hub.views.develop_dataset import GetDatasetTableView, _cell_upload_error
from tfc.utils import storage
from tfc.utils.error_codes import get_error_message
from tfc.utils.lazy_extras import _MissingExtra
from tfc.utils.media_tools import MediaToolUnavailable

VIEW = "model_hub.views.develop_dataset"
SENTINEL = "synthetic-internal-endpoint/bucket/private-object"


@pytest.fixture
def media_dataset(organization, workspace):
    dataset = Dataset.objects.create(
        name="Mixed spreadsheet media",
        organization=organization,
        workspace=workspace,
        dataset_config={"dataset_source_local": True},
    )
    for name, kind in (
        ("text", DataTypeChoices.TEXT),
        ("image", DataTypeChoices.IMAGE),
        ("audio", DataTypeChoices.AUDIO),
        ("document", DataTypeChoices.DOCUMENT),
    ):
        Column.objects.create(
            name=name,
            data_type=kind.value,
            source=SourceChoices.OTHERS.value,
            dataset=dataset,
        )
    with patch(
        f"{VIEW}.log_and_deduct_cost_for_resource_request",
        return_value=MagicMock(status="created"),
    ):
        yield dataset


def _file_upload(auth_client, dataset, file):
    return auth_client.post(
        "/model-hub/develops/add_rows_from_file/",
        {"file": file, "dataset_id": str(dataset.id)},
        format="multipart",
    )


def _rows_file(extension):
    rows = [
        ["text", "image", "audio"],
        ["blank", "", ""],
        ["valid", "https://media.example/good.png", "https://media.example/good.wav"],
        ["invalid", "not-an-image-url", "not-an-audio-url"],
    ]
    if extension == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        for row in rows:
            workbook.active.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        content = buffer.getvalue()
    else:
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        content = buffer.getvalue().encode()
    return SimpleUploadedFile(f"mixed.{extension}", content)


@pytest.mark.parametrize("extension", ["xlsx", "csv"])
def test_real_file_blank_and_populated_media_cells(
    auth_client, media_dataset, extension
):
    """Use the actual spreadsheet reader and validator, mocking only network/storage."""
    with (
        patch("model_hub.views.utils.utils.safe_fetch") as fetch,
        patch(
            f"{VIEW}.upload_image_to_s3", return_value="https://stored.example/good.png"
        ) as image,
        patch(
            f"{VIEW}.upload_audio_to_s3", return_value="https://stored.example/good.wav"
        ) as audio,
    ):
        fetch.return_value = MagicMock(
            status_code=200, headers={"Content-Type": "application/octet-stream"}
        )
        fetch.side_effect = lambda url, **kwargs: MagicMock(
            status_code=200,
            headers={"Content-Type": "application/octet-stream"},
            final_url=url,
        )
        response = _file_upload(auth_client, media_dataset, _rows_file(extension))

    assert response.status_code == 200, response.json()
    image.assert_called_once()
    audio.assert_called_once()
    for kind in ("image", "audio"):
        cells = list(
            Cell.objects.filter(dataset=media_dataset, column__name=kind).order_by(
                "row__order"
            )
        )
        assert len(cells) == 3
        blank, valid, invalid = cells
        assert (blank.status, blank.value, blank.value_infos) == (
            CellStatus.PASS.value,
            "",
            [],
        )
        assert valid.status == CellStatus.PASS.value
        assert (
            valid.value
            == f"https://stored.example/good.{'png' if kind == 'image' else 'wav'}"
        )
        assert valid.value_infos == []
        assert invalid.status == CellStatus.ERROR.value
        assert invalid.value is None
        assert json.loads(invalid.value_infos)["reason"]
    summary = GetDatasetTableView()._get_dataset_status(media_dataset)
    assert summary["failed_cells"] == 2  # supplied invalid values only, never blanks
    assert len(summary["reason"]) == 2


@pytest.mark.parametrize(
    "value,expected",
    [(None, ""), (float("nan"), ""), (pd.NA, ""), (0, "0"), (False, "False")],
)
def test_missing_scalar_normalization_preserves_falsy_values(
    auth_client, media_dataset, value, expected
):
    frame = pd.DataFrame({"text": ["row"], "audio": pd.Series([value], dtype=object)})
    with (
        patch(f"{VIEW}.FileProcessor.process_file", return_value=(frame, None)),
        patch(f"{VIEW}.upload_audio_to_s3") as upload,
    ):
        response = _file_upload(
            auth_client, media_dataset, SimpleUploadedFile("rows.xlsx", b"fixture")
        )
    assert response.status_code == 200, response.json()
    cell = Cell.objects.get(dataset=media_dataset, column__name="audio")
    assert (cell.status, cell.value, cell.value_infos) == (
        CellStatus.PASS.value,
        expected,
        [],
    )
    upload.assert_not_called()


def test_csv_literal_nan_is_a_supplied_value(auth_client, media_dataset):
    """Do not add string-based NA normalization on top of the reader's contract."""
    response = _file_upload(
        auth_client,
        media_dataset,
        SimpleUploadedFile("rows.csv", b"text,audio\nrow,nan\n"),
    )
    assert response.status_code == 200, response.json()
    cell = Cell.objects.get(dataset=media_dataset, column__name="audio")
    assert cell.status == CellStatus.ERROR.value
    assert cell.value is None


def _media_data(kind):
    if kind == "document":
        return (
            "data:text/plain;base64," + base64.b64encode(b"synthetic document").decode()
        )
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.mark.parametrize("kind", ["image", "document"])
@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError])
def test_real_storage_wrapper_uses_stable_reason(
    auth_client, media_dataset, kind, exception_type
):
    """Inject below the real upload helper: it may wrap infrastructure errors in ValueError."""
    with (
        patch.object(storage, "get_storage_client") as client,
        patch.object(storage, "ensure_bucket"),
    ):
        client.return_value.put_object.side_effect = exception_type(SENTINEL)
        response = auth_client.post(
            f"/model-hub/develops/{media_dataset.id}/add_rows/",
            {"rows": [{"cells": [{"column_name": kind, "value": _media_data(kind)}]}]},
            format="json",
        )
    assert response.status_code == 200, response.json()
    client.return_value.put_object.assert_called_once()
    cell = Cell.objects.get(dataset=media_dataset, column__name=kind)
    assert cell.status == CellStatus.ERROR.value
    assert cell.value is None
    assert json.loads(cell.value_infos) == {
        "reason": f"Could not store this {kind} value."
    }
    # The real API readback must not re-expose the wrapped diagnostic either.
    readback = auth_client.get(
        f"/model-hub/develops/{media_dataset.id}/get-dataset-table/"
    )
    assert readback.status_code == 200, readback.json()
    assert SENTINEL not in json.dumps(readback.json())
    assert f"Could not store this {kind} value." in json.dumps(readback.json())


@pytest.mark.parametrize(
    "code",
    [
        "INVALID_IMAGE",
        "INVALID_BASE64_STRING",
        "EMPTY_DATA",
        "UNSUPPORTED_AUDIO_FORMAT",
    ],
)
def test_known_validation_reasons_are_preserved(code):
    reason = get_error_message(code)
    assert _cell_upload_error(ValueError(reason), "image") == {"reason": reason}


def test_wrapped_missing_ffmpeg_uses_only_the_named_cause():
    missing = MediaToolUnavailable("audio format detection")
    wrapped = ValueError(SENTINEL)
    wrapped.__cause__ = missing
    assert _cell_upload_error(wrapped, "audio") == {
        "reason": str(missing),
        "code": "ffmpeg_unavailable",
    }


def test_missing_audio_extra_remains_actionable():
    try:
        _MissingExtra("av", "audio").open()
    except ImportError as exc:
        wrapped = ValueError(str(exc))
        wrapped.__cause__ = exc
    infos = _cell_upload_error(wrapped, "audio")
    assert "optional `audio` dependency group" in infos["reason"]
