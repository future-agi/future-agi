"""``ingest_files_to_s3`` must surface why a file could not be indexed.

A knowledge base built while model serving was down used to end up
"Completed" with an empty ``last_error``. The indexer now fails the file; these
tests pin that the activity records that as a failed file with its reason, and
that both KB endpoints the UI reads hand the reason back. ``remove_kb_files``
records a file's removal the same way, whatever shape its metadata is in.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Files, KnowledgeBaseFile
from model_hub.tasks.develop_dataset import (
    _file_metadata_with_error,
    ingest_files_to_s3,
    remove_kb_files,
)
from model_hub.utils.kb_indexer import KB_EMBEDDINGS_UNAVAILABLE_ERROR


@pytest.fixture
def knowledge_base(organization, workspace, user):
    kb = KnowledgeBaseFile.objects.create(
        name="Handbook",
        organization=organization,
        workspace=workspace,
        created_by=user.email,
    )
    kb_file = Files.objects.create(
        name="handbook.txt",
        metadata=json.dumps({"size": 14}),
        uploaded_url="https://example.com/handbook.txt",
    )
    kb.files.add(kb_file)
    return kb, kb_file


def _ingest_with_result(kb, kb_file, result):
    indexer = MagicMock()
    indexer.process_s3_file.return_value = result
    with (
        patch("model_hub.tasks.develop_dataset.KBIndexer", return_value=indexer),
        patch(
            "model_hub.tasks.develop_dataset.is_kb_deleted_or_cancelled",
            return_value=False,
        ),
    ):
        # The activity wrapper's close_old_connections() would close the test
        # transaction's connection; run the activity body directly.
        ingest_files_to_s3._original_func(
            {str(kb_file.id): "knowledge-base/handbook.txt"},
            str(kb.id),
            str(kb.organization_id),
        )


@pytest.mark.django_db
def test_file_that_could_not_be_embedded_fails_the_knowledge_base(
    auth_client, knowledge_base
):
    kb, kb_file = knowledge_base

    _ingest_with_result(
        kb,
        kb_file,
        {
            "file_id": str(kb_file.id),
            "kb_id": str(kb.id),
            "error": KB_EMBEDDINGS_UNAVAILABLE_ERROR,
        },
    )

    kb.refresh_from_db()
    kb_file.refresh_from_db()
    assert kb.status == StatusType.FAILED.value
    assert kb.last_error == KB_EMBEDDINGS_UNAVAILABLE_ERROR
    assert kb_file.status == StatusType.FAILED.value
    assert json.loads(kb_file.metadata) == {
        "size": 14,
        "error": KB_EMBEDDINGS_UNAVAILABLE_ERROR,
    }

    # Knowledge base list: the Failed status cell's tooltip reads `error`.
    response = auth_client.get("/model-hub/knowledge-base/get/")
    assert response.status_code == status.HTTP_200_OK
    row = next(
        row
        for row in response.json()["result"]["table_data"]
        if row["id"] == str(kb.id)
    )
    assert row["status"] == StatusType.FAILED.value
    assert row["error"] == KB_EMBEDDINGS_UNAVAILABLE_ERROR

    # Files table inside the knowledge base.
    response = auth_client.post(
        "/model-hub/knowledge-base/files/", {"kb_id": str(kb.id)}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    (file_row,) = response.json()["result"]["table_data"]
    assert file_row["status"] == StatusType.FAILED.value
    assert file_row["error"] == KB_EMBEDDINGS_UNAVAILABLE_ERROR
    assert file_row["file_size"] == 14


@pytest.mark.django_db
def test_indexed_file_completes_the_knowledge_base(knowledge_base):
    kb, kb_file = knowledge_base

    _ingest_with_result(kb, kb_file, {"file_id": str(kb_file.id), "kb_id": str(kb.id)})

    kb.refresh_from_db()
    kb_file.refresh_from_db()
    assert kb.status == StatusType.COMPLETED.value
    assert kb.last_error is None
    assert kb_file.status == StatusType.COMPLETED.value
    assert "error" not in json.loads(kb_file.metadata)


@pytest.fixture
def file_with_default_metadata(knowledge_base):
    kb, _ = knowledge_base
    # Metadata left at the field default: a dict, not a JSON string.
    kb_file = Files.objects.create(
        name="notes.txt", uploaded_url="https://example.com/notes.txt"
    )
    kb.files.add(kb_file)
    return kb, kb_file


def _remove_with_result(kb, kb_file, result):
    with patch("model_hub.tasks.develop_dataset.remove_from_kb", return_value=[result]):
        remove_kb_files._original_func(
            [str(kb_file.id)], str(kb.organization_id), str(kb.id)
        )
    kb_file.refresh_from_db()


@pytest.mark.django_db
def test_file_that_could_not_be_removed_keeps_its_error(file_with_default_metadata):
    kb, kb_file = file_with_default_metadata

    _remove_with_result(
        kb,
        kb_file,
        {
            "file_id": str(kb_file.id),
            "status": StatusType.FAILED.value,
            "error": "chunks could not be deleted",
        },
    )

    assert kb_file.status == StatusType.FAILED.value
    assert kb_file.deleted is False
    assert json.loads(kb_file.metadata) == {"error": "chunks could not be deleted"}


@pytest.mark.django_db
def test_removed_file_is_marked_deleted(file_with_default_metadata):
    kb, kb_file = file_with_default_metadata

    _remove_with_result(
        kb,
        kb_file,
        {"file_id": str(kb_file.id), "status": StatusType.COMPLETED.value},
    )

    assert kb_file.status == StatusType.COMPLETED.value
    assert kb_file.deleted is True


def test_recording_an_error_leaves_the_given_metadata_unchanged():
    metadata = {"source": "upload"}

    stored = _file_metadata_with_error(metadata, "x" * 10001)

    assert metadata == {"source": "upload"}
    assert json.loads(stored) == {"source": "upload", "error": "x" * 10000}
