"""Regression coverage for ``ingest_dataset_rows_to_kb`` (#933 review follow-up).

``KBIndexer`` is mocked at the class boundary — the real indexer talks to S3
and an embedding service, neither available here — so these tests verify the
task's own row selection, per-row concatenation, and worker-pool reuse.
"""

import pytest

from model_hub.models.choices import (
    DataTypeChoices,
    DatasetSourceChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, KnowledgeBaseFile, Row
from model_hub.tasks.develop_dataset import ingest_dataset_rows_to_kb
from tfc.middleware.workspace_context import clear_workspace_context

MODULE = "model_hub.tasks.develop_dataset"


@pytest.fixture(autouse=True)
def _clear_workspace_context():
    """These tests query KnowledgeBaseFile.objects (workspace-scoped) against
    a KB created with no workspace — make sure no earlier test's thread-local
    workspace context is still set."""
    clear_workspace_context()
    yield
    clear_workspace_context()


def _dataset_with_rows(organization, rows):
    """``rows`` is a list of ``{column_name: value}`` dicts, one per row."""
    dataset = Dataset.objects.create(
        name="ingest-source-dataset",
        source=DatasetSourceChoices.BUILD.value,
        organization=organization,
    )
    column_names = list({name for row in rows for name in row})
    columns = {
        name: Column.objects.create(
            name=name,
            data_type=DataTypeChoices.TEXT.value,
            dataset=dataset,
            source=SourceChoices.OTHERS.value,
            status=StatusType.COMPLETED.value,
        )
        for name in column_names
    }
    for order, row_values in enumerate(rows):
        row = Row.objects.create(dataset=dataset, order=order)
        for name, value in row_values.items():
            Cell.objects.create(
                dataset=dataset, column=columns[name], row=row, value=value
            )
    return dataset, columns


@pytest.fixture
def kb(organization):
    return KnowledgeBaseFile.objects.create(
        organization=organization, name="ingest-target-kb"
    )


@pytest.mark.integration
class TestIngestDatasetRowsToKb:
    def test_concatenates_selected_columns_per_row_in_order(
        self, mocker, organization, kb
    ):
        dataset, columns = _dataset_with_rows(
            organization,
            [
                {"title": "Row One Title", "body": "Row one body text."},
                {"title": "Row Two Title", "body": "Row two body text."},
            ],
        )
        indexer = mocker.patch(f"{MODULE}.KBIndexer").return_value

        result = ingest_dataset_rows_to_kb(
            str(dataset.id),
            [str(columns["title"].id), str(columns["body"].id)],
            str(kb.id),
            str(organization.id),
        )

        assert result == {"indexed": 2, "errors": 0}
        texts = [call.args[0] for call in indexer.process_content.call_args_list]
        assert texts == [
            "Row One Title\n\nRow one body text.",
            "Row Two Title\n\nRow two body text.",
        ]
        kb.refresh_from_db()
        assert kb.status == StatusType.COMPLETED.value

    def test_shares_one_executor_across_all_rows(self, mocker, organization, kb):
        dataset, columns = _dataset_with_rows(
            organization,
            [{"body": "first"}, {"body": "second"}, {"body": "third"}],
        )
        indexer = mocker.patch(f"{MODULE}.KBIndexer").return_value

        ingest_dataset_rows_to_kb(
            str(dataset.id),
            [str(columns["body"].id)],
            str(kb.id),
            str(organization.id),
        )

        executors_used = {
            call.kwargs["executor"] for call in indexer.process_content.call_args_list
        }
        assert indexer.process_content.call_count == 3
        assert len(executors_used) == 1

    def test_rows_with_no_selected_column_content_are_skipped(
        self, mocker, organization, kb
    ):
        dataset, columns = _dataset_with_rows(
            organization,
            [{"body": "has content"}, {"body": ""}],
        )
        indexer = mocker.patch(f"{MODULE}.KBIndexer").return_value

        result = ingest_dataset_rows_to_kb(
            str(dataset.id),
            [str(columns["body"].id)],
            str(kb.id),
            str(organization.id),
        )

        assert result == {"indexed": 1, "errors": 0}
        assert indexer.process_content.call_count == 1
