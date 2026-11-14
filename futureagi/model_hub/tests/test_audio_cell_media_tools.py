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
