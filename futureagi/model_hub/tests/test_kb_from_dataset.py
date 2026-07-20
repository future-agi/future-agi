"""
Tests for "Create Knowledge Base from Dataset" feature (Issue #933).

Run with: pytest model_hub/tests/test_kb_from_dataset.py -v
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase

from model_hub.models.choices import StatusType


@pytest.mark.django_db
class TestDatasetBridge:
    """Tests for dataset_rows_to_documents in kb_dataset_bridge.py."""

    def _make_mock_dataset(self, rows, column_names=None):
        """Helper to build a mock Dataset with Columns, Rows, and Cells."""
        dataset = MagicMock()
        dataset.id = uuid.uuid4()
        dataset.name = "Test Dataset"

        column_map = {}
        mock_columns = []
        for i, name in enumerate(column_names or ["title", "body"]):
            col = MagicMock()
            col.id = uuid.uuid4()
            col.name = name
            col.dataset = dataset
            mock_columns.append(col)
            column_map[col.id] = col

        mock_rows = []
        mock_cells = []
        for ri, row_data in enumerate(rows):
            row = MagicMock()
            row.id = uuid.uuid4()
            row.dataset = dataset
            row.order = ri
            mock_rows.append(row)
            for col_id, col in column_map.items():
                if col.name in row_data:
                    cell = MagicMock()
                    cell.id = uuid.uuid4()
                    cell.dataset = dataset
                    cell.column = col
                    cell.column_id = col_id
                    cell.row = row
                    cell.row_id = row.id
                    cell.value = row_data[col.name]
                    mock_cells.append(cell)

        return dataset, mock_columns, mock_rows, mock_cells

    @patch("model_hub.utils.kb_dataset_bridge.Column.objects.filter")
    @patch("model_hub.utils.kb_dataset_bridge.Cell.objects.filter")
    @patch("model_hub.utils.kb_dataset_bridge.Row.objects.filter")
    def test_basic_serialisation(self, mock_row_filter, mock_cell_filter, mock_col_filter):
        from model_hub.utils.kb_dataset_bridge import dataset_rows_to_documents

        dataset, mock_columns, mock_rows, mock_cells = self._make_mock_dataset(
            rows=[
                {"title": "Hello", "body": "World"},
                {"title": "Foo", "body": "Bar"},
            ],
            column_names=["title", "body"],
        )

        mock_col_filter.return_value = mock_columns
        mock_cell_filter.return_value.select_related.return_value.iterator.return_value = iter(mock_cells)
        mock_row_filter.return_value.order_by.return_value = mock_rows

        docs = list(dataset_rows_to_documents(dataset, columns=["title", "body"]))
        assert len(docs) == 2
        assert "title: Hello" in docs[0][0]
        assert "body: World" in docs[0][0]
        assert "title: Foo" in docs[1][0]

    @patch("model_hub.utils.kb_dataset_bridge.Column.objects.filter")
    @patch("model_hub.utils.kb_dataset_bridge.Cell.objects.filter")
    @patch("model_hub.utils.kb_dataset_bridge.Row.objects.filter")
    def test_skips_empty_rows(self, mock_row_filter, mock_cell_filter, mock_col_filter):
        from model_hub.utils.kb_dataset_bridge import dataset_rows_to_documents

        dataset, mock_columns, mock_rows, mock_cells = self._make_mock_dataset(
            rows=[
                {"title": "", "body": ""},
                {"title": "Real", "body": "Content"},
            ],
            column_names=["title", "body"],
        )

        mock_col_filter.return_value = mock_columns
        mock_cell_filter.return_value.select_related.return_value.iterator.return_value = iter(mock_cells)
        mock_row_filter.return_value.order_by.return_value = mock_rows

        docs = list(dataset_rows_to_documents(dataset, columns=["title", "body"]))
        assert len(docs) == 1
        assert "Real" in docs[0][0]

    @patch("model_hub.utils.kb_dataset_bridge.Column.objects.filter")
    def test_invalid_columns_raises(self, mock_col_filter):
        from model_hub.utils.kb_dataset_bridge import dataset_rows_to_documents

        dataset = MagicMock()
        dataset.name = "Test"
        mock_col_filter.return_value = []

        with pytest.raises(ValueError, match="None of the requested columns"):
            list(dataset_rows_to_documents(dataset, columns=["nonexistent"]))

    def test_empty_columns_raises(self):
        from model_hub.utils.kb_dataset_bridge import dataset_rows_to_documents

        dataset = MagicMock()
        with pytest.raises(ValueError, match="At least one column"):
            list(dataset_rows_to_documents(dataset, columns=[]))


@pytest.mark.django_db
class TestDatasetColumnsView:
    """Tests for the DatasetColumnsView endpoint."""

    def test_get_columns(self):
        from model_hub.models.develop_dataset import Column, Dataset

        dataset = Dataset.objects.create(name="test-ds")
        Column.objects.create(name="col_a", dataset=dataset, source="user_input")
        Column.objects.create(name="col_b", dataset=dataset, source="user_input")

        from model_hub.utils.kb_dataset_bridge import get_dataset_column_names

        cols = get_dataset_column_names(dataset)
        assert "col_a" in cols
        assert "col_b" in cols
        assert len(cols) == 2


class TestIngestDocumentsFromMemory:
    """Tests for KBIndexer.process_documents_from_memory."""

    @patch("model_hub.utils.kb_indexer.KBIndexer")
    def test_ingest_kb_from_memory(self, mock_indexer_class):
        from model_hub.utils.kb_indexer import ingest_kb_from_memory

        mock_indexer = MagicMock()
        mock_indexer_class.return_value = mock_indexer
        mock_indexer.process_documents_from_memory.return_value = [
            {"doc_index": 0, "vectors": 5, "metadata": {}}
        ]

        documents = [("text content", {"dataset_id": "ds-1", "row_index": 0})]
        result = ingest_kb_from_memory(documents, "kb-1", "org-1")

        assert len(result) == 1
        assert result[0]["vectors"] == 5
        mock_indexer.process_documents_from_memory.assert_called_once_with(
            documents=documents,
            kb_id="kb-1",
            organization_id="org-1",
        )


@pytest.mark.django_db
class TestCreateKBFromDatasetAPI:
    """Tests for CreateKnowledgeBaseView with dataset source."""

    @patch("model_hub.views.develop_dataset.Dataset.objects.get")
    @patch("model_hub.views.develop_dataset.ingest_kb_from_dataset")
    def test_create_kb_from_dataset(
        self, mock_ingest_task, mock_dataset_get
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile

        dataset_id = uuid.uuid4()
        mock_dataset = MagicMock()
        mock_dataset.id = dataset_id
        mock_dataset_get.return_value = mock_dataset

        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        request = MagicMock()
        request.data = {
            "name": "My KB from Dataset",
            "dataset_id": str(dataset_id),
            "columns": ["col_a", "col_b"],
        }
        request.FILES.getlist.return_value = []
        request.headers.get.return_value = None

        user = MagicMock()
        user.id = 1
        user.name = "Test User"
        request.user = user
        request.organization = MagicMock()
        request.organization.id = 1

        with patch.object(view, "_generate_unique_name", return_value="KB-1"):
            with patch.object(view, "_gm") as mock_gm:
                mock_gm.success_response.return_value = MagicMock(status_code=201)
                response = view.post(request)

        assert response.status_code == 201
        kb = KnowledgeBaseFile.objects.filter(name="My KB from Dataset").first()
        assert kb is not None
        assert kb.source_dataset_id == dataset_id
        assert kb.source_dataset_columns == ["col_a", "col_b"]
        mock_ingest_task.delay.assert_called_once()

    def test_dataset_not_found(self):
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        request = MagicMock()
        request.data = {
            "name": "KB",
            "dataset_id": str(uuid.uuid4()),
            "columns": ["col_a"],
        }
        request.FILES.getlist.return_value = []
        request.headers.get.return_value = None
        user = MagicMock()
        user.id = 1
        request.user = user
        request.organization = MagicMock()
        request.organization.id = 1

        with patch.object(view, "_gm") as mock_gm:
            mock_gm.bad_request.return_value = MagicMock(status_code=400)
            response = view.post(request)

        assert response.status_code == 400

    def test_missing_columns_rejected(self):
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        request = MagicMock()
        request.data = {
            "name": "KB",
            "dataset_id": str(uuid.uuid4()),
        }
        request.FILES.getlist.return_value = []
        request.headers.get.return_value = None
        user = MagicMock()
        user.id = 1
        request.user = user
        request.organization = MagicMock()
        request.organization.id = 1

        with patch.object(view, "_gm") as mock_gm:
            mock_gm.bad_request.return_value = MagicMock(status_code=400)
            response = view.post(request)

        assert response.status_code == 400

    def test_both_file_and_dataset_rejected(self):
        from model_hub.views.develop_dataset import CreateKnowledgeBaseView

        view = CreateKnowledgeBaseView()
        request = MagicMock()
        request.data = {
            "name": "KB",
            "dataset_id": str(uuid.uuid4()),
        }
        request.FILES.getlist.return_value = [MagicMock()]
        request.headers.get.return_value = None
        user = MagicMock()
        user.id = 1
        request.user = user
        request.organization = MagicMock()
        request.organization.id = 1

        with patch.object(view, "_gm") as mock_gm:
            mock_gm.bad_request.return_value = MagicMock(status_code=400)
            response = view.post(request)

        assert response.status_code == 400


@pytest.mark.django_db
class TestIngestKbFromDatasetTask:
    """Tests for the ingest_kb_from_dataset Temporal activity."""

    @patch("model_hub.tasks.develop_dataset.Dataset.objects.get")
    @patch("model_hub.tasks.develop_dataset.KnowledgeBaseFile.objects.get")
    @patch("model_hub.tasks.develop_dataset.dataset_rows_to_documents")
    @patch("model_hub.tasks.develop_dataset.ingest_kb_from_memory")
    @patch("model_hub.tasks.develop_dataset.is_kb_deleted_or_cancelled")
    def test_successful_ingestion(
        self,
        mock_is_deleted,
        mock_ingest_memory,
        mock_rows_to_docs,
        mock_kb_get,
        mock_ds_get,
    ):
        from model_hub.tasks.develop_dataset import ingest_kb_from_dataset

        mock_is_deleted.return_value = False
        mock_ds_get.return_value = MagicMock()
        mock_kb = MagicMock()
        mock_kb_get.return_value = mock_kb
        mock_rows_to_docs.return_value = [
            ("text1", {"row_index": 0}),
            ("text2", {"row_index": 1}),
        ]
        mock_ingest_memory.return_value = [
            {"doc_index": 0, "vectors": 3, "metadata": {}},
            {"doc_index": 1, "vectors": 2, "metadata": {}},
        ]

        result = ingest_kb_from_dataset(
            kb_id="kb-1", dataset_id="ds-1", columns=["col_a"], org="org-1"
        )

        assert result["status"] == "success"
        assert result["documents_indexed"] == 2
        assert result["vectors"] == 5
        assert mock_kb.status == StatusType.COMPLETED.value
        mock_kb.save.assert_called()

    @patch("model_hub.tasks.develop_dataset.is_kb_deleted_or_cancelled")
    def test_cancelled_kb_skips(self, mock_is_deleted):
        from model_hub.tasks.develop_dataset import ingest_kb_from_dataset

        mock_is_deleted.return_value = True

        result = ingest_kb_from_dataset(
            kb_id="kb-1", dataset_id="ds-1", columns=["col_a"], org="org-1"
        )

        assert result is None
