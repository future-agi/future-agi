"""Editing an audio cell names a missing media component instead of blaming
the file or asking the user to try again later."""

import base64
import io
import json
import wave
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import CellStatus, DataTypeChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tfc.utils import storage
from tfc.utils.lazy_extras import _MissingExtra


def _wav_data_uri(seconds: float = 0.5) -> str:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x01\x00" * int(16_000 * seconds))
    return "data:audio/wav;base64," + base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def audio_cell(organization, workspace):
    dataset = Dataset.objects.create(
        name="Audio cells", organization=organization, workspace=workspace
    )
    column = Column.objects.create(
        name="recording",
        data_type=DataTypeChoices.AUDIO.value,
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=1)
    cell = Cell.objects.create(dataset=dataset, row=row, column=column, value=None)
    return dataset, row, column, cell


def _edit(auth_client, dataset, row, column):
    return auth_client.post(
        f"/model-hub/develops/{dataset.id}/update_cell_value/",
        {
            "row_id": str(row.id),
            "column_id": str(column.id),
            "new_value": _wav_data_uri(),
        },
        format="json",
    )


def _message(response) -> str:
    body = response.json()
    return str(body.get("message") or body.get("result") or body)


def test_audio_cell_without_ffmpeg_returns_400_naming_ffmpeg(
    auth_client, audio_cell, tmp_path, monkeypatch
):
    """An image built with FFMPEG_FLAVOR=none and the `audio` extra: PyAV
    reads the duration, then format detection needs ffmpeg."""
    dataset, row, column, cell = audio_cell
    monkeypatch.setenv("PATH", str(tmp_path))
    fake_av = MagicMock()
    fake_av.open.return_value = MagicMock(duration=500_000)

    with (
        patch.object(storage, "av", fake_av),
        patch.object(storage, "get_storage_client") as client,
    ):
        response = _edit(auth_client, dataset, row, column)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    message = _message(response)
    assert message.startswith("ffmpeg is not installed in this backend image")
    assert "FFMPEG_FLAVOR=minimal" in message
    assert "try again later" not in message.lower()
    client.assert_not_called()
    cell.refresh_from_db()
    assert cell.status == CellStatus.ERROR.value
    assert json.loads(cell.value_infos)["reason"] == message


def test_audio_cell_without_audio_extra_names_the_extra(auth_client, audio_cell):
    """The default image has no `audio` extra (PyAV): the duration step
    used to report every upload as a corrupted file."""
    dataset, row, column, cell = audio_cell

    with (
        patch.object(storage, "av", _MissingExtra("av", "audio")),
        patch.object(storage, "get_storage_client") as client,
    ):
        response = _edit(auth_client, dataset, row, column)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    message = _message(response)
    assert "optional `audio` dependency group" in message
    assert "corrupted" not in message
    client.assert_not_called()


# ---------------------------------------------------------------------------
# Row writes and uploads: a value that cannot be stored is an error cell (or
# item) with the reason and, for a missing ffmpeg, code "ffmpeg_unavailable".
# ---------------------------------------------------------------------------

FFMPEG_CODE = "ffmpeg_unavailable"


@pytest.fixture
def no_ffmpeg_with_audio_extra(tmp_path, monkeypatch):
    """FFMPEG_FLAVOR=none plus the `audio` extra: PyAV reads the duration,
    then format detection needs ffmpeg."""
    monkeypatch.setenv("PATH", str(tmp_path))
    fake_av = MagicMock()
    fake_av.open.return_value = MagicMock(duration=500_000)
    with (
        patch.object(storage, "av", fake_av),
        patch.object(storage, "get_storage_client") as client,
    ):
        yield client


@pytest.fixture
def no_row_billing():
    with patch(
        "model_hub.views.develop_dataset.log_and_deduct_cost_for_resource_request",
        return_value=MagicMock(status="created"),
    ):
        yield


def test_audio_cell_edit_without_ffmpeg_carries_the_code(
    auth_client, audio_cell, no_ffmpeg_with_audio_extra
):
    dataset, row, column, cell = audio_cell

    response = _edit(auth_client, dataset, row, column)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == FFMPEG_CODE
    cell.refresh_from_db()
    assert json.loads(cell.value_infos)["code"] == FFMPEG_CODE


