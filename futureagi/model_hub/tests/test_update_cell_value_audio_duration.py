"""
Behavioral test for audio-duration persistence when editing an audio cell.

Bug #1767: editing an audio cell recomputed the clip duration but threw it
away, leaving ``Cell.column_metadata`` at its default ``{}`` so the audio
duration filter had nothing to match against. ``UpdateCellValueView.post`` now
stores the freshly computed duration under
``column_metadata["audio_duration_seconds"]``.

These tests drive the real endpoint (``POST .../update_cell_value/``) with the
S3 upload mocked, so they fail against the pre-fix view and pass with it.
"""

import uuid
from unittest.mock import patch

import pytest

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

UPLOAD_TARGET = "model_hub.views.develop_dataset.upload_audio_to_s3_duration"


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="Audio Duration Dataset",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def audio_column(db, dataset):
    return Column.objects.create(
        id=uuid.uuid4(),
        name="audio_column",
        data_type=DataTypeChoices.AUDIO.value,
        # "others" is an editable source, so the edit endpoint accepts it.
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def audio_cell(db, dataset, audio_column):
    row = Row.objects.create(id=uuid.uuid4(), dataset=dataset, order=0)
    return Cell.objects.create(
        id=uuid.uuid4(),
        dataset=dataset,
        column=audio_column,
        row=row,
        status=CellStatus.PASS.value,
    )


def _url(dataset_id):
    return f"/model-hub/develops/{dataset_id}/update_cell_value/"


@pytest.mark.django_db
class TestAudioCellDurationPersisted:
    def test_edit_persists_computed_audio_duration(
        self, auth_client, dataset, audio_column, audio_cell
    ):
        # A freshly created audio cell carries no duration metadata.
        assert audio_cell.column_metadata == {}

        with patch(
            UPLOAD_TARGET,
            return_value=("https://example.com/audio/edited.mp3", 12.5),
        ) as mock_upload:
            resp = auth_client.post(
                _url(dataset.id),
                {
                    "row_id": str(audio_cell.row_id),
                    "column_id": str(audio_column.id),
                    "new_value": "data:audio/mp3;base64,QUJD",
                },
                format="json",
            )

        assert resp.status_code == 200, resp.content
        assert mock_upload.called

        audio_cell.refresh_from_db()
        # Pre-fix, this stayed {} because the computed duration was discarded.
        assert audio_cell.column_metadata.get("audio_duration_seconds") == 12.5
        assert audio_cell.value == "https://example.com/audio/edited.mp3"

    def test_edit_leaves_metadata_untouched_when_duration_unknown(
        self, auth_client, dataset, audio_column, audio_cell
    ):
        # When the uploader cannot determine a duration, we must not write a
        # null/garbage entry the duration filter would then trip over.
        with patch(
            UPLOAD_TARGET,
            return_value=("https://example.com/audio/edited.mp3", None),
        ):
            resp = auth_client.post(
                _url(dataset.id),
                {
                    "row_id": str(audio_cell.row_id),
                    "column_id": str(audio_column.id),
                    "new_value": "data:audio/mp3;base64,QUJD",
                },
                format="json",
            )

        assert resp.status_code == 200, resp.content

        audio_cell.refresh_from_db()
        assert "audio_duration_seconds" not in (audio_cell.column_metadata or {})
