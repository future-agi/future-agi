"""
Tests for media column status handling during CSV file upload.

This test module verifies that audio/image/document columns are NOT marked
as completed immediately after cell creation, but remain with RUNNING status
until process_media_batch finishes processing all cells.

Bug Reference: docs/bugs/audio-column-status-inconsistency.md
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.datasets.create.file_upload import handle_columns_bulk


@pytest.fixture(autouse=True)
def mock_close_old_connections():
    """
    Mock close_old_connections to prevent it from closing the test database connection.

    handle_columns_bulk calls close_old_connections() which is meant for background tasks
    (Celery/Temporal) to clean up stale connections. In a test context, this closes the
    active test database connection, causing OperationalError: the connection is closed.
    """
    with patch("model_hub.views.datasets.create.file_upload.close_old_connections"):
        yield


@pytest.fixture
def dataset(db, organization, workspace):
    """Create a test dataset."""
    return Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def text_column(db, dataset):
    """Create a text column."""
    return Column.objects.create(
        id=uuid.uuid4(),
        name="text_column",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def audio_column(db, dataset):
    """Create an audio column."""
    return Column.objects.create(
        id=uuid.uuid4(),
        name="audio_column",
        data_type=DataTypeChoices.AUDIO.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def image_column(db, dataset):
    """Create an image column."""
    return Column.objects.create(
        id=uuid.uuid4(),
        name="image_column",
        data_type=DataTypeChoices.IMAGE.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def document_column(db, dataset):
    """Create a document column."""
    return Column.objects.create(
        id=uuid.uuid4(),
        name="document_column",
        data_type=DataTypeChoices.DOCUMENT.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def images_column(db, dataset):
    """Create an images column (multiple images)."""
    return Column.objects.create(
        id=uuid.uuid4(),
        name="images_column",
        data_type=DataTypeChoices.IMAGES.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


@pytest.fixture
def rows(db, dataset):
    """Create test rows."""
    row1 = Row.objects.create(id=uuid.uuid4(), dataset=dataset, order=0)
    row2 = Row.objects.create(id=uuid.uuid4(), dataset=dataset, order=1)
    return {0: str(row1.id), 1: str(row2.id)}


class TestHandleColumnsBulkMediaStatus:
    """Tests for handle_columns_bulk media column status behavior."""

    def test_text_column_marked_completed(self, dataset, text_column, rows):
        """Text columns should be marked as COMPLETED after cell creation."""
        data = {"text_column": {0: "value1", 1: "value2"}}
        column_mapping = {"text_column": str(text_column.id)}

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        text_column.refresh_from_db()

        assert completed == 1
        assert errors == 0
        assert text_column.status == StatusType.COMPLETED.value

        # Verify cells have PASS status
        cells = Cell.objects.filter(column=text_column)
        assert cells.count() == 2
        for cell in cells:
            assert cell.status == CellStatus.PASS.value

    def test_audio_column_not_marked_completed(self, dataset, audio_column, rows):
        """Audio columns should NOT be marked as COMPLETED - they remain RUNNING."""
        data = {
            "audio_column": {
                0: "https://example.com/audio1.mp3",
                1: "https://example.com/audio2.mp3",
            }
        }
        column_mapping = {"audio_column": str(audio_column.id)}

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        audio_column.refresh_from_db()

        # Audio column should NOT be in completed count
        assert completed == 0
        assert errors == 0
        # Audio column should remain with RUNNING status
        assert audio_column.status == StatusType.RUNNING.value

        # Verify cells have RUNNING status (awaiting media processing)
        cells = Cell.objects.filter(column=audio_column)
        assert cells.count() == 2
        for cell in cells:
            assert cell.status == CellStatus.RUNNING.value

    def test_image_column_not_marked_completed(self, dataset, image_column, rows):
        """Image columns should NOT be marked as COMPLETED - they remain RUNNING."""
        data = {
            "image_column": {
                0: "https://example.com/img1.png",
                1: "https://example.com/img2.png",
            }
        }
        column_mapping = {"image_column": str(image_column.id)}

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        image_column.refresh_from_db()

        # Image column should NOT be in completed count
        assert completed == 0
        assert errors == 0
        # Image column should remain with RUNNING status
        assert image_column.status == StatusType.RUNNING.value

        # Verify cells have RUNNING status
        cells = Cell.objects.filter(column=image_column)
        assert cells.count() == 2
        for cell in cells:
            assert cell.status == CellStatus.RUNNING.value

    def test_document_column_not_marked_completed(self, dataset, document_column, rows):
        """Document columns should NOT be marked as COMPLETED - they remain RUNNING."""
        data = {
            "document_column": {
                0: "https://example.com/doc1.pdf",
                1: "https://example.com/doc2.pdf",
            }
        }
        column_mapping = {"document_column": str(document_column.id)}

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        document_column.refresh_from_db()

        # Document column should NOT be in completed count
        assert completed == 0
        assert errors == 0
        # Document column should remain with RUNNING status
        assert document_column.status == StatusType.RUNNING.value

        # Verify cells have RUNNING status
        cells = Cell.objects.filter(column=document_column)
        assert cells.count() == 2
        for cell in cells:
            assert cell.status == CellStatus.RUNNING.value

    def test_images_column_not_marked_completed(self, dataset, images_column, rows):
        """Images columns (multiple images) should NOT be marked as COMPLETED - they remain RUNNING."""
        data = {
            "images_column": {
                0: "https://example.com/img1.png,https://example.com/img2.png",
                1: "https://example.com/img3.png,https://example.com/img4.png",
            }
        }
        column_mapping = {"images_column": str(images_column.id)}

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        images_column.refresh_from_db()

        # Images column should NOT be in completed count
        assert completed == 0
        assert errors == 0
        # Images column should remain with RUNNING status
        assert images_column.status == StatusType.RUNNING.value

        # Verify cells have RUNNING status
        cells = Cell.objects.filter(column=images_column)
        assert cells.count() == 2
        for cell in cells:
            assert cell.status == CellStatus.RUNNING.value

    def test_mixed_columns_correct_status(
        self, dataset, text_column, audio_column, rows
    ):
        """Mixed columns: text should be COMPLETED, audio should remain RUNNING."""
        data = {
            "text_column": {0: "text1", 1: "text2"},
            "audio_column": {
                0: "https://example.com/audio1.mp3",
                1: "https://example.com/audio2.mp3",
            },
        }
        column_mapping = {
            "text_column": str(text_column.id),
            "audio_column": str(audio_column.id),
        }

        completed, errors = handle_columns_bulk(
            str(dataset.id), data, column_mapping, rows
        )

        # Refresh from database
        text_column.refresh_from_db()
        audio_column.refresh_from_db()

        # Only text column should be counted as completed
        assert completed == 1
        assert errors == 0

        # Text column should be COMPLETED
        assert text_column.status == StatusType.COMPLETED.value

        # Audio column should remain RUNNING
        assert audio_column.status == StatusType.RUNNING.value

        # Verify text cells have PASS status
        text_cells = Cell.objects.filter(column=text_column)
        for cell in text_cells:
            assert cell.status == CellStatus.PASS.value

        # Verify audio cells have RUNNING status
        audio_cells = Cell.objects.filter(column=audio_column)
        for cell in audio_cells:
            assert cell.status == CellStatus.RUNNING.value


class TestMediaColumnStatusConsistency:
    """Tests to verify column status consistency with cell status."""

    def test_audio_column_status_matches_cell_status(self, dataset, audio_column, rows):
        """
        Verify that when cells have RUNNING status, column also has RUNNING status.
        This is the main bug that was fixed.
        """
        data = {
            "audio_column": {
                0: "https://example.com/audio.mp3",
                1: "https://example.com/audio2.mp3",
            }
        }
        column_mapping = {"audio_column": str(audio_column.id)}

        handle_columns_bulk(str(dataset.id), data, column_mapping, rows)

        # Refresh from database
        audio_column.refresh_from_db()

        # Get cell statuses
        cells = Cell.objects.filter(column=audio_column)
        cell_statuses = set(cell.status for cell in cells)

        # If any cell is RUNNING, column should also be RUNNING (not COMPLETED)
        if CellStatus.RUNNING.value in cell_statuses:
            assert audio_column.status == StatusType.RUNNING.value, (
                "Bug: Column status is COMPLETED while cells are still RUNNING. "
                "Column status should match cell processing state."
            )