def test_add_rows_without_ffmpeg_marks_the_audio_cell_with_the_named_error(
    auth_client, audio_cell, no_ffmpeg_with_audio_extra, no_row_billing
):
    dataset, _, column, _ = audio_cell

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_rows/",
        {"rows": [{"cells": [{"column_name": column.name, "value": _wav_data_uri()}]}]},
        format="json",
    )

    # The row is still added, as for any other bad cell value.
    assert response.status_code == status.HTTP_200_OK
    no_ffmpeg_with_audio_extra.assert_not_called()
    added = Cell.objects.exclude(row__order=1).get(dataset=dataset, column=column)
    assert added.status == CellStatus.ERROR.value
    assert added.value is None
    infos = json.loads(added.value_infos)
    assert infos["code"] == FFMPEG_CODE
    assert infos["reason"].startswith("ffmpeg is not installed in this backend image")
    assert "audio format detection" in infos["reason"]


def test_add_rows_other_upload_error_gets_a_fixed_reason(
    auth_client, audio_cell, no_row_billing
):
    dataset, _, column, _ = audio_cell

    with patch(
        "model_hub.views.develop_dataset.upload_audio_to_s3_duration",
        side_effect=RuntimeError("PutObject on s3://internal-bucket failed"),
    ):
        response = auth_client.post(
            f"/model-hub/develops/{dataset.id}/add_rows/",
            {"rows": [{"cells": [{"column_name": column.name, "value": "x"}]}]},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    added = Cell.objects.exclude(row__order=1).get(dataset=dataset, column=column)
    assert added.status == CellStatus.ERROR.value
    assert json.loads(added.value_infos) == {
        "reason": "Could not store this audio value."
    }


def test_add_rows_from_file_without_ffmpeg_marks_the_audio_cell(
    auth_client, audio_cell, no_row_billing
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from tfc.utils.media_tools import MediaToolUnavailable

    dataset, _, column, _ = audio_cell
    csv = SimpleUploadedFile(
        "rows.csv",
        f"{column.name}\nhttps://media.example/a.wav\n".encode(),
        content_type="text/csv",
    )

    with (
        patch("model_hub.views.develop_dataset.validate_file_url"),
        patch(
            "model_hub.views.develop_dataset.upload_audio_to_s3",
            side_effect=MediaToolUnavailable("audio format detection"),
        ),
    ):
        response = auth_client.post(
            "/model-hub/develops/add_rows_from_file/",
            {"file": csv, "dataset_id": str(dataset.id)},
            format="multipart",
        )

    assert response.status_code == status.HTTP_200_OK, response.json()
    added = Cell.objects.exclude(row__order=1).get(dataset=dataset, column=column)
    assert added.status == CellStatus.ERROR.value
    assert added.value is None
    infos = json.loads(added.value_infos)
    assert infos["code"] == FFMPEG_CODE
    assert infos["reason"].startswith("ffmpeg is not installed in this backend image")


def test_column_conversion_to_audio_without_ffmpeg_carries_the_code(audio_cell):
    from model_hub.views.develop_dataset import DatatypeConverter
    from tfc.utils.media_tools import MediaToolUnavailable

    dataset, _, _, cell = audio_cell
    cell.value = "https://media.example/a.wav"
    converter = DatatypeConverter.__new__(DatatypeConverter)
    converter.new_data_type = DataTypeChoices.AUDIO.value
    converter.dataset_id = str(dataset.id)

    with (
        patch("model_hub.views.develop_dataset.validate_file_url"),
        patch(
            "model_hub.views.develop_dataset.upload_audio_to_s3_duration",
            side_effect=MediaToolUnavailable("audio format detection"),
        ),
    ):
        result = converter._convert_single_cell(cell)

    assert result.status == CellStatus.ERROR.value
    assert result.value_infos["code"] == FFMPEG_CODE
    assert result.value_infos["reason"].startswith("Failed to upload audio: ffmpeg")


def test_upload_file_without_ffmpeg_names_the_error_and_code_per_item(
    auth_client, no_ffmpeg_with_audio_extra
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    wav = base64.b64decode(_wav_data_uri().split(",", 1)[1])
    response = auth_client.post(
        "/model-hub/upload-file/",
        {
            "type": "audio",
            "files": [SimpleUploadedFile("a.wav", wav, content_type="audio/wav")],
        },
        format="multipart",
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    (item,) = response.json()["result"]
    assert item["url"] is None
    assert item["code"] == FFMPEG_CODE
    assert item["error"].startswith("ffmpeg is not installed in this backend image")
